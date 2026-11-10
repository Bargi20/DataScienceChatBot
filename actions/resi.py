import csv
import os
import re
import time
import zlib
from typing import Any, Dict, List, Optional, Text

from rasa_sdk import Action, FormValidationAction, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.types import DomainDict

SLOT_RESI = ["resi_numero_ordine", "resi_tipo_problema", "resi_descrizione"]

PERCORSO_ORDINI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dataset", "ordini.csv"
)

TIPI_VALIDI = ["danneggiato", "pezzi", "sbagliato", "altro"]

NOMI_TIPO = {
    "danneggiato": "prodotto arrivato danneggiato",
    "pezzi": "pezzi mancanti o difettosi",
    "sbagliato": "prodotto sbagliato",
    "altro": "altro problema",
}

PASSI_TIPO = {
    "danneggiato": (
        "Passi successivi:\n"
        "• invia entro 7 giorni dalla consegna una foto della scatola e del danno a assistenza@gigigames.it, "
        "indicando il numero di pratica\n"
        "• conserva imballo e scatola fino alla nostra risposta\n"
        "• appena verificata la foto, ti inviamo la sostituzione o ti rimborsiamo; il reso è gratuito."
    ),
    "pezzi": (
        "Passi successivi:\n"
        "• invia una foto dei pezzi mancanti o difettosi a assistenza@gigigames.it, indicando il numero di pratica\n"
        "• richiediamo il ricambio all'editore e te lo spediamo, oppure sostituiamo il gioco\n"
        "• se serve restituire qualcosa, l'etichetta è gratuita."
    ),
    "sbagliato": (
        "Passi successivi:\n"
        "• ti inviamo gratuitamente il gioco giusto, l'errore è nostro\n"
        "• ti mandiamo l'etichetta prepagata gratuita per restituire quello sbagliato: "
        "conservalo nella sua scatola, senza aprirlo se possibile\n"
        "• puoi anche rispondere all'email di assistenza con una foto dell'etichetta del pacco ricevuto."
    ),
    "altro": (
        "Passi successivi:\n"
        "• un operatore esamina la tua descrizione e ti risponde entro 24 ore lavorative\n"
        "• se puoi, invia foto o dettagli a assistenza@gigigames.it indicando il numero di pratica."
    ),
}


def _cifre_ordine(testo: Text) -> Optional[Text]:
    # Cerca un numero di esattamente 6 cifre, con o senza «#»
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", testo or "")
    return m.group(1) if m else None


def _ordine_esiste(numero: Text) -> Optional[bool]:
    """True/False se il numero è nel file ordini; None se non si può verificare."""
    try:
        if not os.path.isfile(PERCORSO_ORDINI):
            return None
        with open(PERCORSO_ORDINI, newline="", encoding="utf-8-sig") as f:
            campione = f.read(2048)
            f.seek(0)
            try:
                dialetto = csv.Sniffer().sniff(campione, delimiters=",;\t")
            except csv.Error:
                dialetto = csv.excel
            righe = list(csv.DictReader(f, dialect=dialetto))
        if not righe:
            return None
        colonna = None
        for nome in righe[0].keys():
            if nome and any(k in nome.lower() for k in ("ordine", "order", "numero")):
                colonna = nome
                break
        if colonna is None:
            return None
        for r in righe:
            valore = re.sub(r"\D", "", r.get(colonna) or "")
            if valore == numero:
                return True
        return False
    except Exception:
        return None


def riconosci_tipo(testo: Text) -> Optional[Text]:
    t = (testo or "").lower()
    if any(k in t for k in ("mancan", "difett", "pezz", "incomplet", "carte")):
        return "pezzi"
    if any(k in t for k in ("sbagliat", "errat", "divers", "non ordinat", "non quello")):
        return "sbagliato"
    if any(k in t for k in ("danneggi", "rott", "ammacc", "schiacc", "strappat", "rovinat", "trasporto")):
        return "danneggiato"
    if "altro" in t:
        return "altro"
    return None


class ValidateSegnalazioneForm(FormValidationAction):

    def name(self) -> Text:
        return "validate_segnalazione_form"

    def validate_resi_numero_ordine(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                    tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        numero = _cifre_ordine(str(slot_value or ""))
        if numero is None:
            dispatcher.utter_message(
                text="Il numero d'ordine deve essere di 6 cifre, per esempio #482913. Riprova pure."
            )
            return {"resi_numero_ordine": None}
        if _ordine_esiste(numero) is False:
            dispatcher.utter_message(
                text=f"Non trovo nessun ordine con il numero {numero}. Controlla l'email di conferma e riprova."
            )
            return {"resi_numero_ordine": None}
        return {"resi_numero_ordine": numero}

    def validate_resi_tipo_problema(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                    tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        tipo = riconosci_tipo(str(slot_value or ""))
        if tipo is None:
            dispatcher.utter_message(
                text="Non ho capito il tipo di problema. Scegli una delle opzioni qui sotto."
            )
            return {"resi_tipo_problema": None}
        return {"resi_tipo_problema": tipo}

    def validate_resi_descrizione(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                  tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        testo = str(slot_value or "").strip()
        if not testo:
            dispatcher.utter_message(text="Scrivi qualche parola su cosa è successo, oppure «nessuna».")
            return {"resi_descrizione": None}
        return {"resi_descrizione": testo[:500]}


class ActionResiCreaPratica(Action):

    def name(self) -> Text:
        return "action_resi_crea_pratica"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        numero = tracker.get_slot("resi_numero_ordine") or ""
        tipo = tracker.get_slot("resi_tipo_problema")
        if tipo not in TIPI_VALIDI:
            tipo = "altro"

        # Numero di pratica R-xxxxx ricavato da ordine, tipo e momento della richiesta
        codice = zlib.crc32("{}|{}|{}".format(numero, tipo, time.time()).encode()) % 100000
        pratica = "R-{:05d}".format(codice)

        messaggio = (
            "Ho aperto la segnalazione. ✅\n"
            "• Pratica: {}\n"
            "• Ordine: #{}\n"
            "• Problema: {}\n\n"
            "{}\n\n"
            "Per qualsiasi dubbio: assistenza@gigigames.it o 071 123 4567 (lun-ven 9:00-18:00)."
        ).format(pratica, numero, NOMI_TIPO[tipo], PASSI_TIPO[tipo])
        dispatcher.utter_message(text=messaggio)

        return [SlotSet(s, None) for s in SLOT_RESI]


class ActionResiAnnullaSegnalazione(Action):

    def name(self) -> Text:
        return "action_resi_annulla_segnalazione"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        dispatcher.utter_message(
            text="Va bene, ho annullato la segnalazione. Se ti serve, scrivimi di nuovo «voglio segnalare un problema con il mio ordine»."
        )
        return [SlotSet(s, None) for s in SLOT_RESI]
