"""Converte il dataset del negozio (jumanji_giochi_dataset.csv) in dataset/catalogo.csv.

Il file del negozio ha le stesse colonne del catalogo, ma:
- le categorie sono quelle del sito («Party game», «Introduttivi e strategia», ...): le riportiamo
  ai nomi usati dal bot (actions/catalogo.py, CATEGORIE);
- le meccaniche vanno in minuscolo e con gli stessi nomi delle categorie del bot («Deck building» -> deckbuilding);
- l'editore sconosciuto è scritto «-»;
- valutazione e complessità sono sempre vuote: il bot non le usa, quindi non le copiamo.

Uso (dalla cartella del progetto):
    python dataset/importa_negozio.py ~/Downloads/jumanji_giochi_dataset.csv
    python dataset/genera_lookup.py
"""

import argparse
import csv
import sys
from pathlib import Path

PROGETTO = Path(__file__).resolve().parent.parent
USCITA = PROGETTO / "dataset" / "catalogo.csv"

COLONNE_USCITA = [
    "id", "nome", "editore", "autore", "anno", "categorie", "meccaniche", "giocatori_min", "giocatori_max",
    "durata_min", "eta_min", "prezzo", "prezzo_scontato", "disponibilita", "lingua", "espansione_di",
    "novita", "descrizione",
]

# Categoria del negozio -> categorie del bot. «Introduttivi» sono i giochi d'ingresso, quindi anche per famiglie.
MAPPA_CATEGORIE = {
    "introduttivi e strategia": ["famiglia", "strategia"],
    "gestionali": ["strategia", "gestionali"],
    "party game": ["party"],
    "giochi di carte": ["carte"],
    "cooperativi": ["cooperativo"],
    "solitario": ["solitario"],
    "per due": ["per due"],
    "investigativi": ["investigativo"],
    "bambini": ["bambini"],
    "avventura": ["avventura"],
    "guerra e tattica": ["guerra e tattica"],
    "abilità": ["abilità"],
    "classici": ["classici"],
}

MAPPA_MECCANICHE = {
    "deck building": ["deckbuilding"],
    "flip and write": ["roll and write"],
    "lancio di dadi": ["dadi"],
}


def _lista(valore, mappa, sconosciute=None):
    """Voci separate da «|», in minuscolo e tradotte con `mappa`; quelle non in mappa restano uguali."""
    risultato = []
    for voce in (v.strip().lower() for v in valore.split("|")):
        if not voce:
            continue
        if sconosciute is not None and voce not in mappa:
            sconosciute.add(voce)
        for nuova in mappa.get(voce, [voce]):
            if nuova not in risultato:
                risultato.append(nuova)
    return "|".join(risultato)


def converti(riga, sconosciute):
    nuova = {colonna: (riga.get(colonna) or "").strip() for colonna in COLONNE_USCITA}
    if nuova["editore"] == "-":
        nuova["editore"] = ""
    nuova["descrizione"] = nuova["descrizione"].lstrip(".,;: ")
    nuova["categorie"] = _lista(nuova["categorie"], MAPPA_CATEGORIE, sconosciute)
    nuova["meccaniche"] = _lista(nuova["meccaniche"], MAPPA_MECCANICHE)
    return nuova


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sorgente", type=Path, help="CSV esportato dal negozio")
    parser.add_argument("--uscita", type=Path, default=USCITA)
    args = parser.parse_args()

    with open(args.sorgente, encoding="utf-8", newline="") as file:
        righe = list(csv.DictReader(file))
    mancanti = set(COLONNE_USCITA) - set(righe[0]) if righe else set(COLONNE_USCITA)
    if mancanti:
        sys.exit(f"Nel file mancano le colonne: {', '.join(sorted(mancanti))}")

    sconosciute = set()
    convertite = [converti(r, sconosciute) for r in righe]
    with open(args.uscita, "w", encoding="utf-8", newline="") as file:
        scrittore = csv.DictWriter(file, fieldnames=COLONNE_USCITA)
        scrittore.writeheader()
        scrittore.writerows(convertite)
    print(f"Scritti {len(convertite)} giochi in {args.uscita}")
    if sconosciute:
        print("Categorie non previste (copiate così come sono, aggiungile a MAPPA_CATEGORIE):",
              ", ".join(sorted(sconosciute)))


if __name__ == "__main__":
    main()
