# Accesso al catalogo dei giochi (dataset/catalogo.csv), condiviso da tutte le azioni.
#
# Per usare un altro dataset basta cambiare PERCORSO_CATALOGO e, se le colonne hanno
# nomi diversi, la mappa COLONNE (nome nel file -> nome usato dalle azioni).
# Dopo aver cambiato dataset rilancia `python dataset/genera_lookup.py`, che aggiorna
# la lookup table dei nomi dei giochi usata per riconoscerli nei messaggi.

import csv
import difflib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Text

PERCORSO_CATALOGO = Path(__file__).resolve().parent.parent / "dataset" / "catalogo.csv"

COLONNE = {
    "id": "id",
    "nome": "nome",
    "editore": "editore",
    "autore": "autore",
    "anno": "anno",
    "categorie": "categorie",
    "meccaniche": "meccaniche",
    "giocatori_min": "giocatori_min",
    "giocatori_max": "giocatori_max",
    "durata_min": "durata_min",
    "eta_min": "eta_min",
    "prezzo": "prezzo",
    "prezzo_scontato": "prezzo_scontato",
    "disponibilita": "disponibilita",
    "lingua": "lingua",
    "espansione_di": "espansione_di",
    "novita": "novita",
    "descrizione": "descrizione",
}

CAMPI_INTERI = ("id", "anno", "giocatori_min", "giocatori_max", "durata_min", "eta_min")
CAMPI_DECIMALI = ("prezzo", "prezzo_scontato")
CAMPI_LISTA = ("categorie", "meccaniche")

# Il negozio indica solo «disponibile» o «esaurito», non i pezzi in magazzino:
# un gioco disponibile si può ordinare fino a questa quantità.
SCORTA_DISPONIBILE = 99
DISPONIBILITA_TESTO = {"disponibile": SCORTA_DISPONIBILE, "esaurito": 0}

NUMERI_IN_LETTERE = {
    "un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
    "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11,
    "dodici": 12, "tredici": 13, "quattordici": 14, "quindici": 15, "sedici": 16,
    "diciotto": 18, "venti": 20, "trenta": 30, "quaranta": 40,
    "cinquanta": 50, "sessanta": 60, "novanta": 90, "cento": 100,
}

# Valori ammessi nella colonna `categorie`: sono anche i valori canonici dell'entità `categoria`.
CATEGORIE = [
    "strategia", "famiglia", "party", "cooperativo", "carte", "bambini", "astratto",
    "deckbuilding", "legacy", "solitario", "per due", "investigativo",
    "piazzamento lavoratori", "roll and write", "avventura", "bluff", "dadi", "accessori",
    "gestionali", "guerra e tattica", "abilita", "classici",
]
CATEGORIA_ACCESSORI = "accessori"

# Altri modi di chiamare le categorie (già normalizzati) -> categoria del catalogo.
SINONIMI_CATEGORIE = {
    "strategici": "strategia", "strategico": "strategia", "gestionale": "gestionali",
    "german": "gestionali", "eurogame": "gestionali", "euro": "gestionali",
    "introduttivi": "famiglia", "introduttivo": "famiglia",
    "guerra": "guerra e tattica", "di guerra": "guerra e tattica", "tattica": "guerra e tattica",
    "tattici": "guerra e tattica", "tattico": "guerra e tattica", "wargame": "guerra e tattica",
    "war game": "guerra e tattica", "guerra tattica": "guerra e tattica", "battaglie": "guerra e tattica",
    "abilita": "abilita", "destrezza": "abilita", "manualita": "abilita", "riflessi": "abilita",
    "classico": "classici", "tradizionali": "classici", "tradizionale": "classici",
    "famiglie": "famiglia", "family": "famiglia", "familiare": "famiglia", "familiari": "famiglia",
    "party game": "party", "partygame": "party", "feste": "party", "festa": "party",
    "compagnia": "party", "gruppo": "party", "gruppi": "party", "amici": "party",
    "cooperativi": "cooperativo", "collaborativo": "cooperativo", "collaborativi": "cooperativo",
    "coop": "cooperativo", "cooperazione": "cooperativo",
    "carta": "carte", "card game": "carte", "card": "carte",
    "bimbi": "bambini", "bambino": "bambini", "bambina": "bambini", "kids": "bambini",
    "piccoli": "bambini", "piu piccoli": "bambini", "ragazzi": "bambini",
    "astratti": "astratto",
    "deck building": "deckbuilding", "deck builder": "deckbuilding", "deckbuilder": "deckbuilding",
    "costruzione del mazzo": "deckbuilding", "costruzione mazzo": "deckbuilding",
    "campagna legacy": "legacy",
    "solo": "solitario", "da solo": "solitario", "solitaria": "solitario", "in solitaria": "solitario",
    "solitari": "solitario", "single player": "solitario", "un giocatore": "solitario",
    "due": "per due", "in due": "per due", "a due": "per due", "coppia": "per due",
    "in coppia": "per due", "coppie": "per due", "due giocatori": "per due", "duello": "per due",
    "investigativi": "investigativo", "giallo": "investigativo", "gialli": "investigativo",
    "deduzione": "investigativo", "detective": "investigativo", "indagine": "investigativo",
    "indagini": "investigativo", "investigazione": "investigativo", "mistero": "investigativo",
    "misteri": "investigativo", "escape room": "investigativo", "escape": "investigativo",
    "worker placement": "piazzamento lavoratori", "piazzamento": "piazzamento lavoratori",
    "piazzamento di lavoratori": "piazzamento lavoratori",
    "piazzamento dei lavoratori": "piazzamento lavoratori",
    "roll write": "roll and write", "roll e write": "roll and write", "roll n write": "roll and write",
    "roll and writes": "roll and write", "flip and write": "roll and write", "flip write": "roll and write",
    "carta e matita": "roll and write", "carta e penna": "roll and write",
    "avventure": "avventura", "dungeon crawler": "avventura", "dungeon": "avventura",
    "fantasy": "avventura",
    "bluffare": "bluff", "ruoli nascosti": "bluff", "ruolo nascosto": "bluff",
    "deduzione sociale": "bluff", "lupi mannari": "bluff",
    "dado": "dadi",
    "accessorio": "accessori",
}

# Altri nomi con cui si chiamano i giochi -> nome nel catalogo.
# Le voci di giochi che non sono nel catalogo vengono ignorate.
ALIAS_GIOCHI = {
    "coloni di catan": "Catan", "i coloni di catan": "Catan", "settlers of catan": "Catan",
    "catan 5 6": "Catan - Espansione 5-6 giocatori", "catan 5 6 giocatori": "Catan - Espansione 5-6 giocatori",
    "catan cities and knights": "Catan: Città e Cavalieri", "cities and knights": "Catan: Città e Cavalieri",
    "ticket": "Ticket to Ride", "ttr": "Ticket to Ride", "ticket to ride usa": "Ticket to Ride",
    "ticket to ride europe": "Ticket to Ride Europa", "ticket europa": "Ticket to Ride Europa",
    "ticket to ride italia": "Ticket to Ride: Italia e Giappone",
    "ticket to ride giappone": "Ticket to Ride: Italia e Giappone",
    "inns and cathedrals": "Carcassonne: Locande e Cattedrali",
    "seven wonders": "7 Wonders", "7wonders": "7 Wonders", "seven wonder": "7 Wonders",
    "seven wonders duel": "7 Wonders Duel", "7 wonders duello": "7 Wonders Duel",
    "pandemia": "Pandemic", "pandemic on the brink": "Pandemic: Sull'orlo del baratro",
    "on the brink": "Pandemic: Sull'orlo del baratro",
    "pandemic legacy stagione 1": "Pandemic Legacy: Stagione 1",
    "pandemic legacy season 1": "Pandemic Legacy: Stagione 1",
    "codenames duet": "Codenames Duetto", "codenames per due": "Codenames Duetto",
    "terraforming": "Terraforming Mars", "terraforming mars prelude": "Terraforming Mars: Prelude",
    "wingspan europa": "Wingspan: Espansione Europa", "wingspan espansione europa": "Wingspan: Espansione Europa",
    "tzolkin": "Tzolk'in: Il Calendario Maya", "calendario maya": "Tzolk'in: Il Calendario Maya",
    "arnak": "Le Rovine Perdute di Arnak", "rovine di arnak": "Le Rovine Perdute di Arnak",
    "rovine perdute di arnak": "Le Rovine Perdute di Arnak", "lost ruins of arnak": "Le Rovine Perdute di Arnak",
    "scythe invasori": "Scythe: Invasori da Lontano", "invaders from afar": "Scythe: Invasori da Lontano",
    "viticulture": "Viticulture Essential Edition",
    "risk": "Risiko!",
    "dune imperium": "Dune: Imperium",
    "times up": "Time's Up!", "time up": "Time's Up!",
    "spot it": "Dobble",
    "lupus": "Lupus in Tabula", "lupus in fabula": "Lupus in Tabula",
    "bang dodge city": "Bang! Dodge City", "dodge city": "Bang! Dodge City",
    "bang dadi": "Bang! Il Gioco di Dadi", "bang gioco di dadi": "Bang! Il Gioco di Dadi",
    "bang the dice game": "Bang! Il Gioco di Dadi", "bang dice game": "Bang! Il Gioco di Dadi",
    "exploding kitten": "Exploding Kittens", "gattini esplosivi": "Exploding Kittens",
    "imploding kitten": "Imploding Kittens",
    "railroad ink": "Railroad Ink: Edizione Blu Profondo",
    "railroad ink blu profondo": "Railroad Ink: Edizione Blu Profondo",
    "railroad ink deep blue": "Railroad Ink: Edizione Blu Profondo",
    "cartografi": "Cartographers", "cartographer": "Cartographers",
    "clue": "Cluedo",
    "sherlock holmes": "Sherlock Holmes Consulente Investigativo",
    "sherlock": "Sherlock Holmes Consulente Investigativo",
    "consulente investigativo": "Sherlock Holmes Consulente Investigativo",
    "capanna abbandonata": "EXIT: La Capanna Abbandonata", "exit la capanna": "EXIT: La Capanna Abbandonata",
    "gloomhaven fauci del leone": "Gloomhaven: Le Fauci del Leone",
    "fauci del leone": "Gloomhaven: Le Fauci del Leone",
    "jaws of the lion": "Gloomhaven: Le Fauci del Leone",
    "signore degli anelli": "Il Signore degli Anelli: Viaggi nella Terra di Mezzo",
    "viaggi nella terra di mezzo": "Il Signore degli Anelli: Viaggi nella Terra di Mezzo",
    "lord of the rings": "Il Signore degli Anelli: Viaggi nella Terra di Mezzo",
    "arkham": "Arkham Horror: Il Gioco di Carte", "arkham horror lcg": "Arkham Horror: Il Gioco di Carte",
    "arkham lcg": "Arkham Horror: Il Gioco di Carte",
    "marvel champions lcg": "Marvel Champions: Il Gioco di Carte",
    "hero quest": "HeroQuest",
    "zombicide 2": "Zombicide: Seconda Edizione", "zombicide 2a edizione": "Zombicide: Seconda Edizione",
    "andor": "Le Leggende di Andor", "leggende di andor": "Le Leggende di Andor",
    "legends of andor": "Le Leggende di Andor",
    "monopoly": "Monopoly Classic", "monopoli": "Monopoly Classic",
    "primo frutteto": "Il Mio Primo Frutteto", "frutteto": "Il Mio Primo Frutteto",
    "my first orchard": "Il Mio Primo Frutteto",
    "labirinto": "Labirinto Magico", "labyrinth": "Labirinto Magico",
    "gobblet": "Gobblet Gobblers",
    "dottor eureka": "Dr. Eureka", "doctor eureka": "Dr. Eureka", "dr eureka": "Dr. Eureka",
    "zombie kidz": "Zombie Kidz Evolution", "zombie kids": "Zombie Kidz Evolution",
    "friday": "Friday", "venerdi": "Friday",
    "tabu": "Taboo",
    "bomb buster": "Bomb Busters", "flip seven": "Flip 7",
    "king domino": "Kingdomino", "smallworld": "Small World",
    "dragon shield": "Bustine protettive Dragon Shield opache (100 pz)",
    "dadi poliedrici": "Set di 7 dadi poliedrici",
    "vassoio per dadi": "Vassoio per dadi pieghevole", "vassoio dadi": "Vassoio per dadi pieghevole",
    "dice tray": "Vassoio per dadi pieghevole",
    "tappetino": "Tappetino da gioco in neoprene 90x60 cm",
    "tappetino da gioco": "Tappetino da gioco in neoprene 90x60 cm",
    "playmat": "Tappetino da gioco in neoprene 90x60 cm",
    "deck box": "Deck box porta carte da 100",
    "inserto wingspan": "Inserto organizer per Wingspan", "organizer wingspan": "Inserto organizer per Wingspan",
    "organizer per wingspan": "Inserto organizer per Wingspan",
}

# Nomi o alias che sono anche parole comuni («ne voglio uno», «un quarto d'ora»):
# non vanno cercati liberamente nel testo né messi nella lookup table.
NOMI_AMBIGUI = {
    "uno", "quarto", "labirinto", "detective", "exit", "friday", "venerdi", "ticket", "risk",
    "clue", "dune", "root", "coup", "seti", "leaders", "pantheon", "prelude", "imperium",
    "tappetino", "frutteto", "lupus", "sherlock",
    # dal catalogo del negozio
    "cinque", "ideale", "geniale", "colleghi", "medium", "sipario", "presagi", "inferno", "maretta",
    "setup", "eleven", "fishing", "audience", "slide", "burst", "heat", "glow", "tilt", "sand", "pest",
    "piles", "earth", "behind", "champions", "wizard", "umbrella", "canvas", "sketch", "tension",
    "top ten", "daydream", "hint", "the club", "the gang", "the hunt", "ink", "oak", "luz", "trio",
    "dei", "mercoledi", "tombola", "porca vacca", "spietato", "la boca", "origami", "scout", "ice", "feed",
    "legends", "heroes", "dragons", "nightmares", "raids", "reflections", "all in", "on air", "too much",
    "stay cool", "take time", "the game", "the number", "magic five",
}

# Altri nomi degli editori (già normalizzati) -> editore nel catalogo.
ALIAS_EDITORI = {
    "asmodee": "Asmodee Italia", "cranio": "Cranio Creations", "ghenos": "Ghenos Games",
    "dv": "dV Giochi", "dv games": "dV Giochi", "da vinci": "dV Giochi", "da vinci giochi": "dV Giochi",
    "horrible games": "Horrible Guild", "horrible": "Horrible Guild", "raven": "Raven Distribution",
    "pendragon": "Pendragon Game Studio", "ms": "MS Edizioni", "editrice": "Editrice Giochi",
    "nsv": "Nürnberger-Spielkarten", "uniti": "Giochi Uniti",
}

# Radici di parole che indicano un tipo di accessorio, per filtrare le richieste.
TIPI_ACCESSORI = {
    "bustin": "bustin", "sleeve": "bustin", "protezion": "bustin", "proteggi": "bustin",
    "dad": "dadi", "dice": "dadi",
    "tappet": "tappetino", "playmat": "tappetino",
    "organizer": "organizer", "inserto": "organizer", "insert": "organizer",
    "porta carte": "porta carte", "portacarte": "porta carte", "deck box": "porta carte",
    "deckbox": "porta carte", "porta mazz": "porta carte",
    "vassoio": "vassoio", "tray": "vassoio",
}


def normalizza(testo: Text) -> Text:
    """Minuscolo, senza accenti né punteggiatura: serve a confrontare i nomi."""
    testo = unicodedata.normalize("NFKD", testo or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", testo.lower()).strip()


def _converti(riga: Dict[Text, Text]) -> Dict[Text, Any]:
    gioco = {campo: (riga.get(colonna) or "").strip() for campo, colonna in COLONNE.items()}
    testo_disponibilita = gioco["disponibilita"].lower()
    if testo_disponibilita in DISPONIBILITA_TESTO:
        gioco["disponibilita"] = DISPONIBILITA_TESTO[testo_disponibilita]
    else:
        gioco["disponibilita"] = int(float(testo_disponibilita)) if testo_disponibilita else 0
    for campo in CAMPI_INTERI:
        gioco[campo] = int(float(gioco[campo])) if gioco[campo] else None
    for campo in CAMPI_DECIMALI:
        gioco[campo] = float(gioco[campo].replace(",", ".")) if gioco[campo] else None
    for campo in CAMPI_LISTA:
        gioco[campo] = [v.strip() for v in gioco[campo].split("|") if v.strip()]
    gioco["novita"] = gioco["novita"] in ("1", "true", "sì", "si")
    return gioco


@lru_cache(maxsize=1)
def carica_catalogo() -> List[Dict[Text, Any]]:
    with open(PERCORSO_CATALOGO, encoding="utf-8", newline="") as file:
        return [_converti(riga) for riga in csv.DictReader(file)]


def ricarica_catalogo() -> None:
    """Svuota le cache: da chiamare dopo aver cambiato PERCORSO_CATALOGO."""
    carica_catalogo.cache_clear()
    _indice_nomi.cache_clear()


def gioco_per_id(id_gioco: int) -> Optional[Dict[Text, Any]]:
    return next((g for g in carica_catalogo() if g["id"] == id_gioco), None)


def e_accessorio(gioco: Dict[Text, Any]) -> bool:
    return CATEGORIA_ACCESSORI in [normalizza(c) for c in gioco["categorie"]]


def varianti_nome(gioco: Dict[Text, Any]) -> List[Text]:
    """Modi diversi di scrivere il nome: senza punteggiatura, accenti, sottotitolo o parentesi."""
    nome = gioco["nome"]
    senza_parentesi = re.sub(r"\s*\([^)]*\)\s*$", "", nome)
    pulito = re.sub(r"\s+", " ", re.sub(r"\s*(:|\s-\s)\s*|[!?]", " ", senza_parentesi)).strip()
    varianti = [nome, senza_parentesi, pulito]
    parti = re.split(r"\s*:\s*|\s+-\s+", senza_parentesi, maxsplit=1)
    if len(parti) == 2:
        varianti.append(parti[0])
        # il sottotitolo da solo («Città e Cavalieri») solo per le espansioni
        if gioco["espansione_di"]:
            varianti.append(parti[1])
    for v in list(varianti):
        senza_accenti = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode()
        varianti += [senza_accenti, re.sub(r"['’]", "", v), re.sub(r"['’]", "", senza_accenti)]
    risultato = []
    for v in varianti:
        v = re.sub(r"[!?.\s]+$", "", v.strip())
        if v and v.lower() not in [r.lower() for r in risultato]:
            risultato.append(v)
    return risultato


@lru_cache(maxsize=1)
def _indice_nomi() -> Dict[Text, Dict[Text, Any]]:
    """Nome normalizzato (nome vero, variante o alias) -> gioco."""
    catalogo = carica_catalogo()
    indice = {normalizza(g["nome"]): g for g in catalogo}
    proposte = {}  # type: Dict[Text, List[Dict[Text, Any]]]
    for g in catalogo:
        for variante in varianti_nome(g):
            chiave = normalizza(variante)
            if chiave and chiave not in indice and g not in proposte.setdefault(chiave, []):
                proposte[chiave].append(g)
    for chiave, giochi in proposte.items():
        # una variante che vale per più giochi («Il Gioco di Carte») non identifica nessuno
        if len(giochi) == 1:
            indice[chiave] = giochi[0]
    for alias, nome in ALIAS_GIOCHI.items():
        gioco = indice.get(normalizza(nome))
        if gioco:
            indice.setdefault(normalizza(alias), gioco)
    return indice


def _preferenza(gioco: Dict[Text, Any]) -> Any:
    # a parità di somiglianza: prima i giochi base, poi i nomi più corti
    return (bool(gioco["espansione_di"]), e_accessorio(gioco), len(gioco["nome"]))


def trova_gioco(nome: Optional[Text]) -> Optional[Dict[Text, Any]]:
    """Il gioco il cui nome somiglia di più a `nome`, o None se nessuno è abbastanza vicino.

    A parità di somiglianza preferisce il gioco base alle espansioni.
    """
    cercato = normalizza(nome or "")
    if not cercato:
        return None
    catalogo = carica_catalogo()
    esatti = [g for g in catalogo if normalizza(g["nome"]) == cercato]
    if esatti:
        return esatti[0]
    indice = _indice_nomi()
    if cercato in indice:
        return indice[cercato]
    compatto = cercato.replace(" ", "")
    for chiave, gioco in indice.items():
        if chiave.replace(" ", "") == compatto:
            return gioco
    # il testo compare per intero nel nome («ticket» -> Ticket to Ride)
    if len(cercato) >= 3:
        parola = re.compile(r"\b" + re.escape(cercato) + r"\b")
        contenuti = [g for g in catalogo if parola.search(normalizza(g["nome"]))]
        if not contenuti and len(cercato) >= 4:
            contenuti = [g for g in catalogo if cercato in normalizza(g["nome"])]
        if contenuti:
            return min(contenuti, key=_preferenza)
    # tutte le parole compaiono nel nome, anche non vicine («bustine dragon», «ticket italia»)
    parole = [p for p in cercato.split() if len(p) >= 3]
    if len(parole) >= 2:
        contenuti = [g for g in catalogo
                     if all(re.search(r"\b" + re.escape(p), normalizza(g["nome"])) for p in parole)]
        if contenuti:
            return min(contenuti, key=_preferenza)
    # un nome del catalogo compare dentro il testo («il gioco catan»)
    nel_testo = trova_giochi_nel_testo(cercato, escludi_ambigui=False)
    if nel_testo:
        return nel_testo[0]
    vicini = difflib.get_close_matches(cercato, list(indice), n=1, cutoff=0.75)
    if vicini:
        return indice[vicini[0]]
    compatti = {chiave.replace(" ", ""): g for chiave, g in indice.items()}
    vicini = difflib.get_close_matches(compatto, list(compatti), n=1, cutoff=0.8)
    return compatti[vicini[0]] if vicini else None


def trova_giochi_nel_testo(testo: Optional[Text], escludi_ambigui: bool = True) -> List[Dict[Text, Any]]:
    """Giochi del catalogo nominati per intero in una frase, nell'ordine in cui compaiono.

    Con `escludi_ambigui` ignora i nomi che sono anche parole comuni (es. «uno»).
    """
    frase = " " + normalizza(testo or "") + " "
    occupato = [False] * len(frase)
    trovati = []
    for chiave, gioco in sorted(_indice_nomi().items(), key=lambda voce: -len(voce[0])):
        if len(chiave) < 3 or (escludi_ambigui and chiave in NOMI_AMBIGUI):
            continue
        inizio = frase.find(" " + chiave + " ")
        while inizio >= 0:
            da, a = inizio + 1, inizio + 1 + len(chiave)
            if not any(occupato[da:a]):
                occupato[da:a] = [True] * (a - da)
                trovati.append((da, gioco))
            inizio = frase.find(" " + chiave + " ", inizio + 1)
    risultato = []
    for _, gioco in sorted(trovati, key=lambda voce: voce[0]):
        if gioco not in risultato:
            risultato.append(gioco)
    return risultato


def nomi_simili(nome: Optional[Text], n: int = 3) -> List[Text]:
    """Nomi del catalogo che somigliano a `nome`, per suggerirli quando un gioco non si trova."""
    cercato = normalizza(nome or "")
    if not cercato:
        return []
    parole = [p for p in cercato.split() if len(p) >= 4]
    punteggi = {}  # type: Dict[Text, float]
    giochi = {}  # type: Dict[Text, Dict[Text, Any]]
    for chiave, gioco in _indice_nomi().items():
        punteggio = difflib.SequenceMatcher(None, cercato, chiave).ratio()
        # somiglianza tra singole parole («gloomheaven» ~ «gloomhaven»), pesata un po' meno
        for p in parole:
            for q in chiave.split():
                if len(q) >= 4:
                    punteggio = max(punteggio, 0.85 * difflib.SequenceMatcher(None, p, q).ratio())
        if punteggio > punteggi.get(gioco["nome"], 0):
            punteggi[gioco["nome"]] = punteggio
            giochi[gioco["nome"]] = gioco
    if not punteggi:
        return []
    soglia = max(0.62, max(punteggi.values()) - 0.12)
    ordinati = sorted(punteggi, key=lambda k: (-round(punteggi[k], 2), _preferenza(giochi[k])))
    return [k for k in ordinati if punteggi[k] >= soglia][:n]


def espansioni_di(gioco: Dict[Text, Any]) -> List[Dict[Text, Any]]:
    base = normalizza(gioco["nome"])
    return [g for g in carica_catalogo() if normalizza(g["espansione_di"]) == base]


def prezzo_attuale(gioco: Dict[Text, Any]) -> float:
    return gioco["prezzo_scontato"] or gioco["prezzo"]


def sconto_percentuale(gioco: Dict[Text, Any]) -> Optional[int]:
    if not gioco["prezzo_scontato"] or not gioco["prezzo"]:
        return None
    return round(100 * (1 - gioco["prezzo_scontato"] / gioco["prezzo"]))


def disponibile(gioco: Dict[Text, Any]) -> bool:
    return (gioco["disponibilita"] or 0) > 0


def categoria_canonica(testo: Optional[Text]) -> Optional[Text]:
    """Riporta una categoria scritta liberamente («giochi cooperativi») a quella del catalogo."""
    cercato = normalizza(testo or "")
    ridotto = re.sub(r"\b(giochi|gioco|game|games|di societa|da tavolo|tipo|genere|categoria|"
                     r"di|da|per|a|in|i|il|la|le|lo|un|tutta|tutti)\b", " ", cercato)
    for chiave in (cercato, re.sub(r"\s+", " ", ridotto).strip()):
        if not chiave:
            continue
        if chiave in CATEGORIE:
            return chiave
        if chiave in SINONIMI_CATEGORIE:
            return SINONIMI_CATEGORIE[chiave]
        if chiave.endswith("i") and chiave[:-1] + "o" in CATEGORIE:
            return chiave[:-1] + "o"
    return None


def _ha_categoria(gioco: Dict[Text, Any], categoria: Text, anche_meccaniche: bool = True) -> bool:
    valori = gioco["categorie"] + (gioco["meccaniche"] if anche_meccaniche else [])
    return categoria in [normalizza(v) for v in valori]


def cerca_giochi(
    giocatori: Optional[int] = None,
    eta: Optional[int] = None,
    durata_max: Optional[int] = None,
    budget_max: Optional[float] = None,
    categoria: Optional[Text] = None,
    solo_disponibili: bool = True,
    includi_espansioni: bool = False,
    limite: Optional[int] = 5,
) -> List[Dict[Text, Any]]:
    """Giochi che rispettano tutti i filtri dati, prima le novità.

    `eta` è l'età del giocatore più giovane: esclude i giochi con età minima superiore.
    Gli accessori compaiono solo se si chiede la categoria «accessori».
    """
    categoria = (categoria_canonica(categoria) or normalizza(categoria)) if categoria else None
    risultati = []
    for g in carica_catalogo():
        if g["espansione_di"] and not includi_espansioni:
            continue
        if e_accessorio(g) and categoria != CATEGORIA_ACCESSORI:
            continue
        if solo_disponibili and not disponibile(g):
            continue
        if giocatori and not ((g["giocatori_min"] or 1) <= giocatori <= (g["giocatori_max"] or 99)):
            continue
        if eta and (g["eta_min"] or 0) > eta:
            continue
        if durata_max and (g["durata_min"] or 0) > durata_max:
            continue
        if budget_max and prezzo_attuale(g) > budget_max:
            continue
        if categoria and not _ha_categoria(g, categoria):
            continue
        risultati.append(g)
    risultati.sort(key=lambda g: not g["novita"])
    return risultati[:limite] if limite else risultati


def _ordina_e_taglia(giochi: List[Dict[Text, Any]], limite: Optional[int]) -> List[Dict[Text, Any]]:
    # prima i disponibili, poi le novità
    giochi = sorted(giochi, key=lambda g: (not disponibile(g), not g["novita"]))
    return giochi[:limite] if limite else giochi


def giochi_per_categoria(
    categoria: Text,
    giocatori: Optional[int] = None,
    solo_disponibili: bool = False,
    includi_espansioni: bool = False,
    limite: Optional[int] = None,
) -> List[Dict[Text, Any]]:
    """Giochi di una categoria (prima i disponibili). Se nessun gioco ha quella categoria,
    cerca tra le meccaniche (es. «draft»)."""
    cat = categoria_canonica(categoria) or normalizza(categoria)
    candidati = [
        g for g in carica_catalogo()
        if (includi_espansioni or not g["espansione_di"])
        and (not solo_disponibili or disponibile(g))
        and (not giocatori or (g["giocatori_min"] or 1) <= giocatori <= (g["giocatori_max"] or 99))
    ]
    risultati = [g for g in candidati if _ha_categoria(g, cat, anche_meccaniche=False)]
    if not risultati:
        risultati = [g for g in candidati if _ha_categoria(g, cat)]
    return _ordina_e_taglia(risultati, limite)


def editori(includi_accessori: bool = False) -> List[Text]:
    nomi = {g["editore"] for g in carica_catalogo() if g["editore"] and (includi_accessori or not e_accessorio(g))}
    return sorted(nomi, key=lambda e: normalizza(e))


def trova_editore(nome: Optional[Text]) -> Optional[Text]:
    """Il nome dell'editore del catalogo che corrisponde a `nome` («cranio» -> Cranio Creations)."""
    cercato = normalizza(nome or "")
    if not cercato:
        return None
    tutti = {normalizza(e): e for e in editori(includi_accessori=True)}
    if cercato in tutti:
        return tutti[cercato]
    alias = ALIAS_EDITORI.get(cercato)
    if alias and normalizza(alias) in tutti:
        return tutti[normalizza(alias)]
    parola = re.compile(r"\b" + re.escape(cercato) + r"\b")
    contenuti = [e for chiave, e in tutti.items() if parola.search(chiave)]
    if len(contenuti) == 1:
        return contenuti[0]
    vicini = difflib.get_close_matches(cercato, list(tutti), n=1, cutoff=0.75)
    return tutti[vicini[0]] if vicini else None


def giochi_di_editore(
    editore: Text, solo_disponibili: bool = False, includi_espansioni: bool = True, limite: Optional[int] = None
) -> List[Dict[Text, Any]]:
    nome = normalizza(trova_editore(editore) or editore)
    risultati = [
        g for g in carica_catalogo()
        if normalizza(g["editore"]) == nome
        and (includi_espansioni or not g["espansione_di"])
        and (not solo_disponibili or disponibile(g))
    ]
    return _ordina_e_taglia(risultati, limite)


def giochi_in_offerta(
    categoria: Optional[Text] = None, solo_disponibili: bool = False, limite: Optional[int] = None
) -> List[Dict[Text, Any]]:
    """Prodotti con prezzo scontato, prima i disponibili e quelli con lo sconto maggiore."""
    cat = (categoria_canonica(categoria) or normalizza(categoria)) if categoria else None
    risultati = [
        g for g in carica_catalogo()
        if g["prezzo_scontato"]
        and (not solo_disponibili or disponibile(g))
        and (not cat or _ha_categoria(g, cat))
    ]
    risultati.sort(key=lambda g: (not disponibile(g), -(sconto_percentuale(g) or 0)))
    return risultati[:limite] if limite else risultati


def giochi_novita(solo_disponibili: bool = False, limite: Optional[int] = None) -> List[Dict[Text, Any]]:
    risultati = [g for g in carica_catalogo() if g["novita"] and (not solo_disponibili or disponibile(g))]
    risultati.sort(key=lambda g: (not disponibile(g), -(g["anno"] or 0)))
    return risultati[:limite] if limite else risultati


def tipi_accessorio(testo: Optional[Text]) -> List[Text]:
    """Tipi di accessorio nominati in una frase («bustine», «dadi», ...)."""
    frase = normalizza(testo or "")
    tipi = []
    for radice, tipo in TIPI_ACCESSORI.items():
        if re.search(r"\b" + re.escape(radice), frase) and tipo not in tipi:
            tipi.append(tipo)
    return tipi


def accessori(testo: Optional[Text] = None, solo_disponibili: bool = False) -> List[Dict[Text, Any]]:
    """Gli accessori del catalogo; se `testo` nomina un tipo (bustine, dadi...) solo quelli."""
    tutti = [g for g in carica_catalogo() if e_accessorio(g) and (not solo_disponibili or disponibile(g))]
    tipi = tipi_accessorio(testo)
    if tipi:
        tutti = [g for g in tutti if any(re.search(r"\b" + re.escape(t), normalizza(g["nome"])) for t in tipi)]
    return sorted(tutti, key=lambda g: (not disponibile(g), normalizza(g["nome"])))


def giochi_simili(
    gioco: Dict[Text, Any], limite: int = 3, solo_disponibili: bool = True,
    giocatori: Optional[int] = None, eta: Optional[int] = None,
) -> List[Dict[Text, Any]]:
    """Giochi che somigliano a `gioco` per categorie e meccaniche (utile come alternativa)."""
    if e_accessorio(gioco):
        prima_parola = normalizza(gioco["nome"]).split(" ")[0]
        return [g for g in accessori(solo_disponibili=solo_disponibili)
                if g is not gioco and normalizza(g["nome"]).startswith(prima_parola)][:limite]
    famiglia = {normalizza(gioco["nome"]), normalizza(gioco["espansione_di"])} - {""}
    categorie = {normalizza(c) for c in gioco["categorie"]}
    meccaniche = {normalizza(m) for m in gioco["meccaniche"]}
    candidati = []
    for g in carica_catalogo():
        if g["espansione_di"] or e_accessorio(g) or normalizza(g["nome"]) in famiglia:
            continue
        if solo_disponibili and not disponibile(g):
            continue
        if giocatori and not ((g["giocatori_min"] or 1) <= giocatori <= (g["giocatori_max"] or 99)):
            continue
        if eta and (g["eta_min"] or 0) > eta:
            continue
        comuni_cat = len(categorie & {normalizza(c) for c in g["categorie"]})
        comuni_mec = len(meccaniche & {normalizza(m) for m in g["meccaniche"]})
        if not comuni_cat and not comuni_mec:
            continue
        candidati.append((3 * comuni_cat + comuni_mec, g))
    candidati.sort(key=lambda c: c[0], reverse=True)
    return [g for _, g in candidati[:limite]]


def estrai_numero(testo: Optional[Text]) -> Optional[int]:
    """Primo numero in `testo`, scritto in cifre o in lettere («quattro» -> 4)."""
    if not testo:
        return None
    cifre = re.search(r"\d+", str(testo))
    if cifre:
        return int(cifre.group())
    for parola in normalizza(str(testo)).split():
        if parola in NUMERI_IN_LETTERE:
            return NUMERI_IN_LETTERE[parola]
    return None


def formatta_prezzo(valore: float) -> Text:
    return f"{valore:.2f} €".replace(".", ",")


def giocatori_testo(gioco: Dict[Text, Any]) -> Text:
    """«da 2 a 4 giocatori»; stringa vuota per gli accessori o se il dato manca."""
    minimo, massimo = gioco["giocatori_min"], gioco["giocatori_max"]
    if not minimo and not massimo:
        return ""
    if not massimo or massimo > 20:
        minimo = minimo or 1
        return f"da {minimo} {'giocatore' if minimo == 1 else 'giocatori'} in su"
    if not minimo or minimo == massimo:
        return "1 giocatore" if massimo == 1 else f"{massimo} giocatori"
    return f"da {minimo} a {massimo} giocatori"


def durata_testo(gioco: Dict[Text, Any]) -> Text:
    """«45 minuti», «un'ora e mezza», «2 ore»; stringa vuota se il dato manca."""
    minuti = gioco["durata_min"]
    if not minuti:
        return ""
    if minuti < 60:
        return f"{minuti} minuti"
    ore, resto = divmod(minuti, 60)
    testo = "un'ora" if ore == 1 else f"{ore} ore"
    if resto == 30:
        return testo + " e mezza"
    return testo + (f" e {resto} minuti" if resto else "")


def eta_testo(gioco: Dict[Text, Any]) -> Text:
    """«dai 10 anni», «dagli 8 anni»; stringa vuota se il dato manca."""
    eta = gioco["eta_min"]
    if not eta:
        return ""
    return f"{'dagli' if eta in (8, 11) or 80 <= eta <= 89 else 'dai'} {eta} anni"


def descrivi_breve(gioco: Dict[Text, Any]) -> Text:
    """Una riga: nome, giocatori, durata, età e prezzo (con lo sconto se c'è)."""
    prezzo = formatta_prezzo(prezzo_attuale(gioco))
    if gioco["prezzo_scontato"]:
        prezzo += f" (invece di {formatta_prezzo(gioco['prezzo'])})"
    if e_accessorio(gioco) or not gioco["giocatori_min"]:
        return f"{gioco['nome']}: {prezzo}"
    parti = [giocatori_testo(gioco)]
    if gioco["durata_min"]:
        parti.append(f"circa {gioco['durata_min']} minuti")
    if gioco["eta_min"]:
        parti.append(eta_testo(gioco))
    return f"{gioco['nome']}: {', '.join(parti + [prezzo])}"
