import logging

from telebot import types

import database as db
from .. import keyboards as kbd
from ..decorators import admin_required
from ..helpers import safe_edit

logger = logging.getLogger(__name__)


def register(ctx):
    bot = ctx.bot

    # ═══════════════════════════════════════════════════════════════════ #
    #                          ОТПРАВКА МЕНЮ                               #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _send_main_menu(chat_id: int, edit_id: int = None) -> None:
        s = db.get_accounts_stats()

        fav_line = (
            f"  ⭐ Избранных:         {s['favorites']}\n"
            if s.get("favorites") else ""
        )
        archive_line = (
            f"  📦 В архиве:          {s['archived']}\n"
            if s.get("archived") else ""
        )

        text = (
            "🏛 ХРАНИЛИЩЕ TELEGRAM-АККАУНТОВ\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 Общая статистика:\n\n"
            f"  📱 Всего аккаунтов:   {s['total']}\n"
            f"  🧹 Очищено:           {s['cleaned']}\n"
            f"  ⚠️ Ожидают очистки:   {s['uncleaned']}\n"
            f"  ❌ Мёртвых:           {s['dead']}\n"
            f"{fav_line}{archive_line}"
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 Возможности:\n\n"
            "  • 📦 Хранилище — просмотр аккаунтов\n"
            "  • ⭐ Избранное — только избранные\n"
            "  • 🛒 Купить номера — покупка через LZT\n"
            "  • 📊 Отчёты — статистика и экспорт CSV\n"
            "  • 📥 LZT-логи — история покупок\n"
            "  • 🔍 Проверка — проверка сессий\n"
            "  • 🗑️ Удалить мёртвые — чистка\n"
            "  • 🧹 Очистка — убить чужие сессии + чаты\n"
            "  • ➕ Добавить — новый аккаунт\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏰ Автоочистка: 24ч 10сек\n"
            f"📆 TTL сессий: {_get_ttl_days()} дней\n\n"
            "Выберите действие:"
        )

        if edit_id:
            await safe_edit(bot, chat_id, edit_id, text, kbd.main_menu())
        else:
            await bot.send_message(chat_id, text, reply_markup=kbd.main_menu())

    def _get_ttl_days() -> int:
        """Показывает текущий TTL из config (или 0, если не задан)."""
        try:
            from config import SESSION_TTL_DAYS
            return SESSION_TTL_DAYS or 0
        except Exception:
            return 0

    # ═══════════════════════════════════════════════════════════════════ #
    #                          /start                                       #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.message_handler(commands=["start"])
    @admin_required
    async def cmd_start(message: types.Message):
        await _send_main_menu(message.chat.id)

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ВОЗВРАТ В ГЛАВНОЕ МЕНЮ                          #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "main_menu")
    @admin_required
    async def cb_main_menu(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        # Сбрасываем всё, что могло остаться от незавершённых операций
        ctx.clear_user(call.from_user.id)
        await _send_main_menu(call.message.chat.id, call.message.message_id)

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ПУСТОЙ ОТВЕТ (noop)                             #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "noop")
    @admin_required
    async def cb_noop(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)