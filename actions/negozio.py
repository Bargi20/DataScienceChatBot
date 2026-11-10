import csv
import os
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Text, Tuple

from rasa_sdk import Action, FormValidationAction, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.types import DomainDict

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MESI = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]

# Orari del negozio fisico: per ogni giorno (0 = lunedì) le fasce di apertura.
ORARI_NEGOZIO: Dict[int, List[Tuple[time, time]]] = {
    0: [(time(15, 30), time(19, 30))],
    1: [(time(10, 0), time(13, 0)), (time(15, 30), time(19, 30))],
    2: [(time(10, 0), time(13, 0)), (time(15, 30), time(19, 30))],
    3: [(time(10, 0), time(13, 0)), (time(15, 30), time(19, 30))],
    4: [(time(10, 0), time(13, 0)), (time(15, 30), time(19, 30))],
    5: [(time(10, 0), time(13, 0)), (time(15, 30), time(19, 30))],
    6: [],
}

# Operatori dell'assistenza: dal lunedì al venerdì, 9:00-18:00.
ORARIO_OPERATORI = (time(9, 0), time(18, 0))
GIORNI_OPERATORI = range(0, 5)

CONTATTI = (
    "• Email: assistenza@gigigames.it (risposta entro 24 ore lavorative)\n"
    "• Telefono e WhatsApp: 071 123 4567"
)

PERCORSO_EVENTI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dataset", "eventi.csv"
)
MAX_EVENTI_SPECIALI = 4


def _ora(t: time) -> Text:
    return f"{t:%H:%M}"


def _data_estesa(d: date) -> Text:
    return f"{GIORNI[d.weekday()]} {d.day} {MESI[d.month - 1]}"


def stato_negozio(adesso: datetime) -> Tuple[bool, Optional[time], Optional[datetime]]:
    """Restituisce (aperto, orario di chiusura se aperto, prossima apertura se chiuso)."""
    fasce = ORARI_NEGOZIO[adesso.weekday()]
    ora = adesso.time()
    for inizio, fine in fasce:
        if inizio <= ora < fine:
            return True, fine, None
    # Prossima apertura: oggi più tardi oppure nei giorni seguenti
    for inizio, _ in fasce:
        if ora < inizio:
            return False, None, datetime.combine(adesso.date(), inizio)
    for i in range(1, 8):
        giorno = adesso.date() + timedelta(days=i)
        fasce_giorno = ORARI_NEGOZIO[giorno.weekday()]
        if fasce_giorno:
            return False, None, datetime.combine(giorno, fasce_giorno[0][0])
    return False, None, None


def _quando(prossima: datetime, adesso: datetime) -> Text:
    giorni = (prossima.date() - adesso.date()).days
    if giorni == 0:
        return f"oggi alle {_ora(prossima.time())}"
    if giorni == 1:
        return f"domani alle {_ora(prossima.time())}"
    return f"{_data_estesa(prossima.date())} alle {_ora(prossima.time())}"


def operatori_disponibili(adesso: datetime) -> bool:
    return (
        adesso.weekday() in GIORNI_OPERATORI
        and ORARIO_OPERATORI[0] <= adesso.time() < ORARIO_OPERATORI[1]
    )


def prossima_data_settimanale(oggi: date, giorno_settimana: int, incluso_oggi: bool = True) -> date:
    delta = (giorno_settimana - oggi.weekday()) % 7
    if delta == 0 and not incluso_oggi:
        delta = 7
    return oggi + timedelta(days=delta)


def carica_eventi(percorso: Text = PERCORSO_EVENTI) -> List[Dict[Text, Any]]:
    eventi = []
    try:
        with open(percorso, encoding="utf-8", newline="") as f:
            for riga in csv.DictReader(f):
                try:
                    h, m = riga["ora"].strip().split(":")
                    inizio = datetime.combine(
                        date.fromisoformat(riga["data"].strip()), time(int(h), int(m))
                    )
                except (ValueError, KeyError, AttributeError):
                    continue
                riga["inizio"] = inizio
                eventi.append(riga)
    except OSError:
        return []
    return sorted(eventi, key=lambda e: e["inizio"])


def eventi_futuri(adesso: datetime, limite: int = MAX_EVENTI_SPECIALI,
                  percorso: Text = PERCORSO_EVENTI) -> List[Dict[Text, Any]]:
    return [e for e in carica_eventi(percorso) if e["inizio"] >= adesso][:limite]


class ActionNegozioAperto(Action):

    def name(self) -> Text:
        return "action_negozio_aperto"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        adesso = datetime.now()
        aperto, chiusura, apertura = stato_negozio(adesso)
        if aperto:
            testo = f"Sì, il negozio di Via Roma 12 è aperto adesso! 🎲 Chiudiamo alle {_ora(chiusura)}."
        elif apertura is not None:
            testo = (
                "In questo momento il negozio fisico è chiuso. "
                f"Riapriamo {_quando(apertura, adesso)}."
            )
            if adesso.weekday() == 6:
                testo = "Oggi è domenica e il negozio fisico è chiuso. " + f"Riapriamo {_quando(apertura, adesso)}."
        else:
            testo = "In questo momento il negozio fisico è chiuso."
        testo += "\n\nL'e-shop su www.gigigames.it è sempre aperto, 24 ore su 24."
        dispatcher.utter_message(text=testo)
        return []


class ActionProssimiEventi(Action):

    def name(self) -> Text:
        return "action_prossimi_eventi"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        adesso = datetime.now()
        oggi = adesso.date()

        # Appuntamenti fissi: venerdì alle 21:00 e sabato pomeriggio
        venerdi = prossima_data_settimanale(oggi, 4)
        if venerdi == oggi and adesso.time() >= time(21, 0):
            venerdi += timedelta(days=7)
        sabato = prossima_data_settimanale(oggi, 5)
        if sabato == oggi and adesso.time() >= time(19, 30):
            sabato += timedelta(days=7)
        righe = [
            "Ecco cosa bolle in pentola da GIGI Games (Via Roma 12, Ancona).",
            "",
            "Appuntamenti fissi:",
            f"• {_data_estesa(venerdi)}, ore 21:00: serata di gioco libero (gratis)",
            f"• {_data_estesa(sabato)}, pomeriggio: tavolo demo per provare i giochi",
        ]

        speciali = eventi_futuri(adesso)
        if speciali:
            righe += ["", "Eventi speciali:"]
            for e in speciali:
                dettagli = [e.get("gioco", "").strip(), e.get("costo", "").strip()]
                posti = e.get("posti", "").strip()
                if posti:
                    dettagli.append(f"{posti} posti")
                dettagli = [d for d in dettagli if d]
                riga = f"• {_data_estesa(e['inizio'].date())}, ore {_ora(e['inizio'].time())}: {e['titolo']}"
                if dettagli:
                    riga += " (" + ", ".join(dettagli) + ")"
                righe.append(riga)
        else:
            righe += ["", "Al momento non ci sono eventi speciali in programma, ma resta sintonizzato!"]

        righe += ["", "Per iscriverti a un torneo scrivi a assistenza@gigigames.it o chiama il 071 123 4567."]
        dispatcher.utter_message(text="\n".join(righe))
        return []


class ActionParlaOperatore(Action):

    def name(self) -> Text:
        return "action_parla_operatore"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        adesso = datetime.now()
        intro = (
            "Capisco, preferisci parlare con una persona. Io sono un assistente virtuale e "
            "non posso trasferire la chat a un operatore, ma puoi contattarli direttamente."
        )
        if operatori_disponibili(adesso):
            stato = "Gli operatori sono disponibili adesso (lun–ven 9:00–18:00), quindi telefona o scrivi su WhatsApp."
        else:
            stato = (
                "Adesso gli operatori non sono in servizio: rispondono dal lunedì al venerdì, "
                "dalle 9:00 alle 18:00. Scrivi pure e ti risponderanno appena possibile."
            )
        dispatcher.utter_message(text=f"{intro}\n\n{stato}\n\n{CONTATTI}")
        return []


def interpreta_voto(testo: Any) -> Optional[int]:
    """Estrae un voto da 1 a 5 da un testo («4», «quattro», «5 stelle»)."""
    if testo is None:
        return None
    t = str(testo).lower()
    parole = {"uno": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5}
    trovati = re.findall(r"\d+", t)
    if len(trovati) == 1:
        n = int(trovati[0])
        return n if 1 <= n <= 5 else None
    if not trovati:
        for p, n in parole.items():
            if re.search(r"\b" + p + r"\b", t):
                return n
    return None


class ValidateFeedbackNegozioForm(FormValidationAction):

    def name(self) -> Text:
        return "validate_feedback_negozio_form"

    def validate_negozio_voto(self, slot_value: Any, dispatcher: CollectingDispatcher,
                              tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        voto = interpreta_voto(slot_value)
        if voto is None:
            dispatcher.utter_message(text="Mi serve un voto da 1 a 5: usa i bottoni oppure scrivi un numero.")
            return {"negozio_voto": None}
        return {"negozio_voto": str(voto)}

    def validate_negozio_commento(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                  tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        testo = str(slot_value or "").strip()
        if not testo or testo.lower() in ("-", "no", "niente", "nulla", "salta", "nessuno", "nessun commento", "/deny"):
            return {"negozio_commento": "-"}
        return {"negozio_commento": testo}


class ActionInviaFeedbackNegozio(Action):

    def name(self) -> Text:
        return "action_invia_feedback_negozio"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        voto = interpreta_voto(tracker.get_slot("negozio_voto")) or 0
        commento = tracker.get_slot("negozio_commento")
        if voto >= 4:
            testo = "Grazie di cuore per il voto! 🎉 Siamo felicissimi che ti sia trovato bene: il tuo parere ci dà la carica per continuare così."
        elif voto == 3:
            testo = "Grazie per il feedback! Ci sta un voto a metà: lavoreremo per migliorare e meritarci qualcosa in più la prossima volta."
        else:
            testo = (
                "Grazie per la sincerità, e scusaci se non siamo stati all'altezza. "
                "Prenderemo in carico il tuo feedback per migliorare. "
                "Se vuoi che qualcuno ti risponda, scrivi a assistenza@gigigames.it."
            )
        if commento and commento != "-":
            testo += "\nAbbiamo letto anche il tuo commento, grazie per averlo condiviso."
        dispatcher.utter_message(text=testo)
        return [SlotSet("negozio_voto", None), SlotSet("negozio_commento", None)]


class ActionAzzeraFeedbackNegozio(Action):

    def name(self) -> Text:
        return "action_azzera_feedback_negozio"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        return [SlotSet("negozio_voto", None), SlotSet("negozio_commento", None)]
