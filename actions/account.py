import difflib
import re
from typing import Any, Dict, List, Optional, Text

from rasa_sdk import Action, FormValidationAction, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.types import DomainDict

from actions import catalogo

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def estrai_email(testo: Any) -> Optional[Text]:
    """La prima email trovata nel testo (in minuscolo), o None."""
    if not isinstance(testo, str):
        return None
    trovata = EMAIL_RE.search(testo.strip())
    return trovata.group(0).lower() if trovata else None


def giochi_simili(nome: Text, massimo: int = 3) -> List[Text]:
    """Nomi di giochi base del catalogo che somigliano a quanto scritto."""
    cercato = catalogo.normalizza(nome)
    if not cercato:
        return []
    parole = [p for p in cercato.split() if len(p) >= 3]
    punteggi = []
    for g in catalogo.carica_catalogo():
        if g["espansione_di"]:
            continue
        n = catalogo.normalizza(g["nome"])
        punteggio = difflib.SequenceMatcher(None, cercato, n).ratio()
        if any(p in n.split() or n.startswith(p) for p in parole):
            punteggio += 0.3
        if punteggio >= 0.45:
            punteggi.append((punteggio, g["nome"]))
    punteggi.sort(key=lambda x: -x[0])
    return [n for _, n in punteggi[:massimo]]


def _valida_email(dispatcher: CollectingDispatcher, valore: Any) -> Dict[Text, Any]:
    email = estrai_email(valore)
    if not email:
        dispatcher.utter_message(response="utter_account_email_non_valida")
        return {"account_email": None}
    return {"account_email": email}


class ValidateNewsletterForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_newsletter_form"

    def validate_account_email(
        self, slot_value: Any, dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: DomainDict,
    ) -> Dict[Text, Any]:
        return _valida_email(dispatcher, slot_value)


class ActionIscriviNewsletter(Action):
    def name(self) -> Text:
        return "action_iscrivi_newsletter"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        email = tracker.get_slot("account_email")
        dispatcher.utter_message(
            text=(f"Fatto! Ho iscritto {email} alla newsletter di GIGI Games 🎉\n"
                  "Ti arriveranno novità e offerte, e puoi disiscriverti quando vuoi dal link "
                  "in fondo a ogni email.\n"
                  "Ecco il tuo regalo di benvenuto: usa il codice BENVENUTO10 per avere il 10% "
                  "di sconto sul primo ordine.")
        )
        return []


class ActionPreparaAvviso(Action):
    """Prima del form: prende il gioco appena citato o l'ultimo di cui si è parlato."""

    def name(self) -> Text:
        return "action_prepara_avviso"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        return [SlotSet("avviso_gioco", tracker.get_slot("gioco"))]


class ValidateAvvisoDisponibilitaForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_avviso_disponibilita_form"

    async def required_slots(
        self, domain_slots: List[Text], dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: DomainDict,
    ) -> List[Text]:
        # Se il gioco è disponibile non serve l'email: lo diciamo subito.
        gioco = catalogo.trova_gioco(tracker.get_slot("avviso_gioco"))
        if gioco and catalogo.disponibile(gioco):
            return ["avviso_gioco"]
        return ["avviso_gioco", "account_email"]

    def validate_avviso_gioco(
        self, slot_value: Any, dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: DomainDict,
    ) -> Dict[Text, Any]:
        gioco = catalogo.trova_gioco(slot_value if isinstance(slot_value, str) else None)
        if gioco:
            return {"avviso_gioco": gioco["nome"], "gioco": gioco["nome"]}
        simili = giochi_simili(slot_value) if isinstance(slot_value, str) else []
        if simili:
            elenco = "\n".join(f"• {n}" for n in simili)
            dispatcher.utter_message(
                text=f"Non trovo «{slot_value}» nel catalogo. Intendevi uno di questi?\n{elenco}\n"
                     "Scrivimi il nome esatto del gioco.")
        else:
            dispatcher.utter_message(
                text=f"Non trovo «{slot_value}» nel catalogo. Prova a scrivere il nome come "
                     "appare sulla scatola.")
        return {"avviso_gioco": None}

    def validate_account_email(
        self, slot_value: Any, dispatcher: CollectingDispatcher,
        tracker: Tracker, domain: DomainDict,
    ) -> Dict[Text, Any]:
        return _valida_email(dispatcher, slot_value)


class ActionAvvisoDisponibilita(Action):
    def name(self) -> Text:
        return "action_avviso_disponibilita"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        gioco = catalogo.trova_gioco(tracker.get_slot("avviso_gioco"))
        if not gioco:
            dispatcher.utter_message(text="Non sono riuscito a trovare il gioco, riproviamo?")
            return [SlotSet("avviso_gioco", None)]
        if catalogo.disponibile(gioco):
            dispatcher.utter_message(
                text=f"Buone notizie: {gioco['nome']} è disponibile adesso! 🎉\n"
                     f"{catalogo.descrivi_breve(gioco)}\n"
                     "Lo trovi su www.gigigames.it, non serve nessun avviso.")
        else:
            email = tracker.get_slot("account_email")
            # Simulazione: l'avviso non viene salvato da nessuna parte.
            dispatcher.utter_message(
                text=f"Perfetto! Appena {gioco['nome']} torna disponibile ti scriviamo a {email} 📬")
        return [SlotSet("avviso_gioco", None), SlotSet("gioco", gioco["nome"])]
