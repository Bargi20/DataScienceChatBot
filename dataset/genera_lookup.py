"""Rigenera data/catalogo/lookup_giochi.yml a partire dal catalogo.

La lookup table `gioco` contiene i nomi di tutti i prodotti del catalogo con le loro
varianti (senza accenti, senza punteggiatura, senza sottotitolo) e gli alias più comuni
definiti in actions/catalogo.py; la lookup `editore` contiene gli editori.

Uso (dalla cartella del progetto):
    python dataset/genera_lookup.py
    python dataset/genera_lookup.py --csv altro_catalogo.csv
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Text

PROGETTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGETTO))

from actions import catalogo  # noqa: E402

USCITA = PROGETTO / "data" / "catalogo" / "lookup_giochi.yml"

# Alias degli editori da riconoscere oltre ai nomi completi (quelli troppo generici restano fuori).
ALIAS_EDITORI_LOOKUP = ["asmodee", "cranio", "ghenos", "horrible games", "raven", "pendragon", "da vinci giochi"]
# Marchi da non riconoscere come editore (è il nome del negozio).
EDITORI_ESCLUSI = {"gigi games"}


def _valido(voce: Text) -> bool:
    # le espressioni della lookup sono cercate tra \b...\b: devono iniziare e finire con lettere o cifre
    return bool(re.match(r"^\w.*\w$|^\w$", voce)) and catalogo.normalizza(voce) not in catalogo.NOMI_AMBIGUI


def _ordina(voci: Iterable[Text]) -> List[Text]:
    unici = {}
    for voce in voci:
        voce = re.sub(r"\s+", " ", voce).strip()
        # sul telefono l'apostrofo diventa spesso ’
        for forma in {voce, voce.replace("'", "’")}:
            if _valido(forma):
                unici.setdefault(forma.lower(), forma)
    # le voci più lunghe prima: nella regex vince la prima alternativa che corrisponde
    return sorted(unici.values(), key=lambda v: (-len(v), v.lower()))


def nomi_giochi() -> List[Text]:
    voci = []
    nomi_catalogo = {catalogo.normalizza(g["nome"]) for g in catalogo.carica_catalogo()}
    for gioco in catalogo.carica_catalogo():
        voci += catalogo.varianti_nome(gioco)
    voci += [alias for alias, nome in catalogo.ALIAS_GIOCHI.items() if catalogo.normalizza(nome) in nomi_catalogo]
    return _ordina(voci)


def nomi_editori() -> List[Text]:
    tutti = [e for e in catalogo.editori(includi_accessori=True) if catalogo.normalizza(e) not in EDITORI_ESCLUSI]
    voci = list(tutti)
    for editore in tutti:
        voci.append(catalogo.normalizza(editore))
    voci += [a for a in ALIAS_EDITORI_LOOKUP if catalogo.trova_editore(a)]
    return _ordina(voci)


def scrivi(percorso: Path) -> None:
    righe = [
        "# File generato da dataset/genera_lookup.py a partire da dataset/catalogo.csv:",
        "# non modificarlo a mano, rilancia lo script quando cambia il catalogo.",
        'version: "3.1"',
        "",
        "nlu:",
    ]
    for nome, voci in (("gioco", nomi_giochi()), ("editore", nomi_editori())):
        righe += [f"- lookup: {nome}", "  examples: |"]
        righe += [f"    - {voce}" for voce in voci]
        righe.append("")
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text("\n".join(righe), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", help="catalogo da usare al posto di dataset/catalogo.csv")
    parser.add_argument("--output", default=str(USCITA), help="file YAML da scrivere")
    argomenti = parser.parse_args()
    if argomenti.csv:
        catalogo.PERCORSO_CATALOGO = Path(argomenti.csv).resolve()
        catalogo.ricarica_catalogo()
    scrivi(Path(argomenti.output))
    print(f"Scritte {len(nomi_giochi())} voci per `gioco` e {len(nomi_editori())} per `editore` in {argomenti.output}")


if __name__ == "__main__":
    main()
