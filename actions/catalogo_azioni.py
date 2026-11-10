# Azioni dell'area catalogo: informazioni sui prodotti, elenchi e confronti.
# Ogni dato (prezzi, disponibilità, giocatori...) viene dal catalogo tramite actions/catalogo.py.

import json
import random
from abc import ABCMeta, abstractmethod
from typing import Any, Dict, List, Optional, Text, Tuple

from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher

from actions import catalogo as cat

# Ultima domanda fatta su un gioco: permette «e Carcassonne?» o di rispondere a «di quale gioco?».
SLOT_DOMANDA = "catalogo_domanda"
MAX_ELENCO = 6
SOGLIA_SPEDIZIONE_GRATIS = 49
LIMITE_PAYLOAD = 64  # Telegram accetta al massimo 64 byte nei dati di un pulsante

DOMANDE_GIOCO = {
    "info_gioco": ["Di quale gioco vuoi sapere di più?", "Certo! Di che gioco parliamo?"],
    "chiedi_prezzo": ["Di quale gioco vuoi sapere il prezzo?", "Quale gioco ti interessa? Ti dico subito quanto costa."],
    "chiedi_disponibilita": ["Di quale gioco vuoi controllare la disponibilità?", "Quale gioco cerchi? Controllo subito se c'è."],
    "chiedi_giocatori": ["Di quale gioco vuoi sapere il numero di giocatori?"],
    "chiedi_durata": ["Di quale gioco vuoi sapere la durata?"],
    "chiedi_eta": ["Di quale gioco vuoi sapere l'età consigliata?"],
    "chiedi_difficolta": ["Di quale gioco vuoi sapere quanto è difficile?"],
    "chiedi_editore_autore": ["Di quale gioco vuoi sapere editore e autore?"],
    "chiedi_lingua": ["Di quale gioco vuoi sapere la lingua?"],
    "chiedi_espansioni": ["Per quale gioco cerchi le espansioni?"],
}

ETICHETTE_CATEGORIE = {
    "strategia": "di strategia", "famiglia": "per famiglie", "party": "party", "cooperativo": "cooperativi",
    "carte": "di carte", "bambini": "per bambini", "astratto": "astratti", "deckbuilding": "di deckbuilding",
    "legacy": "legacy", "solitario": "da giocare in solitario", "per due": "per due giocatori",
    "investigativo": "investigativi", "piazzamento lavoratori": "di piazzamento lavoratori",
    "roll and write": "roll & write", "avventura": "di avventura", "bluff": "di bluff", "dadi": "di dadi",
    "accessori": "accessori", "gestionali": "gestionali", "guerra e tattica": "di guerra e tattica",
    "abilita": "di abilità", "classici": "classici",
}
CATEGORIE_PULSANTI = ["strategia", "famiglia", "party", "cooperativo", "bambini", "carte", "per due", "investigativo"]

# Sinonimi troppo generici per cercarli liberamente in una frase.
SINONIMI_NON_NEL_TESTO = {"solo", "due", "gruppo", "gruppi", "amici", "euro", "card", "carta", "coop",
                          "escape", "piazzamento", "compagnia", "piccoli", "ragazzi", "dado", "giallo"}


# ---------------------------------------------------------------------------
# Lettura del messaggio e del contesto
# ---------------------------------------------------------------------------

def _entita(tracker: Tracker, nome: Text) -> List[Text]:
    """Valori dell'entità `nome` nell'ultimo messaggio, in ordine e senza doppioni."""
    entita = sorted(
        (e for e in tracker.latest_message.get("entities", []) if e.get("entity") == nome),
        key=lambda e: e.get("start") or 0,
    )
    valori = []
    for e in entita:
        valore = str(e.get("value") or "").strip()
        if valore and cat.normalizza(valore) not in [cat.normalizza(v) for v in valori]:
            valori.append(valore)
    return valori


def _testo(tracker: Tracker) -> Text:
    return tracker.latest_message.get("text") or ""


def _giochi_citati(tracker: Tracker) -> Tuple[List[Dict[Text, Any]], List[Text]]:
    """Giochi nominati nell'ultimo messaggio e nomi che non corrispondono a nessun gioco."""
    trovati, mancanti = [], []
    for valore in _entita(tracker, "gioco"):
        gioco = cat.trova_gioco(valore)
        if gioco is None:
            mancanti.append(valore)
        elif gioco not in trovati:
            trovati.append(gioco)
    for gioco in cat.trova_giochi_nel_testo(_testo(tracker)):
        if gioco not in trovati:
            trovati.append(gioco)
    return trovati, mancanti


def _gioco_precedente(tracker: Tracker) -> Optional[Dict[Text, Any]]:
    """Il gioco di cui si parlava prima dell'ultimo messaggio dell'utente."""
    utente_visto = False
    for evento in reversed(tracker.events):
        if evento.get("event") == "user":
            utente_visto = True
        elif utente_visto and evento.get("event") == "slot" and evento.get("name") == "gioco":
            gioco = cat.trova_gioco(evento.get("value"))
            if gioco:
                return gioco
    return None


def _ultima_azione_bot(tracker: Tracker) -> Optional[Text]:
    """Nome dell'ultima azione eseguita prima dell'ultimo messaggio dell'utente."""
    utente_visto = False
    for evento in reversed(tracker.events):
        if evento.get("event") == "user":
            utente_visto = True
        elif utente_visto and evento.get("event") == "action" and evento.get("name") != "action_listen":
            return evento.get("name")
    return None


def _categoria_richiesta(tracker: Tracker) -> Optional[Text]:
    for valore in _entita(tracker, "categoria"):
        canonica = cat.categoria_canonica(valore)
        if canonica:
            return canonica
    frase = " " + cat.normalizza(_testo(tracker)) + " "
    voci = [(c, c) for c in cat.CATEGORIE] + [
        (s, c) for s, c in cat.SINONIMI_CATEGORIE.items() if s not in SINONIMI_NON_NEL_TESTO
    ]
    for sinonimo, canonica in sorted(voci, key=lambda v: -len(v[0])):
        if " " + sinonimo + " " in frase:
            return canonica
    return None


def _numero_entita(tracker: Tracker, nome: Text) -> Optional[int]:
    for valore in _entita(tracker, nome):
        numero = cat.estrai_numero(valore)
        if numero:
            return numero
    return None


# ---------------------------------------------------------------------------
# Testi
# ---------------------------------------------------------------------------

def _prezzo_testo(gioco: Dict[Text, Any]) -> Text:
    prezzo = cat.formatta_prezzo(cat.prezzo_attuale(gioco))
    if gioco["prezzo_scontato"]:
        prezzo += f" (in offerta, invece di {cat.formatta_prezzo(gioco['prezzo'])}: -{cat.sconto_percentuale(gioco)}%)"
    return prezzo


def _disponibilita_breve(gioco: Dict[Text, Any]) -> Text:
    pezzi = gioco["disponibilita"] or 0
    if pezzi <= 0:
        return "Esaurito al momento ❌"
    if pezzi <= 3:
        return f"Disponibile, ultimi {pezzi} pezzi ✅"
    return "Disponibile ✅"


def _riga_elenco(gioco: Dict[Text, Any]) -> Text:
    riga = f"• {cat.descrivi_breve(gioco)}"
    return riga + (" – esaurito" if not cat.disponibile(gioco) else "")


def _giocatori_frase(gioco: Dict[Text, Any]) -> Text:
    minimo, massimo = gioco["giocatori_min"], gioco["giocatori_max"]
    if minimo == massimo == 1:
        return f"{gioco['nome']} è un gioco per un solo giocatore."
    if minimo == massimo:
        return f"{gioco['nome']} si gioca in {minimo}, né più né meno."
    return f"{gioco['nome']} si gioca {cat.giocatori_testo(gioco)}."


def _pulsante(titolo: Text, intent: Text, entita: Optional[Dict[Text, Text]] = None) -> Optional[Dict[Text, Text]]:
    payload = "/" + intent + (json.dumps(entita, ensure_ascii=False) if entita else "")
    if len(payload.encode("utf-8")) > LIMITE_PAYLOAD:
        return None
    return {"title": titolo, "payload": payload}


def _pulsanti(voci: List[Optional[Dict[Text, Text]]]) -> List[Dict[Text, Text]]:
    return [v for v in voci if v]


def _non_trovato(dispatcher: CollectingDispatcher, valore: Text, intent: Text) -> None:
    simili = cat.nomi_simili(valore)
    if simili:
        elenco = "\n".join(f"• {nome}" for nome in simili)
        dispatcher.utter_message(
            text=f"Non trovo «{valore}» nel nostro catalogo 🤔 Forse intendevi:\n{elenco}",
            buttons=_pulsanti([_pulsante(nome, intent, {"gioco": nome}) for nome in simili]),
        )
    else:
        dispatcher.utter_message(
            text=f"Mi dispiace, «{valore}» non è nel nostro catalogo. Controlla il nome oppure chiedimi "
                 f"i giochi di una categoria, per esempio «giochi cooperativi» o «giochi per bambini»."
        )


def _alternative_testo(gioco: Dict[Text, Any], **filtri: Any) -> Text:
    simili = cat.giochi_simili(gioco, limite=3, **filtri)
    if not simili:
        return ""
    return "\n".join(f"• {cat.descrivi_breve(g)}" for g in simili)


def _messaggio_esaurito(gioco: Dict[Text, Any]) -> Text:
    testo = (f"Purtroppo {gioco['nome']} al momento è esaurito 😔\n"
             f"Se vuoi ti avviso io quando torna: scrivimi «avvisami quando torna disponibile».")
    alternative = _alternative_testo(gioco)
    if alternative:
        testo += f"\n\nIntanto potrebbe piacerti anche:\n{alternative}"
    return testo


def _accessori_richiesti(tracker: Tracker, gioco: Optional[Dict[Text, Any]]) -> List[Dict[Text, Any]]:
    """Accessori di un tipo nominato nel messaggio («quanto costano le bustine?»)."""
    testo = _testo(tracker)
    tipi = cat.tipi_accessorio(testo)
    if not tipi:
        return []
    if gioco and (cat.e_accessorio(gioco) or any(t in cat.normalizza(gioco["nome"]) for t in tipi)):
        return []
    return cat.accessori(testo)


def _elenco_accessori(dispatcher: CollectingDispatcher, accessori: List[Dict[Text, Any]], intro: Text) -> None:
    righe = [f"• {a['nome']}: {_prezzo_testo(a)}" + ("" if cat.disponibile(a) else " – esaurito") for a in accessori]
    dispatcher.utter_message(text=intro + "\n" + "\n".join(righe))


# ---------------------------------------------------------------------------
# Domande su un singolo prodotto
# ---------------------------------------------------------------------------

class _AzioneGioco(Action, metaclass=ABCMeta):
    """Trova il gioco (nel messaggio o nello slot `gioco`) e poi risponde con `rispondi`.

    È astratta, così il server delle azioni non la registra."""

    intent = ""

    def name(self) -> Text:
        return "action_" + self.intent

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        trovati, mancanti = _giochi_citati(tracker)
        gioco = trovati[0] if trovati else None
        accessori = _accessori_richiesti(tracker, gioco)
        if len(accessori) > 1:
            _elenco_accessori(dispatcher, accessori, "Ecco cosa abbiamo:")
            return [SlotSet(SLOT_DOMANDA, self.intent)]
        if len(accessori) == 1:
            gioco = accessori[0]
        if gioco is None and mancanti:
            _non_trovato(dispatcher, mancanti[0], self.intent)
            return [SlotSet("gioco", None), SlotSet(SLOT_DOMANDA, self.intent)]
        if gioco is None:
            gioco = cat.trova_gioco(tracker.get_slot("gioco"))
        if gioco is None:
            dispatcher.utter_message(text=random.choice(DOMANDE_GIOCO.get(self.intent, DOMANDE_GIOCO["info_gioco"])))
            return [SlotSet(SLOT_DOMANDA, self.intent)]

        eventi = self.rispondi(dispatcher, tracker, gioco) or []
        return [SlotSet("gioco", gioco["nome"]), SlotSet(SLOT_DOMANDA, self.intent)] + eventi

    @abstractmethod
    def rispondi(self, dispatcher: CollectingDispatcher, tracker: Tracker,
                 gioco: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        ...

    @staticmethod
    def _solo_giochi(dispatcher: CollectingDispatcher, gioco: Dict[Text, Any]) -> bool:
        """Per le domande che non hanno senso su un accessorio (giocatori, durata...)."""
        if cat.e_accessorio(gioco):
            dispatcher.utter_message(
                text=f"{gioco['nome']} è un accessorio, non un gioco 🙂 Costa {_prezzo_testo(gioco)}. "
                     f"{_disponibilita_breve(gioco)}"
            )
            return True
        return False


class ActionInfoGioco(_AzioneGioco):
    intent = "info_gioco"

    def rispondi(self, dispatcher, tracker, gioco):
        if cat.e_accessorio(gioco):
            righe = [f"🧩 {gioco['nome']}", gioco["descrizione"], "",
                     f"• Marca: {gioco['editore']}", f"• Prezzo: {_prezzo_testo(gioco)}",
                     f"• {_disponibilita_breve(gioco)}"]
            dispatcher.utter_message(text="\n".join(righe))
            return []
        intestazione = f"🎲 {gioco['nome']}"
        dettagli = ", ".join(str(x) for x in (gioco["editore"], gioco["anno"]) if x)
        if dettagli:
            intestazione += f" ({dettagli})"
        righe = [intestazione, gioco["descrizione"]]
        if gioco["espansione_di"]:
            righe.append(f"È un'espansione di {gioco['espansione_di']}: per giocare serve il gioco base.")
        righe.append("")
        if gioco["giocatori_min"]:
            righe.append(f"• Giocatori: {cat.giocatori_testo(gioco)}")
        if gioco["durata_min"]:
            righe.append(f"• Durata: circa {cat.durata_testo(gioco)}")
        if gioco["eta_min"]:
            righe.append(f"• Età: {cat.eta_testo(gioco)}")
        righe += [f"• Prezzo: {_prezzo_testo(gioco)}", f"• {_disponibilita_breve(gioco)}"]
        espansioni = cat.espansioni_di(gioco)
        if espansioni:
            righe.append(f"\nHa {len(espansioni)} espansion{'e' if len(espansioni) == 1 else 'i'} in catalogo: "
                         f"chiedimi «che espansioni ha?» per vederle.")
        dispatcher.utter_message(text="\n".join(righe))
        return []


class ActionChiediPrezzo(_AzioneGioco):
    intent = "chiedi_prezzo"

    def rispondi(self, dispatcher, tracker, gioco):
        prezzo = cat.formatta_prezzo(cat.prezzo_attuale(gioco))
        if gioco["prezzo_scontato"]:
            testo = (f"{gioco['nome']} è in offerta a {prezzo} invece di {cat.formatta_prezzo(gioco['prezzo'])} "
                     f"(-{cat.sconto_percentuale(gioco)}%) 🔥")
        else:
            testo = random.choice([
                f"{gioco['nome']} costa {prezzo}.",
                f"Il prezzo di {gioco['nome']} è {prezzo}.",
                f"Per {gioco['nome']} il prezzo è {prezzo}.",
            ])
        if gioco["espansione_di"]:
            base = cat.trova_gioco(gioco["espansione_di"])
            if base:
                testo += (f"\nÈ un'espansione: serve il gioco base {base['nome']}, "
                          f"che costa {cat.formatta_prezzo(cat.prezzo_attuale(base))}.")
        if not cat.disponibile(gioco):
            testo += "\nAl momento però è esaurito: se vuoi scrivimi «avvisami quando torna disponibile»."
        elif cat.prezzo_attuale(gioco) > SOGLIA_SPEDIZIONE_GRATIS:
            testo += f"\nSuperando i {SOGLIA_SPEDIZIONE_GRATIS} € la spedizione in Italia è gratuita 🚚"
        dispatcher.utter_message(text=testo)
        return []


class ActionChiediDisponibilita(_AzioneGioco):
    intent = "chiedi_disponibilita"

    def rispondi(self, dispatcher, tracker, gioco):
        pezzi = gioco["disponibilita"] or 0
        if pezzi <= 0:
            dispatcher.utter_message(text=_messaggio_esaurito(gioco))
            return []
        if pezzi <= 3:
            testo = (f"Sì, {gioco['nome']} è disponibile, ma ne restano solo {pezzi} pezzi: "
                     f"se ti interessa, meglio non aspettare troppo!")
        else:
            testo = random.choice([
                f"Sì, {gioco['nome']} è disponibile ✅",
                f"Buone notizie: {gioco['nome']} è disponibile ✅",
                f"Sì, abbiamo {gioco['nome']} in magazzino ✅",
            ])
        testo += (f"\nPrezzo: {_prezzo_testo(gioco)}. Puoi ordinarlo online o ritirarlo gratis "
                  f"in negozio (pronto entro 24 ore).")
        dispatcher.utter_message(text=testo)
        return []


class ActionChiediGiocatori(_AzioneGioco):
    intent = "chiedi_giocatori"

    def rispondi(self, dispatcher, tracker, gioco):
        if self._solo_giochi(dispatcher, gioco):
            return []
        minimo, massimo = gioco["giocatori_min"] or 1, gioco["giocatori_max"] or 99
        numero = _numero_entita(tracker, "num_giocatori")
        espansioni = [e for e in cat.espansioni_di(gioco) if (e["giocatori_max"] or 0) > massimo]
        if numero is None:
            testo = _giocatori_frase(gioco)
            if minimo == 1 and massimo > 1:
                testo += " Ha anche la modalità in solitario."
            for e in espansioni:
                testo += f"\nCon l'espansione «{e['nome']}» si arriva fino a {e['giocatori_max']}."
        elif minimo <= numero <= massimo:
            testo = f"Sì, in {numero} si può giocare a {gioco['nome']} 👍 ({cat.giocatori_testo(gioco)})."
        else:
            utili = [e for e in espansioni if (e["giocatori_min"] or 1) <= numero <= (e["giocatori_max"] or 99)]
            if utili:
                testo = (f"Con il solo gioco base no ({cat.giocatori_testo(gioco)}), ma con l'espansione "
                         f"«{utili[0]['nome']}» si può giocare anche in {numero}.")
            else:
                testo = f"No, {gioco['nome']} si gioca {cat.giocatori_testo(gioco)}: in {numero} non si può."
                alternative = _alternative_testo(gioco, giocatori=numero)
                if alternative:
                    testo += f"\n\nIn {numero} potresti provare:\n{alternative}"
        dispatcher.utter_message(text=testo)
        return []


class ActionChiediDurata(_AzioneGioco):
    intent = "chiedi_durata"

    def rispondi(self, dispatcher, tracker, gioco):
        if self._solo_giochi(dispatcher, gioco):
            return []
        if not gioco["durata_min"]:
            dispatcher.utter_message(text=f"Non ho il dato sulla durata di {gioco['nome']}, mi dispiace.")
            return []
        durata = cat.durata_testo(gioco)
        testo = random.choice([
            f"Una partita a {gioco['nome']} dura circa {durata}.",
            f"{gioco['nome']} dura più o meno {durata} a partita.",
        ])
        if gioco["durata_min"] <= 20:
            testo += " Ottimo per una partita al volo ⏱️"
        elif gioco["durata_min"] >= 120:
            testo += " Meglio tenersi libera la serata!"
        dispatcher.utter_message(text=testo)
        return []


class ActionChiediEta(_AzioneGioco):
    intent = "chiedi_eta"

    def rispondi(self, dispatcher, tracker, gioco):
        if self._solo_giochi(dispatcher, gioco):
            return []
        eta_min = gioco["eta_min"]
        if not eta_min:
            dispatcher.utter_message(text=f"Non ho il dato sull'età consigliata di {gioco['nome']}, mi dispiace.")
            return []
        eta = _numero_entita(tracker, "eta")
        if eta is None:
            testo = f"{gioco['nome']} è consigliato {cat.eta_testo(gioco)} in su."
        elif eta >= eta_min:
            testo = f"Sì, a {eta} anni va bene: {gioco['nome']} è indicato {cat.eta_testo(gioco)} in su 👍"
        else:
            testo = f"{gioco['nome']} è indicato {cat.eta_testo(gioco)}, quindi a {eta} anni è ancora un po' presto."
            alternative = _alternative_testo(gioco, eta=eta)
            if alternative:
                testo += f"\n\nPer {eta} anni potresti guardare:\n{alternative}"
        dispatcher.utter_message(text=testo)
        return []


class ActionChiediDifficolta(_AzioneGioco):
    intent = "chiedi_difficolta"

    def rispondi(self, dispatcher, tracker, gioco):
        if self._solo_giochi(dispatcher, gioco):
            return []
        # il catalogo non ha un indice di difficoltà: diamo età, durata e genere, che aiutano a farsene un'idea
        indizi = []
        if gioco["eta_min"]:
            indizi.append(cat.eta_testo(gioco))
        if gioco["durata_min"]:
            indizi.append(f"con partite da circa {cat.durata_testo(gioco)}")
        testo = f"Non ho un indice di difficoltà per {gioco['nome']}."
        if indizi:
            testo += f" Per farti un'idea: si gioca {', '.join(indizi)}"
            if gioco["categorie"]:
                testo += f" ed è nella categoria {', '.join(gioco['categorie'][:2])}"
            testo += "."
        dispatcher.utter_message(text=testo)
        return []


class ActionChiediEditoreAutore(_AzioneGioco):
    intent = "chiedi_editore_autore"

    def rispondi(self, dispatcher, tracker, gioco):
        if cat.e_accessorio(gioco):
            dispatcher.utter_message(text=f"{gioco['nome']} è un accessorio prodotto da {gioco['editore']}.")
            return []
        parti = []
        if gioco["editore"]:
            parti.append(f"è pubblicato da {gioco['editore']}")
        if gioco["autore"]:
            parti.append(f"è stato ideato da {gioco['autore']}")
        testo = f"{gioco['nome']} " + (" ed ".join(parti) if parti else "non ha editore e autore indicati nel catalogo")
        testo += f" (uscito nel {gioco['anno']})." if gioco["anno"] else "."
        pulsanti = _pulsanti([
            _pulsante(f"Altri giochi {gioco['editore']}", "cerca_editore", {"editore": gioco["editore"]})
        ]) if gioco["editore"] else []
        dispatcher.utter_message(text=testo, buttons=pulsanti)
        return []


class ActionChiediLingua(_AzioneGioco):
    intent = "chiedi_lingua"

    def rispondi(self, dispatcher, tracker, gioco):
        if self._solo_giochi(dispatcher, gioco):
            return []
        lingua = cat.normalizza(gioco["lingua"])
        if lingua == "italiano":
            testo = f"Sì, {gioco['nome']} è in italiano 🇮🇹 (edizione italiana)."
        elif lingua.startswith("indipendente"):
            testo = (f"{gioco['nome']} è indipendente dalla lingua: sui componenti non c'è testo da leggere, "
                     f"quindi la lingua non è un problema.")
        elif lingua:
            testo = f"L'edizione di {gioco['nome']} che vendiamo è in {gioco['lingua']}."
        else:
            testo = f"Non ho informazioni sulla lingua di {gioco['nome']}, mi dispiace."
        dispatcher.utter_message(text=testo)
        return []


class ActionChiediEspansioni(_AzioneGioco):
    intent = "chiedi_espansioni"

    def rispondi(self, dispatcher, tracker, gioco):
        if self._solo_giochi(dispatcher, gioco):
            return []
        if gioco["espansione_di"]:
            base = cat.trova_gioco(gioco["espansione_di"])
            testo = f"{gioco['nome']} è un'espansione di {gioco['espansione_di']}: per usarla serve il gioco base."
            if base:
                testo += f"\n• {cat.descrivi_breve(base)}" + ("" if cat.disponibile(base) else " – esaurito")
                altre = [e for e in cat.espansioni_di(base) if e["nome"] != gioco["nome"]]
                if altre:
                    testo += "\n\nAltre espansioni dello stesso gioco:\n" + "\n".join(_riga_elenco(e) for e in altre)
            dispatcher.utter_message(text=testo)
            return []
        espansioni = cat.espansioni_di(gioco)
        if not espansioni:
            dispatcher.utter_message(text=f"Per {gioco['nome']} al momento non abbiamo espansioni in catalogo.")
            return []
        quante = "un'espansione" if len(espansioni) == 1 else f"{len(espansioni)} espansioni"
        righe = "\n".join(_riga_elenco(e) for e in espansioni)
        dispatcher.utter_message(text=f"Per {gioco['nome']} abbiamo {quante}:\n{righe}")
        return []


AZIONI_GIOCO = {classe.intent: classe for classe in (
    ActionInfoGioco, ActionChiediPrezzo, ActionChiediDisponibilita, ActionChiediGiocatori, ActionChiediDurata,
    ActionChiediEta, ActionChiediDifficolta, ActionChiediEditoreAutore, ActionChiediLingua, ActionChiediEspansioni,
)}


class ActionConfrontaGiochi(Action):

    def name(self) -> Text:
        return "action_confronta_giochi"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        giochi, mancanti = _giochi_citati(tracker)
        if mancanti:
            _non_trovato(dispatcher, mancanti[0], "confronta_giochi")
            return [SlotSet(SLOT_DOMANDA, "confronta_giochi")]
        if len(giochi) == 1:
            # «confrontalo con Azul»: l'altro è il gioco di cui si parlava prima
            precedente = _gioco_precedente(tracker)
            if precedente and precedente["nome"] != giochi[0]["nome"]:
                giochi.insert(0, precedente)
        if len(giochi) < 2:
            testo = "Quali due giochi vuoi confrontare? Scrivimi per esempio «confronta Catan e Carcassonne»."
            if giochi:
                testo = f"Con quale gioco vuoi confrontare {giochi[0]['nome']}?"
            dispatcher.utter_message(text=testo)
            eventi = [SlotSet(SLOT_DOMANDA, "confronta_giochi")]
            return eventi + ([SlotSet("gioco", giochi[0]["nome"])] if giochi else [])
        a, b = giochi[0], giochi[1]
        if cat.e_accessorio(a) or cat.e_accessorio(b):
            dispatcher.utter_message(text=f"Posso confrontare solo i giochi, non gli accessori: "
                                          f"{a['nome']} costa {_prezzo_testo(a)}, {b['nome']} costa {_prezzo_testo(b)}.")
            return [SlotSet("gioco", a["nome"]), SlotSet(SLOT_DOMANDA, "confronta_giochi")]
        dispatcher.utter_message(text=self._confronto(a, b))
        return [SlotSet("gioco", a["nome"]), SlotSet(SLOT_DOMANDA, "confronta_giochi")]

    @staticmethod
    def _confronto(a: Dict[Text, Any], b: Dict[Text, Any]) -> Text:
        def giocatori(g):
            minimo, massimo = g["giocatori_min"], g["giocatori_max"]
            return f"{minimo}" if minimo == massimo else f"{minimo}–{massimo}"

        def durata(g):
            return f"{g['durata_min']} min" if g["durata_min"] else "–"

        righe = [
            f"Ecco il confronto tra {a['nome']} e {b['nome']}:",
            f"• Giocatori: {giocatori(a)} | {giocatori(b)}",
            f"• Durata: {durata(a)} | {durata(b)}",
            f"• Età: {cat.eta_testo(a) or '–'} | {cat.eta_testo(b) or '–'}",
            f"• Prezzo: {cat.formatta_prezzo(cat.prezzo_attuale(a))} | {cat.formatta_prezzo(cat.prezzo_attuale(b))}",
            f"• Disponibile: {'sì' if cat.disponibile(a) else 'no'} | {'sì' if cat.disponibile(b) else 'no'}",
        ]
        pregi = {a["nome"]: [], b["nome"]: []}  # type: Dict[Text, List[Text]]

        def premia(chi: Optional[Dict[Text, Any]], pregio: Text) -> None:
            if chi:
                pregi[chi["nome"]].append(pregio)

        if (a["durata_min"] or 0) != (b["durata_min"] or 0):
            premia(min(a, b, key=lambda g: g["durata_min"] or 0), "più veloce")
        if abs(cat.prezzo_attuale(a) - cat.prezzo_attuale(b)) >= 3:
            premia(min(a, b, key=cat.prezzo_attuale), "più economico")
        if (a["giocatori_max"] or 0) != (b["giocatori_max"] or 0):
            premia(max(a, b, key=lambda g: g["giocatori_max"] or 0), "adatto a gruppi più numerosi")
        frasi = []
        for nome, lista in pregi.items():
            if lista:
                elenco = lista[0] if len(lista) == 1 else ", ".join(lista[:-1]) + " e " + lista[-1]
                frasi.append(f"{nome} è {elenco}")
        if frasi:
            righe.append("\nIn breve: " + "; ".join(frasi) + ".")
        else:
            righe.append("\nSono molto simili: scegli in base al tema che ti piace di più.")
        return "\n".join(righe)


class ActionIndicaGioco(Action):
    """L'utente scrive solo il nome di un gioco: risponde all'ultima domanda fatta sul catalogo
    («quanto costa Catan?» → «e Carcassonne?»), altrimenti mostra la scheda del gioco."""

    def name(self) -> Text:
        return "action_indica_gioco"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        domanda = tracker.get_slot(SLOT_DOMANDA)
        if _ultima_azione_bot(tracker) not in AZIONI_CATALOGO:
            domanda = None
        if domanda == "confronta_giochi":
            return ActionConfrontaGiochi().run(dispatcher, tracker, domain)
        classe = AZIONI_GIOCO.get(domanda or "", ActionInfoGioco)
        return classe().run(dispatcher, tracker, domain)


# ---------------------------------------------------------------------------
# Elenchi
# ---------------------------------------------------------------------------

class ActionCercaCategoria(Action):

    def name(self) -> Text:
        return "action_cerca_categoria"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        categoria = _categoria_richiesta(tracker)
        if categoria is None:
            valori = _entita(tracker, "categoria")
            testo = "Che genere di giochi cerchi?"
            if valori:
                testo = f"Non ho una categoria «{valori[0]}» nel catalogo. Che genere di giochi cerchi?"
            dispatcher.utter_message(
                text=testo + " Puoi scegliere qui sotto oppure scrivermelo (per esempio «giochi astratti» o «roll & write»).",
                buttons=_pulsanti([_pulsante(c.capitalize(), "cerca_categoria", {"categoria": c})
                                   for c in CATEGORIE_PULSANTI]),
            )
            return []
        if categoria == cat.CATEGORIA_ACCESSORI:
            _elenco_accessori(dispatcher, cat.accessori(), "Ecco gli accessori che abbiamo:")
            return [SlotSet("categoria", categoria)]
        giocatori = _numero_entita(tracker, "num_giocatori")
        tutti = cat.giochi_per_categoria(categoria, giocatori=giocatori)
        etichetta = ETICHETTE_CATEGORIE.get(categoria, categoria)
        per_quanti = f" per {giocatori} giocatori" if giocatori else ""
        if not tutti:
            dispatcher.utter_message(text=f"Al momento non abbiamo giochi {etichetta}{per_quanti} in catalogo, mi dispiace.")
            return [SlotSet("categoria", categoria)]
        disponibili = [g for g in tutti if cat.disponibile(g)]
        mostrati = (disponibili or tutti)[:MAX_ELENCO]
        testo = f"Ecco alcuni giochi {etichetta}{per_quanti} che abbiamo:\n"
        testo += "\n".join(_riga_elenco(g) for g in mostrati)
        if len(tutti) > len(mostrati):
            testo += f"\n\nIn tutto ne abbiamo {len(tutti)} in catalogo. Chiedimi pure di uno in particolare!"
        dispatcher.utter_message(text=testo)
        return [SlotSet("categoria", categoria)]


class ActionCercaEditore(Action):

    def name(self) -> Text:
        return "action_cerca_editore"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        valori = _entita(tracker, "editore")
        editore = next((cat.trova_editore(v) for v in valori if cat.trova_editore(v)), None)
        if editore is None and not valori:
            frase = " " + cat.normalizza(_testo(tracker)) + " "
            editore = next((e for e in cat.editori(includi_accessori=True)
                            if " " + cat.normalizza(e) + " " in frase), None)
        if editore is None:
            elenco = ", ".join(cat.editori())
            if valori:
                testo = f"Non abbiamo giochi di «{valori[0]}» in catalogo. Gli editori che trattiamo sono: {elenco}."
            else:
                testo = f"Gli editori che trattiamo sono: {elenco}.\nDi quale vuoi vedere i giochi?"
            principali = sorted(cat.editori(), key=lambda e: -len(cat.giochi_di_editore(e)))[:4]
            dispatcher.utter_message(
                text=testo,
                buttons=_pulsanti([_pulsante(e, "cerca_editore", {"editore": e}) for e in principali]),
            )
            return []
        # prima i giochi base, poi le espansioni
        giochi = sorted(cat.giochi_di_editore(editore), key=lambda g: bool(g["espansione_di"]))
        mostrati = giochi[:MAX_ELENCO + 2]
        testo = f"Di {editore} abbiamo {len(giochi)} prodott{'o' if len(giochi) == 1 else 'i'}"
        testo += ":\n" if len(giochi) == len(mostrati) else f"; eccone alcuni:\n"
        testo += "\n".join(_riga_elenco(g) for g in mostrati)
        dispatcher.utter_message(text=testo)
        return [SlotSet("editore", editore)]


class ActionMostraOfferte(Action):

    def name(self) -> Text:
        return "action_mostra_offerte"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        categoria = _categoria_richiesta(tracker)
        offerte = cat.giochi_in_offerta(categoria=categoria) if categoria else []
        intro = f"Ecco i giochi {ETICHETTE_CATEGORIE.get(categoria or '', categoria)} in offerta 🔥"
        if categoria and not offerte:
            intro = (f"Al momento non ci sono offerte su giochi {ETICHETTE_CATEGORIE.get(categoria, categoria)}, "
                     f"ma ecco tutte le offerte del momento 🔥")
        if not offerte:
            offerte = cat.giochi_in_offerta()
            if not categoria:
                intro = random.choice(["Ecco le offerte del momento 🔥", "Questi sono i prodotti in offerta adesso 🔥"])
        if not offerte:
            dispatcher.utter_message(text="In questo momento non ci sono prodotti in offerta. "
                                          "Iscriviti alla newsletter per sapere subito quando arrivano!")
            return []
        righe = []
        for g in offerte[:MAX_ELENCO + 2]:
            riga = (f"• {g['nome']}: {cat.formatta_prezzo(g['prezzo_scontato'])} invece di "
                    f"{cat.formatta_prezzo(g['prezzo'])} (-{cat.sconto_percentuale(g)}%)")
            righe.append(riga + ("" if cat.disponibile(g) else " – esaurito"))
        if len(offerte) > len(righe):
            righe.append(f"…e altre {len(offerte) - len(righe)}: chiedimi pure di un gioco in particolare.")
        dispatcher.utter_message(text=intro + "\n" + "\n".join(righe))
        return [SlotSet("categoria", categoria)] if categoria else []


class ActionMostraNovita(Action):

    def name(self) -> Text:
        return "action_mostra_novita"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        novita = cat.giochi_novita()
        if not novita:
            dispatcher.utter_message(text="Al momento non ci sono novità in catalogo, ma torna a trovarci presto!")
            return []
        intro = random.choice(["Ecco le ultime novità arrivate ✨", "Queste sono le novità del catalogo ✨"])
        dispatcher.utter_message(text=intro + "\n" + "\n".join(_riga_elenco(g) for g in novita[:MAX_ELENCO + 2]))
        return []


class ActionMostraPiuApprezzati(Action):

    def name(self) -> Text:
        return "action_mostra_piu_apprezzati"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        categoria = _categoria_richiesta(tracker)
        if categoria == cat.CATEGORIA_ACCESSORI:
            categoria = None
        # il catalogo non ha valutazioni: proponiamo le novità disponibili (della categoria, se indicata)
        if categoria:
            giochi = [g for g in cat.giochi_per_categoria(categoria, solo_disponibili=True) if g["novita"]][:5]
        else:
            giochi = cat.giochi_novita(solo_disponibili=True, limite=5)
        etichetta = f" {ETICHETTE_CATEGORIE.get(categoria, categoria)}" if categoria else ""
        testo = "Non ho le valutazioni dei giocatori, quindi non posso fare una classifica. "
        if not giochi:
            dispatcher.utter_message(text=testo + "Se mi dici per quante persone è o che genere ti piace, "
                                                  "ti consiglio io qualcosa! 🎲")
            return [SlotSet("categoria", categoria)] if categoria else []
        righe = [f"{i}. {g['nome']} – {_prezzo_testo(g)}" for i, g in enumerate(giochi, start=1)]
        testo += f"Ecco però le novità{etichetta} disponibili:\n" + "\n".join(righe)
        testo += "\n\nVuoi sapere di più su uno di questi? Scrivimi il nome!"
        dispatcher.utter_message(text=testo)
        return [SlotSet("categoria", categoria)] if categoria else []


class ActionMostraAccessori(Action):

    def name(self) -> Text:
        return "action_mostra_accessori"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        testo = _testo(tracker)
        tipi = cat.tipi_accessorio(testo)
        trovati = cat.accessori(testo)
        if tipi and not trovati:
            trovati = cat.accessori()
            intro = "Questo tipo di accessorio al momento non lo abbiamo, ma ecco cosa c'è:"
        elif tipi:
            intro = "Ecco cosa abbiamo:"
        else:
            intro = "Ecco gli accessori che abbiamo per i tuoi giochi:"
        if not trovati:
            dispatcher.utter_message(text="Al momento non abbiamo accessori in catalogo, mi dispiace.")
            return []
        _elenco_accessori(dispatcher, trovati, intro)
        if len(trovati) == 1:
            return [SlotSet("gioco", trovati[0]["nome"])]
        return []


AZIONI_CATALOGO = {
    "action_info_gioco", "action_chiedi_prezzo", "action_chiedi_disponibilita", "action_chiedi_giocatori",
    "action_chiedi_durata", "action_chiedi_eta", "action_chiedi_difficolta", "action_chiedi_editore_autore",
    "action_chiedi_lingua", "action_chiedi_espansioni", "action_confronta_giochi", "action_indica_gioco",
}
