# Azioni dell'area ordini: stato, annullamento, modifica e ritardo di un ordine già fatto.
# Il form chiede numero d'ordine ed email: i dettagli si mostrano solo se coincidono.

from typing import Any, Dict, List, Optional, Text

from rasa_sdk import Action, FormValidationAction, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.types import DomainDict

from actions.catalogo import formatta_prezzo
from actions.ordini_dati import (
    STATI_ANNULLABILI,
    STATI_PRE_SPEDIZIONE,
    consegna_prevista,
    data_ordine,
    email_coincide,
    estrai_email,
    estrai_numero_ordine,
    formatta_data,
    in_ritardo,
    ordine_per_numero,
    righe_articoli,
)

MAX_TENTATIVI = 3

SCOPO_PER_INTENT = {
    "traccia_ordine": "stato",
    "annulla_ordine": "annulla",
    "modifica_ordine": "modifica",
    "ordine_in_ritardo": "ritardo",
}

CONTATTI_ASSISTENZA = (
    "Assistenza (lun-ven 9:00-18:00):\n"
    "• assistenza@gigigames.it (risposta entro 24 ore lavorative)\n"
    "• telefono e WhatsApp 071 123 4567"
)

RESET_FORM = [
    SlotSet("ordini_numero", None),
    SlotSet("ordini_email", None),
    SlotSet("ordini_tentativi", None),
]


def _entita(tracker: Tracker, nome: Text) -> Optional[Text]:
    for entita in tracker.latest_message.get("entities", []):
        if entita.get("entity") == nome:
            return entita.get("value")
    return None


class ActionOrdiniImpostaScopo(Action):
    """Memorizza cosa vuole fare l'utente (stato, annulla, ...) e riparte da zero con il form."""

    def name(self) -> Text:
        return "action_ordini_imposta_scopo"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        intent = tracker.latest_message.get("intent", {}).get("name")
        scopo = SCOPO_PER_INTENT.get(intent, "stato")
        # Se il numero o l'email sono già nel messaggio li tengo, altrimenti il form li chiede.
        numero = estrai_numero_ordine(_entita(tracker, "numero_ordine"))
        email = estrai_email(_entita(tracker, "email"))
        return [
            SlotSet("ordini_scopo", scopo),
            SlotSet("ordini_numero", numero),
            SlotSet("ordini_email", email),
            SlotSet("ordini_tentativi", None),
            SlotSet("ordini_attesa_conferma", False),
        ]


class ValidateOrdineForm(FormValidationAction):

    def name(self) -> Text:
        return "validate_ordine_form"

    async def required_slots(self, domain_slots: List[Text], dispatcher: CollectingDispatcher,
                             tracker: Tracker, domain: DomainDict) -> List[Text]:
        # Dopo tre tentativi falliti il form si chiude da solo e l'azione finale rimanda all'assistenza.
        if (tracker.get_slot("ordini_tentativi") or 0) >= MAX_TENTATIVI:
            return []
        return ["ordini_numero", "ordini_email"]

    @staticmethod
    def _fallimento(tracker: Tracker, slot: Text) -> Dict[Text, Any]:
        tentativi = int(tracker.get_slot("ordini_tentativi") or 0) + 1
        return {slot: None, "ordini_tentativi": float(tentativi)}

    def validate_ordini_numero(self, valore: Any, dispatcher: CollectingDispatcher,
                               tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        if valore is None:
            # slot appena azzerato da action_ordini_imposta_scopo: non è un tentativo
            return {"ordini_numero": None}
        numero = estrai_numero_ordine(str(valore))
        if numero is None:
            dispatcher.utter_message(
                text="Il numero d'ordine è formato da 6 cifre, per esempio 482913. Puoi ricontrollarlo?")
            return self._fallimento(tracker, "ordini_numero")
        if ordine_per_numero(numero) is None:
            dispatcher.utter_message(
                text=f"Non trovo nessun ordine con il numero {numero}. "
                     "Ricontrolla il numero: lo trovi nell'email di conferma.")
            return self._fallimento(tracker, "ordini_numero")
        return {"ordini_numero": numero}

    def validate_ordini_email(self, valore: Any, dispatcher: CollectingDispatcher,
                              tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        if valore is None:
            return {"ordini_email": None}
        email = estrai_email(str(valore))
        if email is None:
            dispatcher.utter_message(text="Non mi sembra un indirizzo email valido. Riprova, per favore.")
            return self._fallimento(tracker, "ordini_email")
        ordine = ordine_per_numero(tracker.get_slot("ordini_numero"))
        if ordine is None or not email_coincide(ordine, email):
            dispatcher.utter_message(
                text="Questa email non coincide con quella usata per l'ordine. "
                     "Riprova con l'indirizzo che hai inserito al momento dell'acquisto.")
            return self._fallimento(tracker, "ordini_email")
        return {"ordini_email": email}


def _dettagli(ordine: Dict[Text, Any]) -> Text:
    righe = [f"Ordine {ordine['numero']}", f"Stato: {ordine['stato']}"]
    righe.append("Articoli:\n" + "\n".join(righe_articoli(ordine)))
    righe.append(f"Totale: {formatta_prezzo(ordine['totale'])}")
    righe.append(f"Ordinato il {formatta_data(data_ordine(ordine))}")
    return "\n".join(righe)


def _consegna_e_tracciamento(ordine: Dict[Text, Any]) -> List[Text]:
    righe = []
    stato = ordine["stato"]
    prevista = consegna_prevista(ordine)
    if stato == "consegnato" and prevista:
        righe.append(f"Consegnato il {formatta_data(prevista)}")
    elif stato in ("spedito", "in consegna", "in preparazione") and prevista:
        etichetta = "Ritiro in negozio previsto" if ordine["spedizione"] == "ritiro in negozio" \
            else "Consegna prevista"
        righe.append(f"{etichetta}: {formatta_data(prevista)}")
    elif stato == "in attesa di pagamento":
        righe.append("Consegna: da definire, la calcoliamo dopo il pagamento")
    if ordine["spedizione"]:
        righe.append(f"Spedizione: {ordine['spedizione']}")
    if ordine["corriere"] and stato in ("spedito", "in consegna", "consegnato", "reso in corso"):
        righe.append(f"Corriere: {ordine['corriere']}, tracciamento {ordine['tracking']}")
    return righe


def _risposta_stato(dispatcher: CollectingDispatcher, ordine: Dict[Text, Any]) -> None:
    testo = _dettagli(ordine) + "\n" + "\n".join(_consegna_e_tracciamento(ordine))
    if ordine["stato"] == "in attesa di pagamento":
        testo += "\n\nL'ordine parte appena riceviamo il pagamento (per il bonifico 1-2 giorni dall'accredito)."
    dispatcher.utter_message(text=testo)


def _risposta_annulla(dispatcher: CollectingDispatcher, ordine: Dict[Text, Any]) -> List[Dict[Text, Any]]:
    stato = ordine["stato"]
    if stato in STATI_ANNULLABILI:
        dispatcher.utter_message(
            text=f"L'ordine {ordine['numero']} ({stato}) si può ancora annullare.\n"
                 f"{chr(10).join(righe_articoli(ordine))}\nTotale {formatta_prezzo(ordine['totale'])}\n\n"
                 "Vuoi annullarlo davvero?",
            buttons=[{"title": "Sì, annulla", "payload": "/affirm"},
                     {"title": "No, lascialo", "payload": "/deny"}])
        return [SlotSet("ordini_attesa_conferma", True)]
    if stato == "annullato":
        dispatcher.utter_message(text=f"L'ordine {ordine['numero']} risulta già annullato.")
    elif stato == "consegnato":
        dispatcher.utter_message(
            text=f"L'ordine {ordine['numero']} è già stato consegnato, quindi non si può annullare. "
                 "Hai 14 giorni dalla consegna per fare il reso: dall'area account, in I miei ordini, "
                 "scegli Richiedi reso.")
    elif stato == "reso in corso":
        dispatcher.utter_message(text=f"Per l'ordine {ordine['numero']} c'è già un reso in corso, "
                                      "quindi non c'è nulla da annullare.")
    else:
        dispatcher.utter_message(
            text=f"L'ordine {ordine['numero']} è già partito ({stato}), quindi non posso annullarlo.\n"
                 "Hai due strade:\n"
                 "• rifiutare il pacco alla consegna\n"
                 "• fare il reso entro 14 giorni dalla consegna (area account, I miei ordini, Richiedi reso)")
    return []


def _risposta_modifica(dispatcher: CollectingDispatcher, ordine: Dict[Text, Any]) -> None:
    if ordine["stato"] in STATI_PRE_SPEDIZIONE:
        dispatcher.utter_message(
            text=f"L'ordine {ordine['numero']} non è ancora partito, quindi indirizzo e contenuto "
                 "si possono ancora modificare. Scrivi subito all'assistenza indicando il numero d'ordine "
                 f"e cosa vuoi cambiare.\n\n{CONTATTI_ASSISTENZA}")
    elif ordine["stato"] == "annullato":
        dispatcher.utter_message(text=f"L'ordine {ordine['numero']} è annullato, non si può modificare.")
    else:
        dispatcher.utter_message(
            text=f"L'ordine {ordine['numero']} è già {ordine['stato']}: dopo la spedizione non si può "
                 "cambiare né l'indirizzo né il contenuto. Se serve, puoi rifiutare il pacco o fare "
                 "il reso entro 14 giorni dalla consegna.")


def _risposta_ritardo(dispatcher: CollectingDispatcher, ordine: Dict[Text, Any]) -> None:
    stato = ordine["stato"]
    prevista = consegna_prevista(ordine)
    if stato == "in attesa di pagamento":
        dispatcher.utter_message(
            text=f"L'ordine {ordine['numero']} non è ancora partito perché risulta in attesa di pagamento. "
                 "Appena arriva il pagamento lo prepariamo (con il bonifico servono 1-2 giorni dall'accredito).")
    elif stato == "annullato":
        dispatcher.utter_message(text=f"L'ordine {ordine['numero']} è stato annullato, per questo non arriverà.")
    elif stato == "reso in corso":
        dispatcher.utter_message(text=f"Per l'ordine {ordine['numero']} c'è un reso in corso, quindi non è "
                                      "previsto nessun nuovo arrivo.")
    elif stato == "consegnato":
        quando = f" il {formatta_data(prevista)}" if prevista else ""
        dispatcher.utter_message(
            text=f"Il corriere ({ordine['corriere']}) segna l'ordine {ordine['numero']} come consegnato{quando}.\n"
                 "Se non l'hai ricevuto controlla con vicini o portineria e l'avviso di giacenza (il pacco "
                 f"resta in giacenza 7 giorni). Se non lo trovi, scrivici.\n\n{CONTATTI_ASSISTENZA}")
    elif in_ritardo(ordine):
        dispatcher.utter_message(
            text=f"Hai ragione, l'ordine {ordine['numero']} è in ritardo: la consegna era prevista per "
                 f"{formatta_data(prevista)} e il suo stato è «{stato}». Mi dispiace per il disagio.\n"
                 + (f"Corriere {ordine['corriere']}, tracciamento {ordine['tracking']}.\n" if ordine["tracking"] else "")
                 + f"Contatta l'assistenza così lo verifichiamo subito.\n\n{CONTATTI_ASSISTENZA}")
    else:
        dispatcher.utter_message(
            text=f"L'ordine {ordine['numero']} è in orario: stato «{stato}», "
                 f"consegna prevista {formatta_data(prevista)}.\n"
                 + (f"Corriere {ordine['corriere']}, tracciamento {ordine['tracking']}.\n" if ordine["tracking"] else "")
                 + "Se non arriva entro quella data, scrivimi di nuovo e ti metto in contatto con l'assistenza.")


class ActionOrdiniEsegui(Action):
    """Alla fine del form: se numero ed email coincidono risponde in base allo scopo."""

    def name(self) -> Text:
        return "action_ordini_esegui"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        ordine = ordine_per_numero(tracker.get_slot("ordini_numero"))
        verificato = ordine is not None and email_coincide(ordine, tracker.get_slot("ordini_email"))
        if not verificato:
            motivo = "dopo tre tentativi" if (tracker.get_slot("ordini_tentativi") or 0) >= MAX_TENTATIVI else "ora"
            dispatcher.utter_message(
                text=f"Non riesco a verificare l'ordine {motivo}. Per sicurezza non posso mostrarti "
                     f"i dettagli: ti conviene contattare l'assistenza.\n\n{CONTATTI_ASSISTENZA}")
            return RESET_FORM

        scopo = tracker.get_slot("ordini_scopo") or "stato"
        eventi: List[Dict[Text, Any]] = []
        if scopo == "annulla":
            eventi = _risposta_annulla(dispatcher, ordine)
        elif scopo == "modifica":
            _risposta_modifica(dispatcher, ordine)
        elif scopo == "ritardo":
            _risposta_ritardo(dispatcher, ordine)
        else:
            _risposta_stato(dispatcher, ordine)

        # Se aspetto una conferma tengo numero ed email, altrimenti riparto da zero.
        return eventi if eventi else RESET_FORM


class ActionOrdiniConfermaAnnullamento(Action):

    def name(self) -> Text:
        return "action_ordini_conferma_annullamento"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        if not tracker.get_slot("ordini_attesa_conferma"):
            # Nessuna domanda di annullamento in sospeso: non si annulla niente
            dispatcher.utter_message(
                text="Non c'è nessun annullamento in sospeso. Se vuoi annullare un ordine, "
                     "scrivimi «voglio annullare il mio ordine».")
            return [SlotSet("ordini_attesa_conferma", False)]
        ordine = ordine_per_numero(tracker.get_slot("ordini_numero"))
        if ordine is None or ordine["stato"] not in STATI_ANNULLABILI:
            dispatcher.utter_message(text="Non riesco a completare l'annullamento: scrivi all'assistenza.\n\n"
                                          + CONTATTI_ASSISTENZA)
        elif ordine["stato"] == "in attesa di pagamento":
            dispatcher.utter_message(
                text=f"Fatto, ho registrato l'annullamento dell'ordine {ordine['numero']}. "
                     "Non avendo ancora ricevuto il pagamento, non c'è nulla da rimborsare.")
        else:
            dispatcher.utter_message(
                text=f"Fatto, ho registrato l'annullamento dell'ordine {ordine['numero']}. "
                     "Riceverai una email di conferma e il rimborso arriverà con lo stesso metodo di "
                     "pagamento. Se hai problemi, l'assistenza è qui per te.")
        return RESET_FORM + [SlotSet("ordini_attesa_conferma", False)]


class ActionOrdiniRifiutaAnnullamento(Action):

    def name(self) -> Text:
        return "action_ordini_rifiuta_annullamento"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        dispatcher.utter_message(text="Va bene, non annullo niente: l'ordine resta com'è.")
        return RESET_FORM + [SlotSet("ordini_attesa_conferma", False)]


class ActionOrdiniReset(Action):
    """Esce dal form quando l'utente lascia perdere."""

    def name(self) -> Text:
        return "action_ordini_reset"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        dispatcher.utter_message(text="Nessun problema, lasciamo stare. Se ti serve ancora, sono qui.")
        return RESET_FORM + [SlotSet("ordini_attesa_conferma", False)]
