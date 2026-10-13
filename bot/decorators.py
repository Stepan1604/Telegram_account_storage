import logging
from functools import wraps

from config import ADMINS

logger = logging.getLogger(__name__)


def admin_required(handler):
    """
    Декоратор для проверки прав администратора.

    Пропускает выполнение хендлера только если user_id из from_user
    входит в список ADMINS из config.py. Иначе логирует отказ и,
    если возможно, отправляет пользователю всплывающее уведомление.

    Работает как для message_handler, так и для callback_query_handler.

    :param handler: async-функция хендлера.
    :return: обёрнутая async-функция.
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