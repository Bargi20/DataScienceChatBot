# Azioni del carrello in chat: il carrello vive nello slot `carrello`, una lista di
# {"id": id catalogo, "quantita": n}. Nomi, prezzi e disponibilità vengono sempre dal catalogo.

import difflib
import re
from typing import Any, Dict, List, Optional, Text, Tuple

from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher

from actions.catalogo import (
    carica_catalogo,
    estrai_numero,
    formatta_prezzo,
    gioco_per_id,
    normalizza,
    prezzo_attuale,
    trova_gioco,
)

SPEDIZIONE_STANDARD = 4.90
SOGLIA_GRATIS = 49.0
VICINO_ALLA_SOGLIA = 15.0  # sotto questo scarto avvisiamo che manca poco
URL_CHECKOUT = "www.gigigames.it/checkout"

# codice -> descrizione breve; la logica è in _sconto_codice
CODICI = ("BENVENUTO10", "GIOCA5")
BOTTONI_CARRELLO = [
    {"title": "Vedi il carrello", "payload": "/mostra_carrello"},
    {"title": "Vai al checkout", "payload": "/vai_checkout"},
]

RESET_CONFERMA = SlotSet("carrello_conferma_svuota", False)


# ---------- funzioni di supporto ----------

def _leggi_carrello(tracker: Tracker) -> List[Dict[Text, int]]:
    righe = []
    for riga in tracker.get_slot("carrello") or []:
        try:
            id_gioco, quantita = int(riga["id"]), int(riga["quantita"])
        except (KeyError, TypeError, ValueError):
            continue
        if quantita > 0:
            righe.append({"id": id_gioco, "quantita": quantita})
    return righe


def _quantita_messaggio(tracker: Tracker) -> Optional[int]:
    """La quantità citata nell'ultimo messaggio (non quella vecchia rimasta nello slot)."""
    for valore in tracker.get_latest_entity_values("quantita"):
        numero = estrai_numero(valore)
        if numero is not None:
            return numero
    return None


def _nome_messaggio(tracker: Tracker) -> Optional[Text]:
    """Il gioco citato nell'ultimo messaggio, altrimenti l'ultimo gioco di cui si è parlato."""
    nome = next(tracker.get_latest_entity_values("gioco"), None)
    return nome or tracker.get_slot("gioco")


def _simili(nome: Text, limite: int = 3) -> List[Text]:
    """Nomi del catalogo che somigliano a `nome` (per i suggerimenti)."""
    cercato = normalizza(nome)
    nomi = [g["nome"] for g in carica_catalogo() if not g["espansione_di"]]
    parole = [p for p in cercato.split() if len(p) >= 3]
    trovati = [n for n in nomi if any(p in normalizza(n).split() for p in parole)]
    per_nome = {normalizza(n): n for n in nomi}
    for vicino in difflib.get_close_matches(cercato, list(per_nome), n=limite, cutoff=0.4):
        if per_nome[vicino] not in trovati:
            trovati.append(per_nome[vicino])
    return trovati[:limite]


def _non_trovato(dispatcher: CollectingDispatcher, nome: Text) -> None:
    simili = _simili(nome)
    if simili:
        elenco = "\n".join(f"• {n}" for n in simili)
        dispatcher.utter_message(
            text=f"Non trovo «{nome}» nel catalogo. Intendevi uno di questi?\n{elenco}\n"
                 "Scrivimi il nome esatto e lo metto nel carrello."
        )
    else:
        dispatcher.utter_message(
            text=f"Non trovo «{nome}» nel catalogo. Controlla il nome oppure chiedimi di cercarti un gioco."
        )


def _chiedi_gioco(dispatcher: CollectingDispatcher, azione: Text) -> None:
    dispatcher.utter_message(text=f"Quale gioco vuoi {azione}? Scrivimi il nome.")


def _sistema(righe: List[Dict[Text, int]]) -> Tuple[List[Dict[Text, int]], List[Text]]:
    """Allinea il carrello al catalogo: toglie i giochi spariti o esauriti e limita le quantità."""
    nuove, avvisi = [], []
    for riga in righe:
        gioco = gioco_per_id(riga["id"])
        if gioco is None:
            continue
        disponibili = gioco["disponibilita"] or 0
        if disponibili <= 0:
            avvisi.append(f"{gioco['nome']} nel frattempo è esaurito e l'ho tolto dal carrello.")
        elif riga["quantita"] > disponibili:
            avvisi.append(f"Di {gioco['nome']} sono rimasti solo {disponibili} pezzi: ho aggiornato la quantità.")
            nuove.append({"id": riga["id"], "quantita": disponibili})
        else:
            nuove.append(dict(riga))
    return nuove, avvisi


def _sconto_codice(codice: Optional[Text], subtotale: float) -> Tuple[float, Optional[Text]]:
    """(importo dello sconto, motivo per cui il codice non vale; None se è valido)."""
    if not codice:
        return 0.0, None
    if codice == "BENVENUTO10":
        return round(subtotale * 0.10, 2), None
    if codice == "GIOCA5":
        if subtotale >= 40:
            return min(5.0, subtotale), None
        mancano = formatta_prezzo(40 - subtotale)
        return 0.0, f"GIOCA5 richiede una spesa di almeno 40 €: ti mancano {mancano}."
    return 0.0, f"Il codice {codice} non è un codice valido."


def _calcola(righe: List[Dict[Text, int]], codice: Optional[Text]) -> Dict[Text, Any]:
    linee, subtotale = [], 0.0
    for riga in righe:
        gioco = gioco_per_id(riga["id"])
        prezzo = prezzo_attuale(gioco)
        totale_riga = round(prezzo * riga["quantita"], 2)
        subtotale += totale_riga
        linee.append(f"• {riga['quantita']} × {gioco['nome']}: {formatta_prezzo(prezzo)} → {formatta_prezzo(totale_riga)}")
    subtotale = round(subtotale, 2)
    sconto, motivo = _sconto_codice(codice, subtotale)
    netto = round(subtotale - sconto, 2)
    spedizione = 0.0 if (not righe or netto >= SOGLIA_GRATIS) else SPEDIZIONE_STANDARD
    return {
        "linee": linee, "subtotale": subtotale, "sconto": sconto, "motivo": motivo,
        "netto": netto, "spedizione": spedizione, "totale": round(netto + spedizione, 2),
        "codice_valido": codice if (codice and not motivo) else None,
    }


def _testo_riepilogo(conto: Dict[Text, Any], codice: Optional[Text], titolo: Text) -> Text:
    righe = [titolo, *conto["linee"], "", f"Subtotale: {formatta_prezzo(conto['subtotale'])}"]
    if conto["codice_valido"]:
        etichetta = " (-10%)" if codice == "BENVENUTO10" else ""
        righe.append(f"Sconto {codice}{etichetta}: -{formatta_prezzo(conto['sconto'])}")
    if conto["spedizione"]:
        righe.append(f"Spedizione standard: {formatta_prezzo(conto['spedizione'])}")
    else:
        righe.append("Spedizione standard: gratuita")
    righe.append(f"Totale: {formatta_prezzo(conto['totale'])}")
    mancano = round(SOGLIA_GRATIS - conto["netto"], 2)
    if conto["spedizione"] and 0 < mancano <= VICINO_ALLA_SOGLIA:
        righe.append(f"\nTi mancano {formatta_prezzo(mancano)} per la spedizione gratuita.")
    return "\n".join(righe)


def _eventi_sync(
    tracker: Tracker, righe: List[Dict[Text, int]], conto: Optional[Dict[Text, Any]] = None
) -> List[Dict[Text, Any]]:
    """Eventi che aggiornano carrello e codice (il codice decade se non vale più)."""
    eventi = [SlotSet("carrello", righe), RESET_CONFERMA]
    codice = tracker.get_slot("carrello_codice")
    if codice and (conto or _calcola(righe, codice))["codice_valido"] is None:
        eventi.append(SlotSet("carrello_codice", None))
    return eventi


def _avviso_codice(tracker: Tracker, conto: Dict[Text, Any]) -> Optional[Text]:
    codice = tracker.get_slot("carrello_codice")
    if codice and conto["motivo"]:
        return f"Attenzione: il codice {codice} non è più applicabile. {conto['motivo']}"
    return None


def _cerca_riga(righe: List[Dict[Text, int]], id_gioco: int) -> Optional[Dict[Text, int]]:
    return next((r for r in righe if r["id"] == id_gioco), None)


# ---------- azioni ----------

class ActionAggiungiCarrello(Action):

    def name(self) -> Text:
        return "action_aggiungi_carrello"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        nome = _nome_messaggio(tracker)
        if not nome:
            _chiedi_gioco(dispatcher, "aggiungere al carrello")
            return [RESET_CONFERMA]
        gioco = trova_gioco(nome)
        if gioco is None:
            _non_trovato(dispatcher, nome)
            return [RESET_CONFERMA]

        quantita = _quantita_messaggio(tracker)
        if quantita is None:
            quantita = 1
        if quantita <= 0:
            dispatcher.utter_message(text="La quantità deve essere almeno 1. Quanti ne vuoi?")
            return [RESET_CONFERMA, SlotSet("gioco", gioco["nome"])]

        eventi = [SlotSet("gioco", gioco["nome"])]
        disponibili = gioco["disponibilita"] or 0
        if disponibili <= 0:
            dispatcher.utter_message(
                text=f"{gioco['nome']} al momento è esaurito, quindi non posso metterlo nel carrello. 😕\n"
                     "Posso avvisarti quando torna disponibile: scrivimi «avvisami quando torna disponibile»."
            )
            return eventi + [RESET_CONFERMA]

        righe = _leggi_carrello(tracker)
        riga = _cerca_riga(righe, gioco["id"])
        gia = riga["quantita"] if riga else 0
        aggiunti = min(quantita, disponibili - gia)
        if aggiunti <= 0:
            dispatcher.utter_message(
                text=f"Hai già nel carrello tutti i pezzi disponibili di {gioco['nome']} ({disponibili}), "
                     "non posso aggiungerne altri."
            )
            return eventi + [RESET_CONFERMA]
        if riga:
            riga["quantita"] += aggiunti
        else:
            righe.append({"id": gioco["id"], "quantita": aggiunti})

        prezzo = formatta_prezzo(prezzo_attuale(gioco))
        testo = f"Fatto! Ho aggiunto {aggiunti} × {gioco['nome']} ({prezzo} l'uno) al carrello. 🛒"
        if aggiunti < quantita:
            testo += (f"\nNe volevi {quantita}, ma i pezzi disponibili sono {disponibili}"
                      f"{f' (ne avevi già {gia})' if gia else ''}.")
        conto = _calcola(righe, tracker.get_slot("carrello_codice"))
        testo += f"\nSubtotale attuale: {formatta_prezzo(conto['subtotale'])}."
        dispatcher.utter_message(text=testo, buttons=BOTTONI_CARRELLO)
        return eventi + _eventi_sync(tracker, righe, conto)


class ActionTogliCarrello(Action):

    def name(self) -> Text:
        return "action_togli_carrello"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        righe = _leggi_carrello(tracker)
        if not righe:
            dispatcher.utter_message(text="Il carrello è vuoto, non c'è niente da togliere. 🙂")
            return [RESET_CONFERMA]
        nome = _nome_messaggio(tracker)
        if not nome:
            _chiedi_gioco(dispatcher, "togliere dal carrello")
            return [RESET_CONFERMA]
        gioco = trova_gioco(nome)
        if gioco is None:
            _non_trovato(dispatcher, nome)
            return [RESET_CONFERMA]
        riga = _cerca_riga(righe, gioco["id"])
        eventi = [SlotSet("gioco", gioco["nome"])]
        if riga is None:
            dispatcher.utter_message(text=f"{gioco['nome']} non è nel tuo carrello.")
            return eventi + [RESET_CONFERMA]

        quantita = _quantita_messaggio(tracker)
        if quantita and 0 < quantita < riga["quantita"]:
            riga["quantita"] -= quantita
            testo = f"Ok, ho tolto {quantita} × {gioco['nome']}: ne restano {riga['quantita']} nel carrello."
        else:
            righe.remove(riga)
            testo = f"Ho tolto {gioco['nome']} dal carrello."
        conto = _calcola(righe, tracker.get_slot("carrello_codice"))
        if righe:
            testo += f"\nSubtotale attuale: {formatta_prezzo(conto['subtotale'])}."
        else:
            testo += "\nIl carrello ora è vuoto."
        dispatcher.utter_message(text=testo)
        return eventi + _eventi_sync(tracker, righe, conto)


class ActionCambiaQuantitaCarrello(Action):

    def name(self) -> Text:
        return "action_cambia_quantita_carrello"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        righe = _leggi_carrello(tracker)
        if not righe:
            dispatcher.utter_message(text="Il carrello è vuoto: prima aggiungi un gioco, poi ne cambiamo la quantità.")
            return [RESET_CONFERMA]
        nome = _nome_messaggio(tracker)
        if not nome and len(righe) == 1:
            nome = gioco_per_id(righe[0]["id"])["nome"]  # un solo gioco nel carrello: è chiaro quale
        if not nome:
            _chiedi_gioco(dispatcher, "cambiare di quantità (e quanti ne vuoi)")
            return [RESET_CONFERMA]
        gioco = trova_gioco(nome)
        if gioco is None:
            _non_trovato(dispatcher, nome)
            return [RESET_CONFERMA]
        eventi = [SlotSet("gioco", gioco["nome"])]
        riga = _cerca_riga(righe, gioco["id"])
        if riga is None:
            dispatcher.utter_message(
                text=f"{gioco['nome']} non è nel carrello. Se lo vuoi, scrivimi «aggiungi {gioco['nome']}»."
            )
            return eventi + [RESET_CONFERMA]
        quantita = _quantita_messaggio(tracker)
        if quantita is None:
            dispatcher.utter_message(
                text=f"Hai {riga['quantita']} × {gioco['nome']} nel carrello. Quanti ne vuoi? (scrivi per esempio «ne voglio 3»)"
            )
            return eventi + [RESET_CONFERMA]

        disponibili = gioco["disponibilita"] or 0
        if quantita <= 0:
            righe.remove(riga)
            testo = f"Va bene, ho tolto {gioco['nome']} dal carrello."
        elif quantita > disponibili:
            riga["quantita"] = disponibili
            testo = (f"Di {gioco['nome']} abbiamo solo {disponibili} pezzi: ho portato la quantità a {disponibili}.")
        else:
            riga["quantita"] = quantita
            testo = f"Fatto, ora nel carrello hai {quantita} × {gioco['nome']}."
        righe = [r for r in righe if r["quantita"] > 0]
        conto = _calcola(righe, tracker.get_slot("carrello_codice"))
        if righe:
            testo += f"\nSubtotale attuale: {formatta_prezzo(conto['subtotale'])}."
        dispatcher.utter_message(text=testo)
        return eventi + _eventi_sync(tracker, righe, conto)


class ActionMostraCarrello(Action):

    def name(self) -> Text:
        return "action_mostra_carrello"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        righe, avvisi = _sistema(_leggi_carrello(tracker))
        if not righe:
            testo = "Il tuo carrello è vuoto. 🛒 Dimmi quale gioco ti interessa, oppure chiedimi un consiglio."
            if avvisi:
                testo = "\n".join(avvisi) + "\n" + testo
            dispatcher.utter_message(text=testo)
            return _eventi_sync(tracker, righe)
        codice = tracker.get_slot("carrello_codice")
        conto = _calcola(righe, codice)
        testo = _testo_riepilogo(conto, codice, "Ecco il tuo carrello: 🛒")
        extra = avvisi + ([_avviso_codice(tracker, conto)] if _avviso_codice(tracker, conto) else [])
        if extra:
            testo += "\n\n" + "\n".join(extra)
        dispatcher.utter_message(text=testo, buttons=[BOTTONI_CARRELLO[1]])
        return _eventi_sync(tracker, righe, conto)


class ActionChiediSvuotaCarrello(Action):

    def name(self) -> Text:
        return "action_chiedi_svuota_carrello"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        righe = _leggi_carrello(tracker)
        if not righe:
            dispatcher.utter_message(text="Il carrello è già vuoto. 🙂")
            return [RESET_CONFERMA]
        pezzi = sum(r["quantita"] for r in righe)
        dispatcher.utter_message(
            text=f"Vuoi davvero svuotare il carrello? Ci sono {pezzi} "
                 f"{'pezzo' if pezzi == 1 else 'pezzi'}.",
            buttons=[{"title": "Sì, svuota", "payload": "/affirm"},
                     {"title": "No, tienilo", "payload": "/deny"}],
        )
        return [SlotSet("carrello_conferma_svuota", True)]


class ActionSvuotaCarrello(Action):

    def name(self) -> Text:
        return "action_svuota_carrello"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        # Si svuota solo dopo la conferma esplicita; altrimenti prima si chiede
        if not tracker.get_slot("carrello_conferma_svuota"):
            return ActionChiediSvuotaCarrello().run(dispatcher, tracker, domain)
        dispatcher.utter_message(text="Fatto, ho svuotato il carrello. Quando vuoi ricominciamo! 🛒")
        return [SlotSet("carrello", []), SlotSet("carrello_codice", None), RESET_CONFERMA]


class ActionAnnullaSvuotaCarrello(Action):

    def name(self) -> Text:
        return "action_annulla_svuota_carrello"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(text="Ok, il carrello resta com'è. 👍")
        return [RESET_CONFERMA]


def _estrai_codice(tracker: Tracker) -> Optional[Text]:
    """Il codice citato nell'ultimo messaggio: entità, oppure una parola con lettere e cifre."""
    codice = next(tracker.get_latest_entity_values("codice_sconto"), None)
    if not codice:
        testo = (tracker.latest_message or {}).get("text") or ""
        candidati = [p for p in re.findall(r"[A-Za-z0-9]{4,}", testo)
                     if re.search(r"\d", p) and re.search(r"[A-Za-z]", p)]
        codice = candidati[0] if candidati else None
    return re.sub(r"\s+", "", codice).upper() if codice else None


class ActionApplicaCodiceSconto(Action):

    def name(self) -> Text:
        return "action_applica_codice_sconto"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        codice = _estrai_codice(tracker)
        if not codice:
            dispatcher.utter_message(text="Volentieri! Scrivimi il codice sconto da applicare (per esempio «applica il codice GIOCA5»).")
            return [RESET_CONFERMA]
        righe, avvisi = _sistema(_leggi_carrello(tracker))
        if not righe:
            dispatcher.utter_message(
                text="Il carrello è vuoto: aggiungi prima qualche gioco e poi applico il codice, "
                     "così posso controllare anche le condizioni."
            )
            return _eventi_sync(tracker, righe)
        if codice not in CODICI:
            dispatcher.utter_message(
                text=f"Il codice {codice} non risulta valido. Controlla di averlo scritto bene: "
                     "BENVENUTO10 si riceve iscrivendosi alla newsletter."
            )
            return [RESET_CONFERMA]
        conto = _calcola(righe, codice)
        if conto["motivo"]:
            dispatcher.utter_message(text=f"Non posso applicare il codice per ora. {conto['motivo']}")
            return [RESET_CONFERMA]

        precedente = tracker.get_slot("carrello_codice")
        if precedente and precedente != codice:
            intro = f"Ho sostituito {precedente} con {codice}: vale un solo codice per ordine."
        elif precedente == codice:
            intro = f"Il codice {codice} era già applicato."
        else:
            intro = f"Codice {codice} applicato! 🎉"
        if codice == "BENVENUTO10":
            intro += "\nRicorda che vale solo sul primo ordine: da qui non posso verificarlo, al checkout verrà controllato."
        testo = _testo_riepilogo(conto, codice, intro + "\n")
        if avvisi:
            testo += "\n\n" + "\n".join(avvisi)
        dispatcher.utter_message(text=testo, buttons=[BOTTONI_CARRELLO[1]])
        return [SlotSet("carrello", righe), SlotSet("carrello_codice", codice), RESET_CONFERMA]


class ActionVaiCheckout(Action):

    def name(self) -> Text:
        return "action_vai_checkout"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        righe, avvisi = _sistema(_leggi_carrello(tracker))
        if not righe:
            testo = ("Il carrello è vuoto, quindi non c'è ancora niente da pagare. 🛒\n"
                     "Vuoi che ti cerchi un gioco o che ti consigli qualcosa in base a quanti siete e a cosa vi piace?")
            if avvisi:
                testo = "\n".join(avvisi) + "\n" + testo
            dispatcher.utter_message(text=testo)
            return _eventi_sync(tracker, righe)
        codice = tracker.get_slot("carrello_codice")
        conto = _calcola(righe, codice)
        testo = _testo_riepilogo(conto, codice, "Ecco il riepilogo finale: 🧾")
        extra = avvisi + ([_avviso_codice(tracker, conto)] if _avviso_codice(tracker, conto) else [])
        if extra:
            testo += "\n\n" + "\n".join(extra)
        testo += (f"\n\nIl pagamento si completa sul nostro sito: {URL_CHECKOUT}, "
                  "oppure in negozio, in Via Roma 12 ad Ancona (il ritiro è gratuito).\n"
                  "Nota: il carrello di questa chat non è collegato a quello del sito, "
                  "quindi lì dovrai aggiungere di nuovo i giochi.")
        dispatcher.utter_message(text=testo)
        return _eventi_sync(tracker, righe, conto)
