# Consigli: consiglio personalizzato (consiglio_form), consigli per occasione,
# giochi simili, idee regalo e «sorprendimi».
# I giochi vengono sempre dal catalogo (actions/catalogo.py): qui ci sono solo le regole
# per tradurre le richieste in filtri e per spiegare perché un gioco è adatto.

import itertools
import math
import random
import re
from typing import Any, Callable, Dict, List, Optional, Text, Tuple

from rasa_sdk import Action, FormValidationAction, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.types import DomainDict

from actions import catalogo

Gioco = Dict[Text, Any]

INDIFFERENTE = "indifferente"
SLOT_FORM = [
    "consiglio_giocatori",
    "consiglio_eta",
    "consiglio_durata",
    "consiglio_budget",
    "consiglio_genere",
]
MIN_PROPOSTE = 3
MAX_PROPOSTE = 5

# ---------------------------------------------------------------------------
# Lettura delle risposte dell'utente
# ---------------------------------------------------------------------------

INDIFFERENZA = re.compile(
    r"\b(non (mi )?importa|indifferente|qualsiasi|qualunque|fa lo stesso|e (lo stesso|uguale)|"
    r"uguale|nessuna preferenza|(non ho|senza) (nessuna |particolari )?preferenz\w*|"
    r"decidi tu|vedi tu|fai tu|come vuoi( tu)?|va bene tutto|tutto va bene|non saprei|non so|"
    r"boh+|(nessun|senza|non ho) (un |nessun )?(limite|limiti|budget|vincol\w*)|"
    r"a caso|non conta|non e importante)\b"
)
RISPOSTE_NO = {"no", "nessuno", "nessuna", "niente", "no grazie", "nope", "non ho"}

# Numeri in lettere che il catalogo non conosce
ALTRI_NUMERI = {
    "tredici": 13, "quattordici": 14, "sedici": 16, "diciassette": 17, "diciotto": 18,
    "diciannove": 19, "venticinque": 25, "trentacinque": 35, "quarantacinque": 45,
    "settanta": 70, "ottanta": 80, "novanta": 90, "duecento": 200,
    "decina": 10, "dozzina": 12,
}

# Parole che fanno capire a quale domanda si riferisce un numero detto "di passaggio"
CONTESTO = {
    "consiglio_giocatori": re.compile(
        r"giocator|person|siamo|saremo|amic|coppia|\bin \w+\b|\bper \d+\b|da sol|solitari|gruppo"
    ),
    "consiglio_eta": re.compile(r"\bann[io]\b|\beta\b|bambin|bimb|figli|nipot|ragazz|adult|piccol"),
    "consiglio_durata": re.compile(r"\bmin|\bor[ae]\b|mezz|veloc|rapid|brev|lung|durat|tempo|quart"),
    "consiglio_budget": re.compile(r"euro|\beur\b|budget|spend|prezz|cost|soldi"),
    "consiglio_genere": re.compile(r"."),
}


def _numeri(testo: Optional[Text]) -> List[float]:
    """Tutti i numeri del testo: prima quelli in cifre (anche decimali), poi quelli in lettere.

    Gli articoli «un/una/uno» non contano come 1.
    """
    testo = str(testo or "")
    cifre = re.findall(r"\d+(?:[.,]\d+)?", testo)
    if cifre:
        return [float(n.replace(",", ".")) for n in cifre]
    risultati = []
    for parola in catalogo.normalizza(testo).split():
        if parola in ("un", "uno", "una"):
            continue
        valore = catalogo.estrai_numero(parola) or ALTRI_NUMERI.get(parola)
        if valore:
            risultati.append(float(valore))
    return risultati


def giocatori_da_testo(testo: Optional[Text]) -> Optional[int]:
    t = catalogo.normalizza(testo or "")
    numeri = _numeri(testo)
    if numeri:
        return int(numeri[0])
    if t in ("un", "uno", "una") or re.search(
            r"\b(da sol[oa]|solo io|solitari\w*|in solo|io e basta)\b", t):
        return 1
    if re.search(r"\b(coppia|io e (la mia|il mio|mia|mio)|fidanzat\w*|moglie|marito)\b", t):
        return 2
    if re.search(r"\b(tant[ie]|molt[ie]|numeros\w*|gruppo|gruppone|festa)\b", t):
        return 6
    return None


def eta_da_testo(testo: Optional[Text]) -> Optional[int]:
    """Età del giocatore più giovane: se ci sono più età prende la minore («bambino di 6 anni» -> 6)."""
    t = catalogo.normalizza(testo or "")
    if re.search(r"\d", t):
        # scarta i numeri che parlano d'altro («siamo in 4», «30 euro», «45 minuti»)
        altro = r"(?<!\d)(\d+)(?!\d)(\s+(person\w*|giocator\w*|amic\w*|euro|eur|minut\w*|or[ae])\b)?"
        numeri = []
        for m in re.finditer(altro, t):
            prima = t[:m.start(1)]
            if m.group(2) or re.search(r"\b(in|siamo|saremo)\s*$", prima):
                continue
            numeri.append(int(m.group(1)))
    else:
        numeri = [int(n) for n in _numeri(testo)]
    numeri = [n for n in numeri if n > 0]
    if numeri:
        return min(numeri)
    if re.search(r"\b(adult\w*|maggiorenn\w*|grandi|tutti grandi)\b", t):
        return 18
    return None


def minuti_da_testo(testo: Optional[Text]) -> Optional[int]:
    """Durata in minuti: «un'ora» = 60, «mezz'ora» = 30, «veloce» = 30, «2 ore» = 120, «45» = 45."""
    t = catalogo.normalizza(testo or "")
    if not t:
        return None
    if re.search(r"\btre quarti d ora\b", t):
        return 45
    if re.search(r"\bquarto d ora\b", t):
        return 15
    e_mezza = 30 if re.search(r"\bor[ae] e mezz[ao]\b", t) else 0
    if not e_mezza and re.search(r"\bmezz ?ora\b|\bmezza ora\b", t):
        return 30
    decimali = re.search(r"(\d+[.,]\d+)\s*(h|or[ae])\b", str(testo).lower())
    if decimali:
        return int(round(float(decimali.group(1).replace(",", ".")) * 60))
    ore = re.search(r"\b(\d+|un|una|uno|due|tre|quattro|cinque|sei)\s*(h|or[ae])\b", t)
    if ore:
        quante = 1 if ore.group(1) in ("un", "una", "uno") else catalogo.estrai_numero(ore.group(1))
        return (quante or 1) * 60 + e_mezza
    minuti = re.search(r"\b(\d+)\s*(m|min|minut[io])\b", t)
    if minuti:
        return int(minuti.group(1))
    if re.search(r"\b(veloc\w*|rapid\w*|brev\w*|cort\w*|poco tempo|filler|al volo)\b", t):
        return 30
    numeri = _numeri(testo)
    if numeri:
        # «2» o «3» da soli sono ore, «45» sono minuti
        return int(numeri[0] * 60) if numeri[0] <= 5 else int(numeri[0])
    return None


def budget_da_testo(testo: Optional[Text]) -> Optional[float]:
    """Budget massimo in euro: con un intervallo («tra 20 e 40») prende il valore più alto."""
    testo = str(testo or "")
    basso = testo.lower()
    vicini_euro = re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:€|euro\b|eur\b)", basso)
    vicini_euro += re.findall(r"€\s*(\d+(?:[.,]\d+)?)", basso)
    if vicini_euro:
        return max(float(n.replace(",", ".")) for n in vicini_euro)
    numeri = _numeri(testo)
    if numeri:
        return max(numeri)
    if re.search(r"\b(economic\w*|poco|low cost|risparmi\w*|spendere poco)\b", catalogo.normalizza(testo)):
        return 25.0
    return None


def _e_lunga(testo: Text) -> bool:
    t = catalogo.normalizza(testo)
    return bool(re.search(
        r"\b(lung\w*|tutta la sera\w*|serata intera|tutto il pomeriggio|senza fretta|"
        r"non abbiamo fretta|tutto il tempo)\b", t))


# Sinonimi del genere: radice cercata nel testo -> categoria o meccanica del catalogo.
# Vengono usati solo se quella categoria esiste davvero nel catalogo caricato.
SINONIMI_GENERE = [
    ("strategic", "strategia"), ("gestional", "strategia"), ("eurogame", "strategia"),
    ("german", "strategia"), ("party game", "party"), ("fest", "party"), ("compagnia", "party"),
    ("risat", "party"), ("famil", "famiglia"), ("bimb", "bambini"), ("bambin", "bambini"),
    ("piccol", "bambini"), ("collaborativ", "cooperativo"), ("coop", "cooperativo"),
    ("tutti contro il gioco", "cooperativo"), ("deck", "deckbuilding"), ("mazz", "deckbuilding"),
    ("logic", "astratto"), ("scacchi", "astratto"), ("investigat", "investigativo"),
    ("giall", "investigativo"), ("mister", "investigativo"), ("detective", "investigativo"),
    ("indagin", "investigativo"), ("deduzione sociale", "bluff"), ("ruoli nascosti", "bluff"),
    ("traditor", "bluff"), ("avventur", "avventura"), ("fantasy", "avventura"),
    ("dungeon", "avventura"), ("dad", "dadi"), ("cart", "carte"), ("roll", "roll and write"),
    ("flip", "roll and write"), ("carta e matita", "roll and write"), ("solitari", "solitario"),
    ("da sol", "solitario"), ("due giocatori", "per due"), ("in due", "per due"),
    ("coppi", "per due"), ("lavorator", "piazzamento lavoratori"),
    ("worker", "piazzamento lavoratori"), ("campagna", "legacy"),
]
CATEGORIE_NON_GIOCHI = {"accessori", "accessorio"}


def _e_gioco(gioco: Gioco) -> bool:
    """Esclude accessori (bustine, dadi, organizer…), che non vanno proposti come giochi."""
    categorie = {catalogo.normalizza(c) for c in gioco["categorie"]}
    return not (categorie & CATEGORIE_NON_GIOCHI) and gioco["giocatori_min"] is not None


def _cerca(limite: Optional[int] = None, **filtri: Any) -> List[Gioco]:
    """cerca_giochi del catalogo, senza accessori."""
    giochi = [g for g in catalogo.cerca_giochi(limite=None, **filtri) if _e_gioco(g)]
    return giochi[:limite] if limite else giochi


def _radice(parola: Text) -> Text:
    return parola[:-1] if len(parola) >= 4 and parola[-1] in "aeiou" else parola


def generi_catalogo() -> Dict[Text, Text]:
    """Generi riconosciuti: categorie e meccaniche presenti nel catalogo (normalizzate -> originali)."""
    generi = {}
    for gioco in catalogo.carica_catalogo():
        if not _e_gioco(gioco):
            continue
        for valore in gioco["categorie"] + gioco["meccaniche"]:
            generi.setdefault(catalogo.normalizza(valore), valore)
    return generi


def categorie_principali(quante: int = 10) -> List[Text]:
    """Le categorie più frequenti del catalogo, per suggerire all'utente cosa può scegliere."""
    conteggio = {}  # type: Dict[Text, int]
    for gioco in catalogo.carica_catalogo():
        if gioco["espansione_di"] or not _e_gioco(gioco):
            continue
        for categoria in gioco["categorie"]:
            conteggio[categoria] = conteggio.get(categoria, 0) + 1
    return [c for c, _ in sorted(conteggio.items(), key=lambda kv: (-kv[1], kv[0]))][:quante]


def genere_da_testo(testo: Optional[Text]) -> Optional[Text]:
    t = catalogo.normalizza(testo or "")
    if not t:
        return None
    generi = generi_catalogo()
    # prima i sinonimi ufficiali del catalogo, se il modulo li offre
    canonica = getattr(catalogo, "categoria_canonica", None)
    if canonica:
        trovata = catalogo.normalizza(canonica(t) or "")
        if trovata in generi:
            return generi[trovata]
    # poi i valori del catalogo, dal più lungo (così «piazzamento lavoratori» batte «piazzamento»)
    for normale in sorted(generi, key=len, reverse=True):
        radici = r"\s+".join(re.escape(_radice(p)) + r"\w*" for p in normale.split())
        if re.search(r"\b" + radici, t):
            return generi[normale]
    for radice, genere in SINONIMI_GENERE:
        if re.search(r"\b" + re.escape(radice), t) and catalogo.normalizza(genere) in generi:
            return generi[catalogo.normalizza(genere)]
    return None


# ---------------------------------------------------------------------------
# Aiuti sul tracker
# ---------------------------------------------------------------------------

def _testo_utente(tracker: Tracker) -> Text:
    return tracker.latest_message.get("text") or ""


def _intent_utente(tracker: Tracker) -> Optional[Text]:
    return (tracker.latest_message.get("intent") or {}).get("name")


def _da_pulsante(tracker: Tracker) -> bool:
    return _testo_utente(tracker).startswith("/")


def _entita(tracker: Tracker, nome: Text) -> Optional[Text]:
    valore = next(tracker.get_latest_entity_values(nome), None)
    return str(valore) if valore is not None else None


def _e_indifferente(testo: Text, tracker: Tracker, accetta_no: bool) -> bool:
    if _intent_utente(tracker) == "rispondi_indifferente":
        return True
    t = catalogo.normalizza(testo)
    if INDIFFERENZA.search(t):
        return True
    return accetta_no and (_intent_utente(tracker) == "deny" or t in RISPOSTE_NO)


def _valore_precedente(tracker: Tracker, slot: Text) -> Any:
    """Valore che lo slot aveva prima dell'ultimo messaggio dell'utente."""
    superato_utente = False
    for evento in reversed(tracker.events):
        if evento.get("event") == "user":
            superato_utente = True
        elif superato_utente and evento.get("event") == "slot" and evento.get("name") == slot:
            return evento.get("value")
    return None


def _slot_impostati_nel_turno(tracker: Tracker) -> List[Text]:
    nomi = []
    for evento in reversed(tracker.events):
        if evento.get("event") == "user":
            break
        if evento.get("event") == "slot":
            nomi.append(evento.get("name"))
    return nomi


# ---------------------------------------------------------------------------
# Ricerca e presentazione dei giochi
# ---------------------------------------------------------------------------

ORDINE_ALLENTAMENTO = ["categoria", "durata_max", "budget_max", "eta", "giocatori"]
NOMI_FILTRI = {
    "categoria": "sul genere",
    "durata_max": "sulla durata",
    "budget_max": "sul budget",
    "eta": "sull'età",
    "giocatori": "sul numero di giocatori",
}


def _elenco(parti: List[Text]) -> Text:
    parti = [p for p in parti if p]
    if len(parti) <= 1:
        return "".join(parti)
    congiunzione = " ed " if parti[-1][:1] in ("e", "è") else " e "
    return ", ".join(parti[:-1]) + congiunzione + parti[-1]


def _dai(anni: Any) -> Text:
    """«dai 10 anni», ma «dagli 8 anni» e «dagli 11 anni»."""
    anni = int(anni or 0)
    articolo = "dagli" if anni in (8, 11) or 80 <= anni <= 89 else "dai"
    return f"{articolo} {anni} anni"


def _testo_durata(minuti: int) -> Text:
    if minuti == 30:
        return "mezz'ora"
    if minuti == 60:
        return "un'ora"
    if minuti == 90:
        return "un'ora e mezza"
    if minuti % 60 == 0:
        return f"{minuti // 60} ore"
    return f"{minuti} minuti"


def _testo_filtro(nome: Text, valore: Any) -> Text:
    if nome == "giocatori":
        return "1 giocatore" if valore == 1 else f"{valore} giocatori"
    if nome == "eta":
        return "adulti" if valore >= 18 else _dai(valore)
    if nome == "durata_max":
        return f"entro {_testo_durata(valore)}"
    if nome == "budget_max":
        return f"fino a {catalogo.formatta_prezzo(valore)}"
    return f"genere {valore}"


# Categorie che dicono per quanti si gioca più che il genere: non servono a descrivere il tipo di gioco
CATEGORIE_FORMATO = {"per due", "solitario"}


def _tipo(gioco: Gioco) -> Text:
    """Che tipo di gioco è: categorie e meccaniche principali, senza ripetizioni."""
    categorie = [c for c in gioco["categorie"] if catalogo.normalizza(c) not in CATEGORIE_FORMATO] \
        or gioco["categorie"]
    categorie = categorie[:2]
    gia_dette = {catalogo.normalizza(c) for c in categorie}
    meccaniche = [m for m in gioco["meccaniche"] if catalogo.normalizza(m) not in gia_dette][:2]
    if categorie and meccaniche:
        return f"{_elenco(categorie)}, con {_elenco(meccaniche)}"
    return _elenco(categorie) or _elenco(meccaniche) or "gioco da tavolo"


def _punti_di_forza(gioco: Gioco) -> List[Text]:
    punti = []
    if gioco["prezzo_scontato"]:
        punti.append("ora in offerta")
    if gioco["novita"]:
        punti.append("è una novità")
    return punti


def _riga(gioco: Gioco, motivo: Text) -> Text:
    motivo = motivo[:1].upper() + motivo[1:]
    return f"• {catalogo.descrivi_breve(gioco)}\n   → {motivo}"


def _motivo_filtri(gioco: Gioco, filtri: Dict[Text, Any]) -> Text:
    adatto = []
    giocatori = filtri.get("giocatori")
    if giocatori:
        if gioco["giocatori_min"] == gioco["giocatori_max"] == giocatori:
            adatto.append(f"pensato proprio per {giocatori}" if giocatori > 1 else "pensato per giocare da soli")
        elif giocatori == 1:
            adatto.append("si gioca anche da soli")
        else:
            adatto.append(f"perfetto in {giocatori}")
    adatto += _punti_di_forza(gioco)
    if not adatto:
        return _tipo(gioco)
    return f"{_tipo(gioco)}; {_elenco(adatto[:3])}"


def cerca_con_allentamento(filtri: Dict[Text, Any],
                           limite: Optional[int] = MAX_PROPOSTE) -> Tuple[List[Gioco], List[Text]]:
    """Cerca con tutti i filtri; se non trova nulla toglie i filtri meno importanti, uno alla volta.

    Restituisce i giochi trovati e i nomi dei filtri tolti.
    """
    attivi = [f for f in ORDINE_ALLENTAMENTO if filtri.get(f) is not None]
    for quanti in range(len(attivi) + 1):
        for tolti in itertools.combinations(attivi, quanti):
            prova = {k: v for k, v in filtri.items() if k not in tolti}
            risultati = _cerca(limite=None, **prova)
            if risultati:
                if tolti:
                    risultati.sort(key=lambda g: (round(_scarto(g, filtri, tolti), 2), not g["novita"]))
                return risultati[:limite] if limite else risultati, list(tolti)
    return [], attivi


def _scarto(gioco: Gioco, filtri: Dict[Text, Any], tolti: Tuple[Text, ...]) -> float:
    """Quanto un gioco si allontana dai filtri tolti (0 = li rispetta)."""
    scarto = 0.0
    if "budget_max" in tolti:
        scarto += max(0.0, catalogo.prezzo_attuale(gioco) - filtri["budget_max"]) / filtri["budget_max"]
    if "durata_max" in tolti:
        scarto += max(0.0, _num(gioco["durata_min"]) - filtri["durata_max"]) / filtri["durata_max"]
    if "eta" in tolti:
        scarto += max(0.0, _num(gioco["eta_min"]) - filtri["eta"]) / max(filtri["eta"], 1)
    if "giocatori" in tolti:
        n = filtri["giocatori"]
        fuori = max(0, _num(gioco["giocatori_min"]) - n, n - _num(gioco["giocatori_max"] or 99))
        scarto += fuori / n
    return scarto


def _scarta_esclusi(giochi: List[Gioco], esclusi: List[Gioco]) -> List[Gioco]:
    nomi = {g["nome"] for g in esclusi}
    return [g for g in giochi if g["nome"] not in nomi]


# ---------------------------------------------------------------------------
# Occasioni: ognuna diventa un filtro sui campi del catalogo
# ---------------------------------------------------------------------------

def _num(valore: Any) -> float:
    return float(valore or 0)


def _durata_eta(gioco: Gioco) -> Text:
    """«partite da circa 30 minuti, si gioca dagli 8 anni», solo con i dati che il catalogo ha."""
    parti = []
    if gioco["durata_min"]:
        parti.append(f"partite da circa {gioco['durata_min']} minuti")
    if gioco["eta_min"]:
        parti.append(f"si gioca {_dai(gioco['eta_min'])}")
    return ", ".join(parti) or _tipo(gioco)


OCCASIONI = {
    "coppia": {
        "titolo": "da giocare in coppia",
        "giocatori": 2,
        "filtro": lambda g: True,
        "categoria": "per due",
        "preferisci": lambda g: g["giocatori_max"] == 2,
        "motivo": lambda g: "pensato apposta per due" if g["giocatori_max"] == 2 else "funziona bene anche in due",
    },
    "famiglia": {
        "titolo": "per tutta la famiglia",
        "filtro": lambda g: _num(g["eta_min"]) <= 10 and _num(g["giocatori_max"]) >= 4,
        "categoria": "famiglia",
        "motivo": _durata_eta,
    },
    "bambini": {
        "titolo": "per bambini",
        "filtro": lambda g: _num(g["eta_min"]) <= 8,
        "categoria": "bambini",
        "motivo": _durata_eta,
    },
    "party": {
        "titolo": "per feste e gruppi numerosi",
        "filtro": lambda g: _num(g["giocatori_max"]) >= 6 and _num(g["durata_min"]) <= 60,
        "categoria": "party",
        "motivo": lambda g: f"fino a {g['giocatori_max']} giocatori"
        + (f" in circa {g['durata_min']} minuti" if g["durata_min"] else ""),
    },
    "solitario": {
        "titolo": "da giocare da soli",
        "giocatori": 1,
        "filtro": lambda g: True,
        "categoria": "solitario",
        "preferisci": lambda g: g["giocatori_max"] == 1,
        "motivo": lambda g: "pensato per il gioco in solitario" if g["giocatori_max"] == 1
        else "ha una modalità in solitario",
    },
    "principianti": {
        "titolo": "per chi inizia",
        # senza un indice di difficoltà: giochi d'ingresso («introduttivi» per il negozio) brevi e per tutti
        "filtro": lambda g: _num(g["durata_min"]) <= 45 and _num(g["eta_min"]) <= 10,
        "categoria": "famiglia",
        "motivo": _durata_eta,
    },
    "esperti": {
        "titolo": "per giocatori esperti",
        "filtro": lambda g: _num(g["durata_min"]) >= 90 or _num(g["eta_min"]) >= 14,
        "categoria": "gestionali",
        "motivo": lambda g: f"partite da circa {g['durata_min']} minuti" if g["durata_min"] else _tipo(g),
    },
}  # type: Dict[Text, Dict[Text, Any]]

# Parole che fanno capire l'occasione (anche quando NLU non ha estratto l'entità)
PAROLE_OCCASIONE = [
    ("coppia", r"coppi\w*|per due|ragazz[ao]|fidanzat\w*|moros\w*|moglie|marito|partner|romantic\w*|san valentino|"
               r"anniversario|io e lei|io e lui|la mia ragazza|il mio ragazzo"),
    ("bambini", r"bambin\w*|bimb\w*|figli\w*|nipotin\w*|piccol\w*|elementari|asilo"),
    ("famiglia", r"famigli\w*|familiar\w*|genitori|parenti|natale in casa"),
    ("party", r"party|fest\w*|amici|gruppo|gruppi|numeros\w*|tante persone|compagnia|cena|"
              r"addio al celibato|addio al nubilato|capodanno|compleanno"),
    ("solitario", r"solitari\w*|da sol[oa]|solo io|in solo|single player|per conto mio"),
    ("principianti", r"principiant\w*|inizia\w*|neofit\w*|novizi\w*|mai giocato|prime armi|"
                     r"primo gioco|facil\w*|semplic\w*|gateway|nonn\w*|non gioca mai"),
    ("esperti", r"espert\w*|hardcore|impegnativ\w*|compless[oi]|pesant\w*|gamer|tost\w*|"
                r"appassionat\w*|incallit\w*|ha gia tutto"),
]


def occasione_da_testo(testo: Optional[Text]) -> Optional[Text]:
    t = catalogo.normalizza(testo or "")
    for occasione, schema in PAROLE_OCCASIONE:
        if re.search(r"\b(" + schema + r")\b", t):
            return occasione
    return None


# Categorie del catalogo che corrispondono a un'occasione
OCCASIONE_DA_CATEGORIA = {
    "per due": "coppia", "solitario": "solitario", "famiglia": "famiglia",
    "bambini": "bambini", "party": "party",
}


def occasione_da_valore(valore: Optional[Text]) -> Optional[Text]:
    """Occasione da un valore di entità («occasione» o «categoria»)."""
    normale = catalogo.normalizza(valore or "")
    if not normale:
        return None
    if normale in OCCASIONI:
        return normale
    return OCCASIONE_DA_CATEGORIA.get(normale) or occasione_da_testo(normale)


def _occasione_da_tracker(tracker: Tracker) -> Optional[Text]:
    for entita in ("occasione", "categoria"):
        occasione = occasione_da_valore(_entita(tracker, entita))
        if occasione:
            return occasione
    giocatori = giocatori_da_testo(_entita(tracker, "num_giocatori"))
    if giocatori == 1:
        return "solitario"
    if giocatori == 2:
        return "coppia"
    if giocatori and giocatori >= 6:
        return "party"
    return occasione_da_testo(_testo_utente(tracker))


def giochi_per_occasione(occasione: Text, extra: Dict[Text, Any],
                         limite: Optional[int] = MAX_PROPOSTE) -> Tuple[List[Gioco], bool]:
    """Giochi adatti all'occasione (più eventuali filtri extra: budget, età, durata, giocatori).

    Restituisce i giochi e True se è stato necessario allentare il profilo dell'occasione.
    """
    profilo = OCCASIONI[occasione]
    filtri = dict(extra)
    if profilo.get("giocatori") and not filtri.get("giocatori"):
        filtri["giocatori"] = profilo["giocatori"]
    candidati = _cerca(limite=None, **filtri)
    categoria = catalogo.normalizza(profilo.get("categoria", ""))
    preferisci = profilo.get("preferisci", lambda g: False)

    def ordine(g: Gioco) -> Tuple:
        ha_categoria = bool(categoria) and categoria in [catalogo.normalizza(c) for c in g["categorie"]]
        return (preferisci(g) or ha_categoria, g["novita"])

    if filtri.get("eta"):
        # con un'età precisa evita i giochi pensati per bimbi molto più piccoli
        vicini = [g for g in candidati if _num(g["eta_min"]) >= filtri["eta"] - 3]
        if len(vicini) >= MIN_PROPOSTE:
            candidati = vicini
    adatti = [g for g in candidati if profilo["filtro"](g)]
    if adatti:
        return sorted(adatti, key=ordine, reverse=True)[:limite], False
    return sorted(candidati, key=ordine, reverse=True)[:limite], True


def _filtri_extra(tracker: Tracker) -> Dict[Text, Any]:
    """Budget, età, durata e giocatori citati nel messaggio, se ci sono."""
    filtri = {}  # type: Dict[Text, Any]
    budget = budget_da_testo(_entita(tracker, "budget"))
    if budget:
        filtri["budget_max"] = budget
    eta = eta_da_testo(_entita(tracker, "eta"))
    if eta:
        filtri["eta"] = eta
    durata = minuti_da_testo(_entita(tracker, "durata"))
    if durata:
        filtri["durata_max"] = durata
    giocatori = giocatori_da_testo(_entita(tracker, "num_giocatori"))
    if giocatori:
        filtri["giocatori"] = giocatori
    # un genere che non è già un'occasione («strategia», «cooperativo»…) diventa un filtro
    genere = _entita(tracker, "categoria")
    if genere and not occasione_da_valore(genere):
        trovato = genere_da_testo(genere)
        if trovato:
            filtri["categoria"] = trovato
    return filtri


# ---------------------------------------------------------------------------
# Somiglianza tra giochi
# ---------------------------------------------------------------------------

def _pesi_rarita() -> Dict[Text, float]:
    """Peso di ogni categoria/meccanica: le più rare dicono di più sul carattere di un gioco."""
    base = [g for g in catalogo.carica_catalogo() if not g["espansione_di"] and _e_gioco(g)]
    frequenza = {}  # type: Dict[Text, int]
    for gioco in base:
        for valore in {catalogo.normalizza(v) for v in gioco["categorie"] + gioco["meccaniche"]}:
            frequenza[valore] = frequenza.get(valore, 0) + 1
    return {v: 1 + math.log(len(base) / n) for v, n in frequenza.items()}


def _jaccard(a: List[Text], b: List[Text], pesi: Dict[Text, float]) -> float:
    """Jaccard pesato: somma dei pesi in comune / somma dei pesi totali."""
    a_n = {catalogo.normalizza(x) for x in a}
    b_n = {catalogo.normalizza(x) for x in b}
    totale = sum(pesi.get(v, 1.0) for v in a_n | b_n)
    return sum(pesi.get(v, 1.0) for v in a_n & b_n) / totale if totale else 0.0


def somiglianza(a: Gioco, b: Gioco, pesi: Optional[Dict[Text, float]] = None) -> float:
    """Punteggio di somiglianza: categorie, meccaniche, giocatori, età e durata."""
    pesi = pesi if pesi is not None else _pesi_rarita()
    punti = 3 * _jaccard(a["categorie"], b["categorie"], pesi) + 3 * _jaccard(a["meccaniche"], b["meccaniche"], pesi)
    min_a, max_a = a["giocatori_min"] or 1, a["giocatori_max"] or 1
    min_b, max_b = b["giocatori_min"] or 1, b["giocatori_max"] or 1
    comuni = min(max_a, max_b) - max(min_a, min_b) + 1
    if comuni > 0:
        punti += comuni / (max(max_a, max_b) - min(min_a, min_b) + 1)
    durata_a, durata_b = _num(a["durata_min"]), _num(b["durata_min"])
    if durata_a and durata_b:
        punti += 0.5 * (1 - min(abs(durata_a - durata_b) / max(durata_a, durata_b), 1))
    punti += 1 - min(abs(_num(a["eta_min"]) - _num(b["eta_min"])) / 8, 1)
    return punti


def _motivo_simile(gioco: Gioco, riferimento: Gioco) -> Text:
    comuni_m = [m for m in gioco["meccaniche"]
                if catalogo.normalizza(m) in {catalogo.normalizza(x) for x in riferimento["meccaniche"]}]
    comuni_c = [c for c in gioco["categorie"]
                if catalogo.normalizza(c) in {catalogo.normalizza(x) for x in riferimento["categorie"]}]
    parti = []
    if comuni_m:
        parti.append(f"anche qui {_elenco(comuni_m[:2])}")
    if comuni_c:
        parti.append(f"stesso genere ({_elenco(comuni_c[:2])})")
    if not parti:
        parti.append(_tipo(gioco))
    durata, durata_rif = _num(gioco["durata_min"]), _num(riferimento["durata_min"])
    if durata and durata_rif:
        if abs(durata - durata_rif) <= 15:
            parti.append("durata simile")
        elif durata > durata_rif:
            parti.append("partite più lunghe")
        else:
            parti.append("partite più brevi")
    return ", ".join(parti[:3])


def giochi_simili(gioco: Gioco, limite: int = 4) -> List[Gioco]:
    """I giochi più simili a `gioco`, esclusi il gioco stesso, il suo gioco base e le espansioni."""
    riferimento = gioco
    if gioco["espansione_di"]:
        riferimento = catalogo.trova_gioco(gioco["espansione_di"]) or gioco
    esclusi = {catalogo.normalizza(gioco["nome"]), catalogo.normalizza(riferimento["nome"])}
    esclusi |= {catalogo.normalizza(e["nome"]) for e in catalogo.espansioni_di(riferimento)}
    candidati = [g for g in _cerca(limite=None)
                 if catalogo.normalizza(g["nome"]) not in esclusi]
    # per i giochi da bambini, se possibile, solo giochi adatti alla stessa età
    if _num(riferimento["eta_min"]) <= 8:
        stessa_eta = [g for g in candidati if _num(g["eta_min"]) <= _num(riferimento["eta_min"]) + 4]
        if len(stessa_eta) >= limite:
            candidati = stessa_eta
    pesi = _pesi_rarita()
    # i titoli della stessa serie («Ticket to Ride Europa» per «Ticket to Ride») vanno in fondo:
    # chi ama un gioco di solito li conosce già
    candidati.sort(key=lambda g: (not _stessa_serie(riferimento, g),
                                  round(somiglianza(riferimento, g, pesi), 3), g["novita"]),
                   reverse=True)
    scelti = []  # type: List[Gioco]
    for g in candidati:
        # al massimo un titolo per serie
        if not any(_stessa_serie(g, s) for s in scelti):
            scelti.append(g)
        if len(scelti) == limite:
            break
    return scelti


def _stessa_serie(a: Gioco, b: Gioco) -> bool:
    nome_a, nome_b = catalogo.normalizza(a["nome"]), catalogo.normalizza(b["nome"])
    corto, lungo = sorted((nome_a, nome_b), key=len)
    return bool(corto) and (lungo == corto or lungo.startswith(corto + " "))


# ---------------------------------------------------------------------------
# Azioni
# ---------------------------------------------------------------------------

PULSANTI_DOPO_CONSIGLIO = [
    {"title": "Un altro consiglio", "payload": "/chiedi_consiglio"},
    {"title": "Sorprendimi", "payload": "/chiedi_sorpresa"},
]


class ActionAvviaConsiglio(Action):
    """Prima del form: azzera i criteri rimasti da richieste vecchie e presenta il form."""

    def name(self) -> Text:
        return "action_avvia_consiglio"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        nuovi = _slot_impostati_nel_turno(tracker)
        eventi = [SlotSet(s, None) for s in SLOT_FORM
                  if s not in nuovi and tracker.get_slot(s) is not None]
        richiesto = tracker.get_slot("requested_slot")
        if richiesto and str(richiesto).startswith("consiglio_"):
            eventi.append(SlotSet("requested_slot", None))

        gia_detti = [s for s in SLOT_FORM if s in nuovi and tracker.get_slot(s) is not None]
        if not gia_detti:
            dispatcher.utter_message(response="utter_consiglio_intro")
        elif len(gia_detti) < len(SLOT_FORM):
            dispatcher.utter_message(response="utter_consiglio_intro_criteri")
        return eventi


class ValidateConsiglioForm(FormValidationAction):

    def name(self) -> Text:
        return "validate_consiglio_form"

    def _valida(self, slot: Text, valore: Any, dispatcher: CollectingDispatcher, tracker: Tracker,
                interpreta: Callable[[Text], Any], valido: Callable[[Any], bool],
                errore: Text, accetta_no: bool = True) -> Dict[Text, Any]:
        if valore is None:
            return {slot: None}
        testo = str(valore)
        if testo == INDIFFERENTE:
            return {slot: INDIFFERENTE}
        richiesto = tracker.get_slot("requested_slot")

        if richiesto != slot:
            # Valore arrivato da un'entità mentre si rispondeva a un'altra domanda (o nel
            # primo messaggio): non sovrascrive una risposta già data e serve un indizio nel testo.
            if richiesto:
                precedente = _valore_precedente(tracker, slot)
                if precedente is not None:
                    return {slot: precedente}
            if not _da_pulsante(tracker) and not CONTESTO[slot].search(
                    catalogo.normalizza(_testo_utente(tracker))):
                return {slot: None}
            risultato = interpreta(testo)
            return {slot: str(risultato) if risultato is not None and valido(risultato) else None}

        risultato = interpreta(testo)
        if risultato is not None and valido(risultato):
            return {slot: str(risultato)}
        if risultato is None and _e_indifferente(testo, tracker, accetta_no):
            return {slot: INDIFFERENTE}
        dispatcher.utter_message(text=errore)
        return {slot: None}

    def validate_consiglio_giocatori(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                     tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        return self._valida(
            "consiglio_giocatori", slot_value, dispatcher, tracker,
            giocatori_da_testo, lambda n: 1 <= n <= 20,
            "Non ho capito in quanti giocherete 🤔 Scrivimi un numero da 1 a 20 (per esempio «4»), "
            "oppure «non importa».",
            accetta_no=False,
        )

    def validate_consiglio_eta(self, slot_value: Any, dispatcher: CollectingDispatcher,
                               tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        return self._valida(
            "consiglio_eta", slot_value, dispatcher, tracker,
            eta_da_testo, lambda n: 1 <= n <= 99,
            "Non ho capito l'età 🤔 Scrivimi quanti anni ha il giocatore più giovane (per esempio «8»), "
            "«adulti» oppure «non importa».",
        )

    def validate_consiglio_durata(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                  tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        if slot_value is not None and tracker.get_slot("requested_slot") == "consiglio_durata" \
                and _e_lunga(str(slot_value)):
            return {"consiglio_durata": INDIFFERENTE}
        return self._valida(
            "consiglio_durata", slot_value, dispatcher, tracker,
            minuti_da_testo, lambda n: 5 <= n <= 600,
            "Non ho capito la durata 🤔 Puoi scrivere per esempio «30 minuti», «un'ora», «veloce» "
            "oppure «non importa».",
        )

    def validate_consiglio_budget(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                  tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        return self._valida(
            "consiglio_budget", slot_value, dispatcher, tracker,
            budget_da_testo, lambda n: 1 <= n <= 2000,
            "Non ho capito il budget 🤔 Scrivimi una cifra in euro (per esempio «30 euro») "
            "oppure «non importa».",
        )

    def validate_consiglio_genere(self, slot_value: Any, dispatcher: CollectingDispatcher,
                                  tracker: Tracker, domain: DomainDict) -> Dict[Text, Any]:
        generi = ", ".join(categorie_principali(8))
        return self._valida(
            "consiglio_genere", slot_value, dispatcher, tracker,
            genere_da_testo, lambda g: bool(g),
            f"Non conosco questo genere 🤔 Puoi scegliere per esempio tra: {generi}. "
            "Oppure rispondi «non importa».",
        )


def filtri_dal_form(tracker: Tracker) -> Dict[Text, Any]:
    """Converte gli slot del form nei parametri di cerca_giochi (None = nessun filtro)."""

    def valore(slot: Text, converti: Callable[[Text], Any]) -> Any:
        grezzo = tracker.get_slot(slot)
        if grezzo is None or grezzo == INDIFFERENTE:
            return None
        return converti(str(grezzo))

    return {
        "giocatori": valore("consiglio_giocatori", giocatori_da_testo),
        "eta": valore("consiglio_eta", eta_da_testo),
        "durata_max": valore("consiglio_durata", minuti_da_testo),
        "budget_max": valore("consiglio_budget", budget_da_testo),
        "categoria": valore("consiglio_genere", lambda g: g),
    }


class ActionConsigliaGiochi(Action):
    """Fine del consiglio_form: propone 3-5 giochi del catalogo e azzera i criteri."""

    def name(self) -> Text:
        return "action_consiglia_giochi"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        filtri = filtri_dal_form(tracker)
        azzera = [SlotSet(s, None) for s in SLOT_FORM]
        riepilogo = _elenco([_testo_filtro(k, v) for k, v in filtri.items() if v is not None])

        giochi, tolti = cerca_con_allentamento(filtri)
        if not giochi:
            dispatcher.utter_message(text="Mi dispiace, al momento non ho giochi disponibili da consigliarti 😕")
            return azzera

        righe = []
        if not tolti:
            intro = f"Ecco cosa ti consiglio ({riepilogo}): 🎲" if riepilogo \
                else "Ecco alcuni giochi che abbiamo disponibili: 🎲"
            righe = [_riga(g, _motivo_filtri(g, filtri)) for g in giochi]
            if len(giochi) < MIN_PROPOSTE:
                # pochi risultati: aggiungo qualche alternativa allentando un filtro
                for nome in ORDINE_ALLENTAMENTO:
                    if filtri.get(nome) is None:
                        continue
                    prova = {k: v for k, v in filtri.items() if k != nome}
                    altri = _scarta_esclusi(_cerca(limite=None, **prova), giochi)
                    if altri:
                        altri = altri[:MIN_PROPOSTE - len(giochi)]
                        righe.append(f"\nSe allarghiamo un po' il filtro {NOMI_FILTRI[nome]}, "
                                     f"ti propongo anche:")
                        righe += [_riga(g, _motivo_filtri(g, prova)) for g in altri]
                        break
        else:
            allentati = _elenco([f"{NOMI_FILTRI[f]} ({filtri[f] if f == 'categoria' else _testo_filtro(f, filtri[f])})"
                                 for f in tolti])
            intro = (f"Non ho trovato giochi che rispettino tutti i criteri 😕 "
                     f"Così ho allentato il filtro {allentati}. Ecco cosa ti propongo:")
            restanti = {k: v for k, v in filtri.items() if k not in tolti}
            righe = [_riga(g, _motivo_filtri(g, restanti)) for g in giochi]

        testo = intro + "\n\n" + "\n".join(righe) + \
            "\n\nVuoi saperne di più su uno di questi? Chiedimi pure prezzo o disponibilità."
        dispatcher.utter_message(text=testo, buttons=PULSANTI_DOPO_CONSIGLIO)
        return azzera


class ActionInterrompiConsiglio(Action):
    """Uscita dal consiglio_form: risposta cortese e criteri azzerati."""

    def name(self) -> Text:
        return "action_interrompi_consiglio"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        dispatcher.utter_message(response="utter_consiglio_interrotto")
        return [SlotSet(s, None) for s in SLOT_FORM]


PULSANTI_OCCASIONE = [
    {"title": "In coppia", "payload": '/chiedi_consiglio_occasione{"occasione":"coppia"}'},
    {"title": "In famiglia", "payload": '/chiedi_consiglio_occasione{"occasione":"famiglia"}'},
    {"title": "Per bambini", "payload": '/chiedi_consiglio_occasione{"occasione":"bambini"}'},
    {"title": "Feste e gruppi numerosi", "payload": '/chiedi_consiglio_occasione{"occasione":"party"}'},
    {"title": "Da solo", "payload": '/chiedi_consiglio_occasione{"occasione":"solitario"}'},
    {"title": "Per principianti", "payload": '/chiedi_consiglio_occasione{"occasione":"principianti"}'},
    {"title": "Per esperti", "payload": '/chiedi_consiglio_occasione{"occasione":"esperti"}'},
]


class ActionConsigliaOccasione(Action):
    """Consigli rapidi per un'occasione (coppia, famiglia, bambini, party, solitario, principianti, esperti)."""

    def name(self) -> Text:
        return "action_consiglia_occasione"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        occasione = _occasione_da_tracker(tracker)
        if not occasione:
            dispatcher.utter_message(text="Per quale occasione cerchi un gioco? 🎲",
                                     buttons=PULSANTI_OCCASIONE, button_type="vertical")
            return []

        extra = _filtri_extra(tracker)
        giochi, allentato = giochi_per_occasione(occasione, extra)
        tolti = []  # type: List[Text]
        if not giochi and extra:
            giochi, tolti = [], list(extra)
            for nome in ORDINE_ALLENTAMENTO:
                if nome in extra:
                    ridotti = {k: v for k, v in extra.items() if k != nome}
                    giochi, allentato = giochi_per_occasione(occasione, ridotti)
                    if giochi:
                        tolti = [nome]
                        break
        if not giochi:
            dispatcher.utter_message(
                text="Mi dispiace, al momento non ho giochi disponibili adatti a questa occasione 😕 "
                     "Prova a chiedermi un consiglio personalizzato: scrivi «consigliami un gioco».")
            return []

        profilo = OCCASIONI[occasione]
        criteri = _elenco([_testo_filtro(k, v) for k, v in extra.items()
                           if k not in tolti and not (k == "giocatori" and v == profilo.get("giocatori"))])
        intro = f"Ecco qualche gioco {profilo['titolo']}" + (f" ({criteri})" if criteri else "") + ":"
        if tolti:
            intro = (f"Con il filtro {NOMI_FILTRI[tolti[0]]} non ho trovato nulla, quindi l'ho tolto. "
                     + intro)
        elif allentato:
            intro = ("Non ho giochi che rispecchino in pieno questa occasione, ma questi sono i più vicini:")

        righe = []
        for g in giochi:
            motivo = profilo["motivo"](g)
            forza = _punti_di_forza(g)
            righe.append(_riga(g, f"{motivo}; {_tipo(g)}" + (f", {forza[0]}" if forza else "")))
        testo = intro + "\n\n" + "\n".join(righe) + \
            "\n\nSe vuoi un consiglio ancora più su misura, scrivimi «consigliami un gioco»."
        dispatcher.utter_message(text=testo)
        return []


class ActionGiochiSimili(Action):
    """Giochi simili a uno citato dall'utente (o all'ultimo gioco di cui si è parlato)."""

    def name(self) -> Text:
        return "action_giochi_simili"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        nome = _entita(tracker, "gioco") or tracker.get_slot("gioco")
        if not nome:
            dispatcher.utter_message(
                text="Dimmi il nome di un gioco che ti è piaciuto e ti trovo qualcosa di simile 🙂 "
                     "Per esempio: «qualcosa come Catan».")
            return []

        gioco = catalogo.trova_gioco(nome)
        if not gioco:
            dispatcher.utter_message(
                text=f"Non trovo «{nome}» nel nostro catalogo 🤔 Prova con un altro titolo, "
                     "oppure scrivi «consigliami un gioco» e ti faccio qualche domanda.")
            return []
        if not _e_gioco(gioco):
            dispatcher.utter_message(
                text=f"{gioco['nome']} è un accessorio, non un gioco 🙂 Dimmi il titolo di un gioco "
                     "che ti è piaciuto e ti trovo qualcosa di simile.")
            return []

        simili = giochi_simili(gioco)
        riferimento = catalogo.trova_gioco(gioco["espansione_di"]) if gioco["espansione_di"] else None
        riferimento = riferimento or gioco
        if not simili:
            dispatcher.utter_message(
                text=f"Per ora non ho altri giochi disponibili abbastanza simili a {riferimento['nome']} 😕")
            return [SlotSet("gioco", gioco["nome"])]

        intro = random.choice([
            f"Se ti piace {riferimento['nome']}, potrebbero piacerti anche:",
            f"Ecco qualche gioco nello spirito di {riferimento['nome']}:",
        ])
        righe = [_riga(g, _motivo_simile(g, riferimento)) for g in simili]
        dispatcher.utter_message(text=intro + "\n\n" + "\n".join(righe))
        return [SlotSet("gioco", gioco["nome"])]


# Destinatari tipici di un regalo -> occasione da usare come filtro
DESTINATARI = [
    ("coppia", r"fidanzat\w*|moros\w*|moglie|marito|partner|compagn[ao]|san valentino|anniversario|"
               r"la mia ragazza|il mio ragazzo|sposi"),
    ("bambini", r"bambin\w*|bimb\w*|figli\w*|nipotin\w*|piccol\w*"),
    ("esperti", r"appassionat\w*|espert\w*|nerd|gamer|incallit\w*|ha gia tutto|gioca tantissimo"),
    ("famiglia", r"famigli\w*|genitori"),
    ("party", r"amici|gruppo|colleghi|ufficio|festa"),
    ("principianti", r"non gioca mai|principiant\w*|nonn\w*|suocer\w*|papa|mamma|zi[oa]|collega"),
    ("solitario", r"da sol[oa]|single|solitari\w*"),
]


class ActionIdeaRegalo(Action):
    """Idee regalo dal catalogo, con budget, età, genere e destinatario se l'utente li indica."""

    def name(self) -> Text:
        return "action_idea_regalo"

    @staticmethod
    def _cerca_regalo(occasione: Optional[Text], filtri: Dict[Text, Any],
                      limite: Optional[int] = 4) -> List[Gioco]:
        if occasione:
            return giochi_per_occasione(occasione, filtri, limite=limite)[0]
        # nessun destinatario: prima le novità e i giochi d'ingresso, che vanno bene per quasi tutti
        candidati = _cerca(limite=None, **filtri)
        candidati.sort(key=lambda g: (g["novita"], "famiglia" in [catalogo.normalizza(c) for c in g["categorie"]]),
                       reverse=True)
        return candidati[:limite] if limite else candidati

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        testo = _testo_utente(tracker)
        normale = catalogo.normalizza(testo)
        extra = _filtri_extra(tracker)
        if "budget_max" not in extra and re.search(r"€|euro\b", testo.lower()):
            budget = budget_da_testo(testo)
            if budget:
                extra["budget_max"] = budget
        if "eta" not in extra and re.search(r"\banni\b", normale):
            eta = eta_da_testo(testo)
            if eta:
                extra["eta"] = eta

        occasione = None  # type: Optional[Text]
        for entita in ("occasione", "categoria"):
            occasione = occasione or occasione_da_valore(_entita(tracker, entita))
        if not occasione:
            for nome, schema in DESTINATARI:
                if re.search(r"\b(" + schema + r")\b", normale):
                    occasione = nome
                    break
        if extra.get("eta") and extra["eta"] < 14:
            occasione = "bambini"

        giochi = self._cerca_regalo(occasione, extra)
        tolto = None  # type: Optional[Text]
        for nome in ("categoria", "budget_max"):
            if giochi or nome not in extra:
                continue
            ridotti = {k: v for k, v in extra.items() if k != nome}
            giochi = self._cerca_regalo(occasione, ridotti, limite=None)
            if nome == "budget_max":
                # budget troppo basso: le idee più economiche
                giochi.sort(key=catalogo.prezzo_attuale)
            giochi = giochi[:4]
            tolto = nome if giochi else None
        if not giochi:
            dispatcher.utter_message(
                text="Mi dispiace, ora non ho giochi disponibili adatti 😕 Una buona alternativa è la "
                     "gift card digitale, da 10 a 150 €, valida 12 mesi e inviata via email.")
            return []

        dettagli = []
        if occasione:
            dettagli.append(OCCASIONI[occasione]["titolo"])
        for nome in ("eta", "categoria", "budget_max"):
            if extra.get(nome) and nome != tolto:
                dettagli.append(_testo_filtro(nome, extra[nome]))
        intro = "🎁 Qualche idea regalo" + (f" ({', '.join(dettagli)})" if dettagli else "") + ":"
        if tolto == "budget_max":
            intro = (f"Con un budget di {catalogo.formatta_prezzo(extra['budget_max'])} non ho trovato "
                     f"nulla di adatto: ecco le idee più economiche che ho.")
        elif tolto == "categoria":
            intro = (f"Con il genere {extra['categoria']} non ho trovato nulla di adatto, "
                     f"quindi l'ho lasciato perdere. " + intro)

        righe = []
        for g in giochi:
            motivo = OCCASIONI[occasione]["motivo"](g) + "; " + _tipo(g) if occasione else _motivo_filtri(g, {})
            righe.append(_riga(g, motivo))
        chiusura = ("\n\nPuoi aggiungere la confezione regalo (2 €) con biglietto personalizzato gratuito. "
                    "Se non sei sicuro dei gusti, c'è anche la gift card digitale da 10 a 150 €.")
        if not occasione and not extra:
            chiusura += ("\nSe mi dici per chi è e quanto vuoi spendere, ti preparo idee più mirate "
                         "(per esempio «regalo per mia nipote di 8 anni sui 30 euro»).")
        dispatcher.utter_message(text=intro + "\n\n" + "\n".join(righe) + chiusura)
        return []


class ActionSorprendimi(Action):
    """Un gioco a caso tra quelli disponibili e ben valutati."""

    def name(self) -> Text:
        return "action_sorprendimi"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        disponibili = _cerca(limite=None)
        if not disponibili:
            dispatcher.utter_message(text="Mi dispiace, al momento non ho giochi disponibili da proporti 😕")
            return []
        buoni = disponibili
        ultimo = catalogo.normalizza(tracker.get_slot("gioco") or "")
        scelta = random.choice([g for g in buoni if catalogo.normalizza(g["nome"]) != ultimo] or buoni)

        intro = random.choice([
            "🎲 Ecco la mia sorpresa per te:",
            "🎲 Lasciati tentare da questo:",
            "🎲 Che ne dici di questo?",
        ])
        motivo = "; ".join([_tipo(scelta)] + _punti_di_forza(scelta)[:1])
        testo = (f"{intro} {scelta['nome']}!\n{scelta['descrizione']}\n\n"
                 f"• {catalogo.descrivi_breve(scelta)}\n   → {motivo[:1].upper() + motivo[1:]}")
        dispatcher.utter_message(text=testo, buttons=[
            {"title": "Un'altra sorpresa", "payload": "/chiedi_sorpresa"},
            {"title": "Consiglio su misura", "payload": "/chiedi_consiglio"},
        ])
        return [SlotSet("gioco", scelta["nome"])]
