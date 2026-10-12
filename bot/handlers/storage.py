import asyncio
import io
import logging
from datetime import datetime, timedelta

from telebot import types

import database as db
from .. import keyboards as kbd
from ..decorators import admin_required
from ..helpers import (
    edit_or_send,
    safe_edit,
    country_label,
    status_label,
    format_remaining,
    build_single_account_csv,
    fmt_phone,
)
from ..telethon_ops import (
    read_login_code,
    get_account_info,
    close_client,
    set_session_ttl,
)
from config import SESSION_TTL_DAYS

logger = logging.getLogger(__name__)

PAGE_SIZE = 5
AUTO_CLEAN_DELAY = 24 * 3600 + 10


def register(ctx):
    bot = ctx.bot

    # ═══════════════════════════════════════════════════════════════════ #
    #                          СПИСОК ХРАНИЛИЩА                            #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _render_storage(chat_id, page, edit_id=None, call=None,
                              favorites_only=False):
        accounts = db.get_favorites() if favorites_only else db.get_accounts()

        # ── Пустое хранилище ──
        if not accounts:
            if favorites_only:
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⭐ ИЗБРАННОЕ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "😔 Пока нет избранных аккаунтов."
                )
                markup = kbd.kb([
                    [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
                    [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
                ])
            else:
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "📦 ХРАНИЛИЩЕ АККАУНТОВ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "😔 Хранилище пусто"
                )
                markup = kbd.back_main()

            if call:
                await edit_or_send(bot, call, text, markup)
            else:
                await safe_edit(bot, chat_id, edit_id, text, markup)
            return

        total = len(accounts)
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        page = max(0, min(page, total_pages - 1))
        slice_ = accounts[page * PAGE_SIZE: page * PAGE_SIZE + PAGE_SIZE]

        s = db.get_accounts_stats()

        if favorites_only:
            header = "⭐ ИЗБРАННОЕ"
            extra = "\n💡 Избранные сверху в общем списке."
            markup = kbd.favorites_page(slice_, page, total_pages, PAGE_SIZE)
        else:
            header = "📦 ХРАНИЛИЩЕ АККАУНТОВ"
            extra = ""
            markup = kbd.storage_page(slice_, page, total_pages, PAGE_SIZE)

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{header}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Всего: {total}  |  Страница {page + 1} / {total_pages}\n\n"
            f"🧹 Очищено: {s['cleaned']}\n"
            f"⚠️ Не очищено: {s['uncleaned']}\n"
            f"❌ Мёртвых: {s['dead']}"
            + (f"\n⭐ Избранных: {s['favorites']}" if s.get("favorites") else "")
            + f"\n{extra}\n"
            "Выберите аккаунт:"
        )

        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "storage")
    @admin_required
    async def cb_storage(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        await _render_storage(call.message.chat.id, 0, call=call)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("storage_page_")
    )
    @admin_required
    async def cb_storage_page(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        try:
            page = int(call.data.replace("storage_page_", ""))
        except ValueError:
            page = 0
        await _render_storage(call.message.chat.id, page, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "favorites")
    @admin_required
    async def cb_favorites(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        await _render_storage(call.message.chat.id, 0, call=call,
                              favorites_only=True)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("favorites_page_")
    )
    @admin_required
    async def cb_favorites_page(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        try:
            page = int(call.data.replace("favorites_page_", ""))
        except ValueError:
            page = 0
        await _render_storage(call.message.chat.id, page, call=call,
                              favorites_only=True)

    # ═══════════════════════════════════════════════════════════════════ #
    #                          КАРТОЧКА АККАУНТА                            #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _render_account(call: types.CallbackQuery, acc_id: int,
                              page: int):
        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден",
                               kbd.back_main())
            return

        two_fa = f"«{acc.two_fa}»" if acc.two_fa else "не задан"
        date_str = (
            acc.registered_at.strftime("%d.%m.%Y в %H:%M")
            if acc.registered_at else "—"
        )

        auto_line = ""
        if acc.status == "uncleaned" and acc.registered_at:
            elapsed = (datetime.now() - acc.registered_at).total_seconds()
            remaining = AUTO_CLEAN_DELAY - elapsed
            auto_line = (
                f"\n⏰ Автоочистка: через {format_remaining(remaining)}"
            )

        source_line = ""
        if acc.bought_from and acc.bought_from != "manual":
            price_str = f" за {acc.price} ₽" if acc.price else ""
            item_str = (
                f" (лот #{acc.lzt_item_id})" if acc.lzt_item_id else ""
            )
            source_line = (
                f"\n🛒 Источник: {acc.bought_from}{price_str}{item_str}"
            )
        elif acc.price:
            source_line = f"\n💰 Цена: {acc.price} ₽"

        checked_line = ""
        if acc.last_checked_at:
            checked_line = (
                f"\n🕓 Проверен: "
                f"{acc.last_checked_at.strftime('%d.%m %H:%M')}"
            )
        cleaned_line = ""
        if acc.last_cleaned_at:
            cleaned_line = (
                f"\n🧹 Очищен: "
                f"{acc.last_cleaned_at.strftime('%d.%m %H:%M')}"
            )

        name_display = acc.name or acc.phone
        star_mark = " ⭐" if (acc.favorite or 0) == 1 else ""

        # Базовая карточка (без Telegram-инфо) — показываем сразу
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 ИНФОРМАЦИЯ ОБ АККАУНТЕ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷 Название: {name_display}{star_mark}\n"
            f"📞 Номер:    {acc.phone}\n"
            f"🌍 Страна:   {country_label(acc.phone)}\n"
            f"📆 Добавлен: {date_str}\n"
            f"🔐 2FA:      {two_fa}\n"
            f"📌 Статус:   {status_label(acc.status)}"
            f"{source_line}"
            f"{checked_line}"
            f"{cleaned_line}"
            f"{auto_line}\n\n"
            "⏳ Загружаю профиль Telegram...\n\n"
            f"📆 TTL сессии: {SESSION_TTL_DAYS} дней"
        )
        await edit_or_send(bot, call, text, kbd.account_card(acc, page))

        # Подгружаем детали из Telegram
        info = await get_account_info(acc.session, acc.phone)

        if not info["ok"]:
            text = text.replace(
                "⏳ Загружаю профиль Telegram...\n\n", ""
            )
            text += f"\n\n⚠️ Профиль: {info['error']}"
        else:
            first = info["first_name"] or "—"
            last = info["last_name"] or ""
            full_name = f"{first} {last}".strip() or "—"
            uname = info["username"] or "нет"

            text = text.replace(
                "⏳ Загружаю профиль Telegram...\n\n", ""
            )
            text = text.replace(
                f"📌 Статус:   {status_label(acc.status)}",
                f"📌 Статус:   {status_label(acc.status)}\n\n"
                "👤 ПРОФИЛЬ TELEGRAM\n"
                f"  🏷 Имя:       {full_name}\n"
                f"  📛 Юзернейм:  {uname}\n"
                f"  🆔 ID:        {info['user_id']}\n\n"
                "💬 АКТИВНОСТЬ\n"
                f"  🔑 Сессий:    {info['sessions']}\n"
                f"  💬 Чатов:     {info['chats']}\n"
                f"  📢 Каналов:   {info['channels']}\n"
                f"  👥 Групп:     {info['groups']}\n"
                f"  📦 Всего:     {info['total_dialogs']}"
            )

        await edit_or_send(bot, call, text, kbd.account_card(acc, page))

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_view_"))
    @admin_required
    async def cb_acc_view(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError) as e:
            logger.error(f"[cb_acc_view] parse: {e}")
            return
        await _render_account(call, acc_id, page)

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ПОЛУЧИТЬ КОД ВХОДА                             #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_code_"))
    @admin_required
    async def cb_acc_code(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "⏳ Читаю...")
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return

        await edit_or_send(
            bot, call,
            "⏳ ПОДКЛЮЧАЮСЬ К АККАУНТУ...\n\n"
            f"🏷 {acc.name or acc.phone}\n"
            f"📞 {acc.phone}\n\n"
            "🔎 Читаю переписку с сервисным Telegram...",
        )

        code, msg = await read_login_code(acc.session, acc.phone)

        if code:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🔑 КОД ВХОДА ПОЛУЧЕН\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 Номер: {acc.phone}\n"
                f"🔑 Код:   `{code}`\n"
            )
            if acc.two_fa:
                text += f"🔐 2FA:   `{acc.two_fa}`\n"
            text += "\n💡 Введите код в Telegram."
        else:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ КОД НЕ НАЙДЕН\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                f"Ошибки: {msg}"
            )

        markup = kbd.kb([
            [kbd.btn("🔄 ПОВТОРИТЬ", f"acc_code_{acc_id}_{page}")],
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{page}")],
        ])
        await edit_or_send(bot, call, text, markup)

    # ═══════════════════════════════════════════════════════════════════ #
    #                          ИЗБРАННОЕ                                    #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_fav_"))
    @admin_required
    async def cb_acc_fav(call: types.CallbackQuery):
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if not acc:
            await bot.answer_callback_query(call.id, "❌ Аккаунт не найден",
                                            show_alert=True)
            return

        now_fav = db.toggle_favorite(acc_id)
        db.log_action(call.from_user.id, "favorite",
                      details=f"{'on' if now_fav else 'off'} {acc.phone}",
                      account_id=acc_id)

        await bot.answer_callback_query(
            call.id,
            "⭐ Добавлен в избранное" if now_fav else "☆ Убран из избранного",
            show_alert=False,
        )
        await _render_account(call, acc_id, page)

    # ═══════════════════════════════════════════════════════════════════ #
    #                        ДАТЬ НАЗВАНИЕ                                  #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_rename_"))
    @admin_required
    async def cb_acc_rename(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return

        ctx.user_state[call.from_user.id] = {
            "state": "rename_account",
            "acc_id": acc_id,
            "page": page,
        }

        current = acc.name or acc.phone
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏷 ДАТЬ НАЗВАНИЕ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📞 Номер: {acc.phone}\n"
            f"🏷 Текущее: {current}\n\n"
            "💡 Отправьте новое название одним сообщением.\n"
            "Максимум 64 символа.\n\n"
            "Сбросить на номер — отправьте «-»."
        )
        await edit_or_send(bot, call, text, kbd.cancel())

    # ═══════════════════════════════════════════════════════════════════ #
    #                        СМЕНА СТАТУСА                                  #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_status_set_")
    )
    @admin_required
    async def cb_acc_status_set(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        try:
            _, _, _, acc_id, new_status, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return

        db.update_account_status(acc_id, new_status)
        db.log_action(uid, "status", details=new_status, account_id=acc_id)

        # Смена на "cleaned" — отменяем автоочистку
        if new_status == "cleaned":
            task = ctx.pop_auto_clean_task(acc_id)
            if task and not task.done():
                task.cancel()

        await bot.answer_callback_query(
            call.id, f"✅ Статус: {status_label(new_status)}",
            show_alert=False,
        )
        await _render_account(call, acc_id, page)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_status_")
    )
    @admin_required
    async def cb_acc_status(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✏️ ИЗМЕНЕНИЕ СТАТУСА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷 {acc.name or acc.phone}\n"
            f"📞 {acc.phone}\n"
            f"📌 Текущий: {status_label(acc.status)}\n\n"
            "💡 Выберите новый статус:"
        )
        await edit_or_send(bot, call, text, kbd.status_menu(acc_id, page))

    # ═══════════════════════════════════════════════════════════════════ #
    #                        УСТАНОВИТЬ TTL 12 МЕС                          #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_ttl_"))
    @admin_required
    async def cb_acc_ttl(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "⏳ Устанавливаю TTL...")
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return

        await edit_or_send(
            bot, call,
            "⏳ Устанавливаю TTL...\n\n"
            f"📞 {acc.phone}\n"
            f"📆 Срок: {SESSION_TTL_DAYS} дней (12 месяцев)",
        )

        # Берём клиент через кэш telethon_ops
        from ..telethon_ops import _get_client

        try:
            c = await _get_client(acc.phone, acc.session)
            if not await c.is_user_authorized():
                await edit_or_send(
                    bot, call,
                    "❌ Сессия не авторизована.\n\n"
                    f"📞 {acc.phone}",
                    kbd.back_main(),
                )
                return

            ok, msg = await set_session_ttl(c, SESSION_TTL_DAYS)

            if ok:
                db.log_action(
                    call.from_user.id, "set_ttl",
                    details=f"{acc.phone} → {SESSION_TTL_DAYS}д",
                    account_id=acc.id,
                )
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ TTL УСТАНОВЛЕН\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📞 {acc.phone}\n"
                    f"📆 Срок: {SESSION_TTL_DAYS} дней (12 месяцев)\n\n"
                    "💡 Если аккаунт не заходить 12 месяцев —\n"
                    "Telegram автоматически удалит его."
                )
            else:
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "❌ НЕ УДАЛОСЬ УСТАНОВИТЬ TTL\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📞 {acc.phone}\n\n"
                    f"Ошибки: {msg}"
                )
        except Exception as e:
            logger.error(f"[cb_acc_ttl] {type(e).__name__}: {e}")
            text = f"❌ Ошибка: {type(e).__name__}"

        markup = kbd.kb([
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{page}")],
            [kbd.btn("🏠 МЕНЮ", "main_menu")],
        ])
        await edit_or_send(bot, call, text, markup)

    # ═══════════════════════════════════════════════════════════════════ #
    #                        УДАЛЕНИЕ АККАУНТА                              #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_del_yes_")
    )
    @admin_required
    async def cb_acc_del_yes(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        try:
            _, _, _, acc_id, page = call.data.split("_")
            acc_id = int(acc_id)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if acc:
            task = ctx.pop_auto_clean_task(acc_id)
            if task and not task.done():
                task.cancel()
            await close_client(acc.phone)
            db.delete_account(acc_id)
            db.log_action(uid, "delete", details=acc.phone, account_id=acc_id)
            logger.info(f"[delete] {acc.phone}: удалён")

        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ АККАУНТ УДАЛЁН\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🗑️ Аккаунт убран из хранилища.",
            kbd.kb([[kbd.btn("🔙 В ХРАНИЛИЩЕ", "storage")]]),
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_del_"))
    @admin_required
    async def cb_acc_del(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Не найден")
            return

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ ПОДТВЕРДИТЕ УДАЛЕНИЕ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷 {acc.name or acc.phone}\n"
            f"📞 {acc.phone}\n"
            f"🌍 {country_label(acc.phone)}\n\n"
            "❗ Это действие нельзя отменить."
        )
        await edit_or_send(bot, call, text, kbd.confirm_delete(acc_id, page))

    # ═══════════════════════════════════════════════════════════════════ #
    #                    ЭКСПОРТ ОДНОГО АККАУНТА                            #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_export_")
    )
    @admin_required
    async def cb_acc_export(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id = int(acc_id)
        except (ValueError, IndexError) as e:
            logger.error(f"[acc_export] parse: {e}")
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден",
                               kbd.back_main())
            return

        try:
            data = build_single_account_csv(acc)
        except Exception as e:
            logger.error(f"[acc_export] build: {type(e).__name__}: {e}")
            await edit_or_send(bot, call, f"❌ Ошибка: {type(e).__name__}",
                               kbd.back_main())
            return

        safe_phone = acc.phone.lstrip("+") if acc.phone else str(acc.id)
        fname = f"account_{safe_phone}.csv"

        caption = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📤 ЭКСПОРТ АККАУНТА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷 {acc.name or acc.phone}\n"
            f"📞 {acc.phone}\n"
            f"📄 {fname}\n\n"
            "💡 Все поля + session внутри."
        )

        try:
            await bot.send_document(
                call.message.chat.id,
                document=types.InputFile(io.BytesIO(data), file_name=fname),
                caption=caption,
            )
            db.log_action(call.from_user.id, "export_csv_one",
                          details=acc.phone, account_id=acc.id)
        except Exception as e:
            logger.error(f"[acc_export] send: {type(e).__name__}: {e}")
            await edit_or_send(bot, call,
                               f"❌ Не отправлено: {type(e).__name__}",
                               kbd.back_main())