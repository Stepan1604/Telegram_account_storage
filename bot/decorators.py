import logging
from functools import wraps

from config import ADMINS

logger = logging.getLogger(__name__)


def admin_required(handler):
    """
    Декоратор для callback_query_handler и message_handler.
    Пропускает только админов из config.ADMINS.

    Использование:
        @bot.callback_query_handler(func=lambda c: c.data == "menu")
        @admin_required
        async def cb_menu(call):
            ...
    """
    @wraps(handler)
    async def wrapper(call_or_message, *args, **kwargs):
        user = getattr(call_or_message, "from_user", None)

        if user is None or user.id not in ADMINS:
            uid = user.id if user else "?"
            username = f"@{user.username}" if user and user.username else ""
            logger.warning(
                f"[admin_required] Отказано в доступе: "
                f"user_id={uid} {username}"
            )
            try:
                if hasattr(call_or_message, "answer"):
                    await call_or_message.answer(
                        "⛔ Нет доступа", show_alert=True
                    )
            except Exception:
                pass
            return

        return await handler(call_or_message, *args, **kwargs)

    return wrapper