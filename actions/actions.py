# This files contains your custom actions which can be used to run
# custom Python code.
#
# See this guide on how to implement these action:
# https://rasa.com/docs/rasa/custom-actions

from datetime import datetime
from typing import Any, Dict, List, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MESI = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]


class ActionTellTime(Action):

    def name(self) -> Text:
        return "action_tell_time"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        adesso = datetime.now()
        dispatcher.utter_message(text=f"Sono le {adesso:%H:%M}. ⏰")

        return []


class ActionTellDate(Action):

    def name(self) -> Text:
        return "action_tell_date"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        oggi = datetime.now()
        giorno = GIORNI[oggi.weekday()]
        mese = MESI[oggi.month - 1]
        dispatcher.utter_message(text=f"Oggi è {giorno} {oggi.day} {mese} {oggi.year}. 📅")

        return []


class ActionNiceToMeet(Action):

    def name(self) -> Text:
        return "action_nice_to_meet"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        nome = tracker.get_slot("nome")
        if nome:
            dispatcher.utter_message(text=f"Piacere di conoscerti, {nome.title()}! 😊 Come posso aiutarti?")
        else:
            dispatcher.utter_message(text="Piacere di conoscerti! 😊 Come posso aiutarti?")

        return []
