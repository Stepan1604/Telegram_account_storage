import logging
from telebot.asyncio_handler_backends import BaseMiddleware

from config import ADMINS

logger = logging.getLogger(__name__)


class AdminMiddleware(BaseMiddleware):
    def __init__(self):
        self.update_types = ["message", "callback_query"]

    async def pre_process(self, message, data):
        user = getattr(message, "from_user", None)
        if user is None or user.id not in ADMINS:
            uid = user.id if user else "?"
            logger.warning(f"[AdminMiddleware] отказ user_id={uid}")
            if hasattr(message, "answer"):
                try:
                    await message.answer("⛔ Нет доступа", show_alert=True)
                except Exception:
                    pass
            return {"cancel": True}
        return data

    async def post_process(self, message, data, exception):
        pass