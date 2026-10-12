import asyncio
from typing import Any, Callable, Dict, Optional


class Context:
    """
    Общее состояние, разделяемое между всеми модулями handlers.

    Атрибуты:
        bot — экземпляр AsyncTeleBot
        user_state — FSM-состояния (добавление, переименование, покупка)
        pending_signins — временные клиенты/коды при добавлении аккаунта
        pending_names — имена, заданные на шаге 1 добавления
        running_tasks — фоновые задачи (по user_id), защита от двойного запуска
        cancel_flags — флаги отмены для длинных операций
        buy_ctx — контекст покупки LZT (страна, кол-во)
        buy_cancel — флаги отмены закупки LZT
        auto_clean_tasks — задачи автоочистки (по account_id)
        schedule_auto_clean — функция, устанавливается из clean.py
    """

    def __init__(self, bot):
        self.bot = bot

        # FSM и общие словари
        self.user_state: Dict[int, Dict[str, Any]] = {}
        self.pending_signins: Dict[int, Dict[str, Any]] = {}
        self.pending_names: Dict[int, Optional[str]] = {}

        # Задачи / отмены
        self.running_tasks: Dict[int, asyncio.Task] = {}
        self.cancel_flags: Dict[int, bool] = {}

        # Покупка LZT
        self.buy_ctx: Dict[int, Dict[str, Any]] = {}
        self.buy_cancel: Dict[int, bool] = {}

        # Автоочистка
        self.auto_clean_tasks: Dict[int, asyncio.Task] = {}

        # Устанавливается в clean.py (после определения schedule_auto_clean)
        self.schedule_auto_clean: Optional[Callable] = None

    # --------------------------------------------------------------------- #
    #                               УТИЛИТЫ                                  #
    # --------------------------------------------------------------------- #

    def clear_user(self, user_id: int) -> None:
        """Очищает состояние одного пользователя."""
        self.user_state.pop(user_id, None)
        self.pending_signins.pop(user_id, None)
        self.pending_names.pop(user_id, None)
        self.buy_ctx.pop(user_id, None)

    def has_running_task(self, user_id: int) -> bool:
        """Проверяет, есть ли активная фоновая задача у пользователя."""
        task = self.running_tasks.get(user_id)
        return task is not None and not task.done()

    def set_running_task(self, user_id: int, task: asyncio.Task) -> None:
        self.running_tasks[user_id] = task

    def pop_auto_clean_task(self, account_id: int) -> Optional[asyncio.Task]:
        """Забирает и возвращает задачу автоочистки (для отмены)."""
        return self.auto_clean_tasks.pop(account_id, None)