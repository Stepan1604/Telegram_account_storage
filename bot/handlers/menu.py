import logging

from telebot import types

import database as db
from .. import keyboards as kbd
from ..decorators import admin_required
from ..helpers import safe_edit
from ..telethon_ops import _safe_disconnect

logger = logging.getLogger(__name__)


def register(ctx):
    """
    Регистрирует хендлеры главного меню и команды /start.

    Здесь же реализована очистка подвисших Telethon-клиентов, если
    пользователь прервал флоу добавления аккаунта и вернулся в меню.

    :param ctx: объект Context с общим состоянием.
    """
    bot = ctx.bot

    async def _send_main_menu(chat_id: int, edit_id: int = None) -> None:
        """
        Отправляет или редактирует главное меню с общей статистикой.

        Показывает количество аккаунтов, очищенных, ожидающих очистки,
        мёртвых и избранных. Внизу — список возможностей и
        информация об автоочистке.

        Текст синхронизирован с keyboards.main_menu(): кнопка
        «🗑️ УДАЛИТЬ МЁРТВЫЕ» безвозвратно удаляет аккаунты, а «🔍
        ПРОВЕРКА АККАУНТОВ» различает «точно мёртв» и «не смогли
        проверить», поэтому в подсказке это отражено.

        :param chat_id: ID чата.
        :param edit_id: ID сообщения для редактирования. Если None —
            отправит новое сообщение.
        """
        s = db.get_accounts_stats()

        fav_line = (
            f"  ⭐ Избранных:         {s['favorites']}\n"
            if s.get("favorites") else ""
        )

        text = (
            "🏛 ХРАНИЛИЩЕ TELEGRAM-АККАУНТОВ\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 Общая статистика:\n\n"
            f"  📱 Всего аккаунтов:   {s['total']}\n"
            f"  🧹 Очищено:           {s['cleaned']}\n"
            f"  ⚠️ Ожидают очистки:   {s['uncleaned']}\n"
            f"  ❌ Мёртвых:           {s['dead']}\n"
            f"{fav_line}"
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 Возможности:\n\n"
            "  • 📦 Хранилище — просмотр аккаунтов\n"
            "  • ⭐ Избранное — только избранные\n"
            "  • 🛒 Купить номера — покупка через LZT\n"
            "  • 📊 Отчёты — статистика и экспорт CSV\n"
            "  • 📥 LZT-логи — история покупок\n"
            "  • 🔍 Проверка — проверить сессии\n"
            "  • 🗑️ Удалить мёртвые — безвозвратно\n"
            "  • 🧹 Очистка — убить чужие сессии + чаты\n"
            "  • ➕ Добавить — новый аккаунт\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏰ Автоочистка: 24ч 10сек\n\n"
            "Выберите действие:"
        )

        if edit_id:
            await safe_edit(bot, chat_id, edit_id, text, kbd.main_menu())
        else:
            await bot.send_message(chat_id, text, reply_markup=kbd.main_menu())

    async def _cleanup_pending(uid: int) -> None:
        """
        Закрывает подвисший TelegramClient из незавершённого флоу.

        Если пользователь начал добавление аккаунта, но прервал его
        и вернулся в меню, клиент остаётся в pending_signins и висит.
        Эта функция закрывает его и удаляет запись.

        :param uid: Telegram ID пользователя.
        """
        data = ctx.pending_signins.pop(uid, None)
        if not data:
            return
        client = data.get("client")
        if client:
            try:
                await _safe_disconnect(client)
                logger.info(f"[cleanup_pending] uid={uid}: клиент закрыт")
            except Exception as e:
                logger.warning(f"[cleanup_pending] uid={uid}: {e}")

    @bot.message_handler(commands=["start"])
    @admin_required
    async def cmd_start(message: types.Message):
        """
        Обрабатывает команду /start.

        Закрывает подвисшие клиенты и показывает главное меню.

        :param message: объект Message.
        """
        await _cleanup_pending(message.from_user.id)
        await _send_main_menu(message.chat.id)

    @bot.callback_query_handler(func=lambda c: c.data == "main_menu")
    @admin_required
    async def cb_main_menu(call: types.CallbackQuery):
        """
        Обрабатывает возврат в главное меню по кнопке.

        Закрывает подвисшие клиенты, сбрасывает FSM-состояние
        пользователя и редактирует сообщение на главное меню.

        :param call: callback_query от кнопки "🏠 ГЛАВНОЕ МЕНЮ".
        """
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id

        await _cleanup_pending(uid)

        ctx.clear_user(uid)
        await _send_main_menu(call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "noop")
    @admin_required
    async def cb_noop(call: types.CallbackQuery):
        """
        Обрабатывает нажатие на "пустые" кнопки (например, счётчик страниц).

        Просто подтверждает callback, чтобы Telegram не показывал
        бесконечную загрузку на кнопке.

        :param call: callback_query с data="noop".
        """
        await bot.answer_callback_query(call.id)