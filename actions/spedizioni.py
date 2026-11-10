# Calcolo del costo di spedizione in base a importo e destinazione.

import re
from typing import Any, Dict, List, Optional, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

from actions.catalogo import formatta_prezzo, gioco_per_id, normalizza, prezzo_attuale

COSTO_ITALIA = 4.90
SOGLIA_ITALIA = 49.0
COSTO_UE = 12.90
SOGLIA_UE = 120.0

ISOLE = ["sicilia", "sardegna", "calabria", "isole", "isola", "palermo", "catania", "messina",
         "cagliari", "sassari", "reggio calabria", "cosenza", "catanzaro", "siracusa", "olbia"]
UE = ["germania", "francia", "spagna", "austria", "olanda", "paesi bassi", "belgio", "portogallo",
      "irlanda", "grecia", "polonia", "svezia", "danimarca", "finlandia", "croazia", "slovenia",
      "lussemburgo", "malta", "cipro", "romania", "bulgaria", "ungheria", "repubblica ceca",
      "slovacchia", "estonia", "lettonia", "lituania", "europa", "unione europea", "ue", "berlino",
      "parigi", "madrid", "vienna", "amsterdam", "bruxelles", "lisbona", "barcellona"]
ESTERO = ["svizzera", "regno unito", "inghilterra", "londra", "stati uniti", "usa", "america",
          "canada", "australia", "norvegia", "giappone", "cina", "albania", "brasile", "estero"]


def _contiene(testo: Text, parole: List[Text]) -> bool:
    return any(re.search(r"\b%s\b" % re.escape(p), testo) for p in parole)


def classifica_destinazione(testo: Optional[Text]) -> Optional[Text]:
    """Restituisce 'italia', 'isole', 'ue', 'estero' oppure None se non riconosciuta."""
    t = normalizza(testo or "")
    if not t:
        return None
    # I paesi esteri vengono prima: «regno unito» non deve essere scambiato per altro
    if _contiene(t, ESTERO):
        return "estero"
    if _contiene(t, UE):
        return "ue"
    if _contiene(t, ISOLE):
        return "isole"
    if _contiene(t, ["italia", "milano", "roma", "torino", "napoli", "ancona", "bologna", "firenze",
                     "genova", "verona", "bari", "venezia", "padova", "trieste", "perugia"]):
        return "italia"
    return None


def estrai_importo(testo: Optional[Text]) -> Optional[float]:
    """Legge un importo come «35», «35 euro», «120,50»."""
    if not testo:
        return None
    m = re.search(r"\d+(?:[.,]\d{1,2})?", str(testo))
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def totale_carrello(carrello: Any) -> Optional[float]:
    if not carrello or not isinstance(carrello, list):
        return None
    totale = 0.0
    for riga in carrello:
        try:
            gioco = gioco_per_id(int(riga.get("id")))
            if gioco:
                totale += prezzo_attuale(gioco) * int(riga.get("quantita", 1))
        except (TypeError, ValueError, AttributeError):
            continue
    return totale if totale > 0 else None


def _entita(tracker: Tracker, nome: Text) -> Optional[Text]:
    for e in tracker.latest_message.get("entities", []):
        if e.get("entity") == nome and e.get("value"):
            return str(e["value"])
    return None


class ActionCalcolaSpedizione(Action):

    def name(self) -> Text:
        return "action_calcola_spedizione"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        testo = tracker.latest_message.get("text") or ""

        # Destinazione: dal messaggio, poi dal testo, poi da quella già detta
        dest = classifica_destinazione(_entita(tracker, "destinazione_spedizione"))
        if not dest:
            dest = classifica_destinazione(testo)
        if not dest:
            dest = classifica_destinazione(tracker.get_slot("destinazione_spedizione"))

        # Importo: dal messaggio, poi dal totale del carrello, poi da quello già detto
        importo = estrai_importo(_entita(tracker, "budget"))
        da_carrello = False
        if importo is None:
            importo = totale_carrello(tracker.get_slot("carrello"))
            da_carrello = importo is not None
        if importo is None:
            importo = estrai_importo(tracker.get_slot("budget"))

        if importo is None:
            dispatcher.utter_message(
                text="Per calcolare la spedizione dimmi l'importo dell'ordine e dove spedire, "
                     "per esempio «spedizione per un ordine da 35 euro in Sardegna». 📦"
            )
            return []

        valore = formatta_prezzo(importo)
        origine = "il tuo carrello da %s" % valore if da_carrello else "un ordine da %s" % valore

        if not dest:
            # Destinazione mancante: si danno le tariffe per l'Italia e si chiede il resto
            dest_testo = "Dove devo spedire? Italia, isole, Unione Europea?"
            if importo > SOGLIA_ITALIA:
                dispatcher.utter_message(
                    text="Per %s la spedizione in Italia è gratuita (sopra i 49 €). %s" % (origine, dest_testo))
            else:
                dispatcher.utter_message(
                    text="Per %s la spedizione in Italia costa 4,90 €. %s" % (origine, dest_testo))
            return []

        if dest == "estero":
            msg = "Purtroppo fuori dall'Unione Europea non spediamo. 🙁 In Italia e nell'UE nessun problema!"
        elif dest == "ue":
            if importo > SOGLIA_UE:
                msg = "Per %s la spedizione nell'Unione Europea è gratuita (sopra i 120 €). Consegna in 4-8 giorni lavorativi. 🇪🇺" % origine
            else:
                msg = ("Per %s la spedizione nell'Unione Europea costa 12,90 €, consegna in 4-8 giorni lavorativi. "
                       "Per averla gratis devi superare i 120 €: ti servono ancora oltre %s." % (origine, formatta_prezzo(SOGLIA_UE - importo)))
        else:
            tempi = "2-4 giorni lavorativi"
            if dest == "isole":
                tempi = "2-4 giorni lavorativi più 1-2 giorni per Sicilia, Sardegna e Calabria"
            if importo > SOGLIA_ITALIA:
                msg = "Per %s la spedizione standard è gratuita (sopra i 49 €), consegna in %s. 🎉" % (origine, tempi)
            elif importo == SOGLIA_ITALIA:
                msg = ("Per %s la spedizione standard costa 4,90 €; la gratuità scatta sopra i 49 €, "
                       "quindi basta aggiungere pochi centesimi. Consegna in %s." % (origine, tempi))
            else:
                msg = ("Per %s la spedizione standard costa 4,90 €, consegna in %s. "
                       "Per averla gratis devi superare i 49 €: ti servono ancora oltre %s."
                       % (origine, tempi, formatta_prezzo(SOGLIA_ITALIA - importo)))

        dispatcher.utter_message(text=msg)
        return []
