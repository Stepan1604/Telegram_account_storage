import logging

from telebot import types
from telebot.asyncio_handler_backends import BaseMiddleware, CancelUpdate

from config import ADMINS

logger = logging.getLogger(__name__)


class AdminMiddleware(BaseMiddleware):
    """
    Глобальный middleware для проверки прав администратора.

    Перехватывает все входящие message и callback_query до хендлеров.
    Если user_id отправителя не входит в ADMINS — отменяет обработку
    (CancelUpdate) и для callback_query показывает всплывающее
    уведомление "⛔ Нет доступа".

    Дополняет декоратор admin_required.
    """

    def __init__(self, bot):
        """
        :param bot: экземпляр AsyncTeleBot (нужен для answer_callback_query).
        """
        super().__init__()
        self.bot = bot
        self.update_types = ["message", "callback_query"]

    async def pre_process(self, message, data):
        """
        Проверяет пользователя до передачи обновления в хендлер.

        :param message: объект Message или CallbackQuery.
        :param data: словарь с данными обновления.
        :return: CancelUpdate() для не-админа, иначе None (пропустить).
        """
        user = getattr(message, "from_user", None)

        if user is None or user.id not in ADMINS:
            uid = user.id if user else "?"
            username = f"@{user.username}" if user and user.username else ""
            logger.warning(
                f"[AdminMiddleware] отказ: user_id={uid} {username}"
            )
            if isinstance(message, types.CallbackQuery):
                try:
                    await self.bot.answer_callback_query(
                        message.id, "⛔ Нет доступа", show_alert=True
                    )
                except Exception:
                    pass
            return CancelUpdate()

    async def post_process(self, message, data, exception=None):
        """
        Вызывается после хендлера. Обязателен: в BaseMiddleware метод
        бросает NotImplementedError.

        Здесь только логируем ошибку хендлера, если она была.
        """
        if exception:
            logger.error(
                f"[handler] {type(exception).__name__}: {exception}"
            )
