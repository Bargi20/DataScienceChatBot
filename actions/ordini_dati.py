# Accesso agli ordini di esempio (dataset/ordini.csv), sullo stile di catalogo.py.
#
# Le date nel file sono relative a oggi (giorni_fa, consegna_tra_giorni) così non invecchiano.
# consegna_tra_giorni è negativo se la consegna prevista (o avvenuta) è nel passato.

import csv
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Text, Tuple

from actions.catalogo import gioco_per_id

PERCORSO_ORDINI = Path(__file__).resolve().parent.parent / "dataset" / "ordini.csv"

STATI_ANNULLABILI = ("in attesa di pagamento", "in preparazione")
STATI_PRE_SPEDIZIONE = STATI_ANNULLABILI

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MESI = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]

RE_NUMERO = re.compile(r"(?<!\d)(\d{6})(?!\d)")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _intero(valore: Text) -> Optional[int]:
    valore = (valore or "").strip()
    return int(float(valore)) if valore else None


def _converti(riga: Dict[Text, Text]) -> Dict[Text, Any]:
    riga = {k: (v or "").strip() for k, v in riga.items()}
    articoli: List[Tuple[int, int]] = []
    for voce in riga["articoli"].split("|"):
        if ":" in voce:
            id_gioco, quantita = voce.split(":", 1)
            articoli.append((int(id_gioco), int(quantita)))
    return {
        "numero": riga["numero"],
        "email": riga["email"].lower(),
        "stato": riga["stato"].lower(),
        "spedizione": riga["spedizione"].lower(),
        "corriere": riga["corriere"],
        "tracking": riga["tracking"],
        "articoli": articoli,
        "totale": float(riga["totale"].replace(",", ".")) if riga["totale"] else 0.0,
        "citta": riga["citta"],
        "giorni_fa": _intero(riga["giorni_fa"]) or 0,
        "consegna_tra_giorni": _intero(riga["consegna_tra_giorni"]),
    }


def carica_ordini() -> List[Dict[Text, Any]]:
    # Il file è piccolo: lo rileggo ogni volta così le modifiche si vedono subito.
    with open(PERCORSO_ORDINI, encoding="utf-8", newline="") as file:
        return [_converti(riga) for riga in csv.DictReader(file)]


def estrai_numero_ordine(testo: Optional[Text]) -> Optional[Text]:
    """Le sei cifre del numero d'ordine in «#482913», «482913», «l'ordine 482913»."""
    trovato = RE_NUMERO.search((testo or "").replace(" ", " "))
    return trovato.group(1) if trovato else None


def estrai_email(testo: Optional[Text]) -> Optional[Text]:
    trovata = RE_EMAIL.search(testo or "")
    return trovata.group(0).lower() if trovata else None


def ordine_per_numero(numero: Optional[Text]) -> Optional[Dict[Text, Any]]:
    numero = estrai_numero_ordine(numero)
    return next((o for o in carica_ordini() if o["numero"] == numero), None)


def email_coincide(ordine: Dict[Text, Any], email: Optional[Text]) -> bool:
    return bool(email) and ordine["email"] == (email or "").strip().lower()


def data_ordine(ordine: Dict[Text, Any], oggi: Optional[date] = None) -> date:
    return (oggi or date.today()) - timedelta(days=ordine["giorni_fa"])


def consegna_prevista(ordine: Dict[Text, Any], oggi: Optional[date] = None) -> Optional[date]:
    if ordine["consegna_tra_giorni"] is None:
        return None
    return (oggi or date.today()) + timedelta(days=ordine["consegna_tra_giorni"])


def formatta_data(giorno: date) -> Text:
    return f"{GIORNI[giorno.weekday()]} {giorno.day} {MESI[giorno.month - 1]}"


def in_ritardo(ordine: Dict[Text, Any], oggi: Optional[date] = None) -> bool:
    """Vero se la consegna prevista è passata e l'ordine non è ancora arrivato."""
    if ordine["stato"] not in ("in preparazione", "spedito", "in consegna"):
        return False
    prevista = consegna_prevista(ordine, oggi)
    return prevista is not None and prevista < (oggi or date.today())


def righe_articoli(ordine: Dict[Text, Any]) -> List[Text]:
    """Una riga per articolo, con il nome preso dal catalogo."""
    righe = []
    for id_gioco, quantita in ordine["articoli"]:
        gioco = gioco_per_id(id_gioco)
        nome = gioco["nome"] if gioco else f"Articolo n. {id_gioco}"
        righe.append(f"• {quantita} x {nome}")
    return righe
