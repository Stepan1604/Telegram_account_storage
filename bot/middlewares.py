import logging

from telebot.asyncio_handler_backends import BaseMiddleware

from config import ADMINS

logger = logging.getLogger(__name__)


class AdminMiddleware(BaseMiddleware):
    """
    Глобальный middleware для проверки прав администратора.

    Перехватывает все входящие message и callback_query до того, как
    они попадут в хендлеры. Если user_id отправителя не входит в
    ADMINS — отменяет обработку и, где возможно, показывает
    всплывающее уведомление "⛔ Нет доступа".

    Работает как единая точка защиты от не-админов, дополняя
    декоратор admin_required.
    """

    def __init__(self):
        """
        Инициализирует middleware и задаёт типы обновлений для перехвата.
        """
        super().__init__()
        self.update_types = ["message", "callback_query"]

    async def pre_process(self, message, data):
        """
        Проверяет пользователя до передачи обновления в хендлер.

        Если пользователь не админ — логирует отказ, пытается
        отправить всплывающее уведомление (для callback_query) и
        возвращает {"cancel": True}, чтобы telebot пропустил хендлер.

        :param message: объект Message или CallbackQuery.
        :param data: словарь с данными обновления.
        :return: data при успехе, {"cancel": True} при отказе.
        """
        user = getattr(message, "from_user", None)

        if user is None or user.id not in ADMINS:
            uid = user.id if user else "?"
            username = f"@{user.username}" if user and user.username else ""
            logger.warning(
                f"[AdminMiddleware] отказ: user_id={uid} {username}"
            )
            if hasattr(message, "answer"):
                try:
                    await message.answer("⛔ Нет доступа", show_alert=True)
                except Exception:
                    pass
            return {"cancel": True}

        return data