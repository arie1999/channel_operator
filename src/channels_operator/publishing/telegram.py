"""TelegramPublisher: posts approved drafts to a Telegram channel via the publisher bot token.

The publisher bot is a separate Telegram bot from the editor. It must
be added as an administrator (with post-message permission) to every
channel it publishes to. In a SaaS deployment, each tenant brings their
own publisher-bot token in their channel.yaml.
"""

from telegram import Bot
from telegram.error import TelegramError


class TelegramPublisher:
    def __init__(self, bot_token: str) -> None:
        self.bot = Bot(token=bot_token)

    async def publish(self, *, channel_id: str, text: str) -> int:
        """Post `text` to the channel identified by `channel_id`.

        `channel_id` may be `@channel_username` or a numeric chat ID
        (negative for channels). Returns the Telegram message ID for
        logging. Raises TelegramError on failure (caller decides retry).
        """
        msg = await self.bot.send_message(
            chat_id=channel_id,
            text=text,
            disable_web_page_preview=False,
        )
        return msg.message_id

    async def close(self) -> None:
        """Release the underlying HTTPX client."""
        try:
            await self.bot.shutdown()
        except TelegramError:
            pass
