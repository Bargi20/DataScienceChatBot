import asyncio
from typing import Optional

from aiogram.types import Message
from aiogram.utils.exceptions import TelegramAPIError

from rasa.core.channels.telegram import TelegramInput, TelegramOutput
from rasa.shared.exceptions import RasaException

# Gli unici tipi di aggiornamento che il connettore sa gestire
ALLOWED_UPDATES = ["message", "edited_message", "callback_query"]


class FixedTelegramInput(TelegramInput):
    """TelegramInput che non riusa la sessione aiohttp chiusa da asyncio.run()
    e ignora gli aggiornamenti senza messaggio (es. my_chat_member)."""

    # Quando un utente avvia, blocca o ferma il bot, Telegram manda un aggiornamento
    # my_chat_member senza messaggio: l'originale va in errore e Telegram lo ripete
    # all'infinito. Così il messaggio mancante viene ignorato con "success".
    @staticmethod
    def _is_user_message(message: Optional[Message]) -> bool:
        return message is not None and message.text is not None

    @staticmethod
    def _is_location(message: Optional[Message]) -> bool:
        return message is not None and message.location is not None

    def get_output_channel(self) -> TelegramOutput:
        channel = TelegramOutput(self.access_token)

        try:
            asyncio.run(
                channel.set_webhook(url=self.webhook_url, allowed_updates=ALLOWED_UPDATES)
            )
        except TelegramAPIError as error:
            raise RasaException(
                "Failed to set channel webhook: " + str(error)
            ) from error

        # La sessione creata dentro asyncio.run() e' legata a un loop ormai chiuso:
        # ne usiamo una nuova, creata al primo uso nel loop del server.
        return TelegramOutput(self.access_token)
