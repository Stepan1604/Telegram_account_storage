import asyncio
import io
import logging
from datetime import datetime

from telebot import types

import database as db
from .. import keyboards as kbd
from ..decorators import admin_required
from ..helpers import (
    edit_or_send,
    safe_edit,
    country_label,
    country_label_by_code,
    status_label,
    format_remaining,
    build_single_account_csv,
    fmt_phone,
)
from ..telethon_ops import (
    read_login_code,
    get_account_info,
    close_client,
    _get_client,
    request_email_code,
)
from config import AUTO_CLEAN_DELAY, LOGIN_EMAIL

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


def register(ctx):
    """
    Регистрирует хендлеры хранилища аккаунтов.

    Корень хранилища двухуровневый:
      1. Избранные аккаунты — сверху.
      2. "Папки" стран — по одной кнопке на country_code.

    Также включает: карточку аккаунта с профилем из Telegram,
    получение кода входа, переименование, смену статуса, привязку
    email, удаление и экспорт CSV.

    :param ctx: объект Context с общим состоянием.
    """
    bot = ctx.bot

    # ------------------------------------------------------------------- #
    #                    КОРЕНЬ ХРАНИЛИЩА: ИЗБРАННЫЕ + ПАПКИ               #
    # ------------------------------------------------------------------- #

    async def _render_storage_root(chat_id, edit_id=None, call=None):
        """
        Рисует корень хранилища: избранные сверху, затем папки стран.

        :param chat_id: ID чата.
        :param edit_id: ID сообщения для редактирования.
        :param call: callback_query (если вызвано из кнопки).
        """
        favorites = db.get_favorites()
        groups = db.get_country_groups()
        stats = db.get_accounts_stats()

        if not favorites and not groups:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📦 ХРАНИЛИЩЕ АККАУНТОВ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "😔 Хранилище пусто"
            )
            markup = kbd.back_main()
        else:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📦 ХРАНИЛИЩЕ АККАУНТОВ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 Всего: {stats['total']}\n"
                f"🧹 Очищено: {stats['cleaned']}  "
                f"⚠️ Не очищено: {stats['uncleaned']}  "
                f"❌ Мёртвых: {stats['dead']}\n"
                f"⭐ Избранных: {stats.get('favorites', 0)}\n\n"
                "Выберите аккаунт или папку страны:"
            )
            markup = kbd.storage_root_kb(favorites, groups)

        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    async def _render_storage_country(chat_id, code_token, page,
                                      edit_id=None, call=None):
        """
        Рисует страницу "папки" одной страны.

        :param chat_id: ID чата.
        :param code_token: токен страны из callback_data.
        :param page: номер страницы (0-индексация).
        :param edit_id: ID сообщения для редактирования.
        :param call: callback_query (если вызвано из кнопки).
        """
        real_code = kbd._country_token_to_code(code_token)
        accounts = db.get_accounts_by_country(real_code)
        label = country_label_by_code(real_code)

        if not accounts:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{label}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "😔 В этой папке пусто."
            )
            markup = kbd.kb([[kbd.btn("🔙 К СПИСКУ СТРАН", "storage")]])
            if call:
                await edit_or_send(bot, call, text, markup)
            else:
                await safe_edit(bot, chat_id, edit_id, text, markup)
            return

        total = len(accounts)
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        page = max(0, min(page, total_pages - 1))
        slice_ = accounts[page * PAGE_SIZE: page * PAGE_SIZE + PAGE_SIZE]

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{label}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Всего: {total}  |  Страница {page + 1} / {total_pages}\n\n"
            "💡 Избранные — сверху списка.\n\n"
            "Выберите аккаунт:"
        )
        markup = kbd.storage_country_page_kb(slice_, code_token, page,
                                             total_pages, PAGE_SIZE)

        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    async def _render_favorites_list(chat_id, page, edit_id=None, call=None):
        """
        Рисует страницу полного списка избранного.

        :param chat_id: ID чата.
        :param page: номер страницы (0-индексация).
        :param edit_id: ID сообщения для редактирования.
        :param call: callback_query (если вызвано из кнопки).
        """
        favorites = db.get_favorites()

        if not favorites:
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
            if call:
                await edit_or_send(bot, call, text, markup)
            else:
                await safe_edit(bot, chat_id, edit_id, text, markup)
            return

        total = len(favorites)
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        page = max(0, min(page, total_pages - 1))
        slice_ = favorites[page * PAGE_SIZE: page * PAGE_SIZE + PAGE_SIZE]

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⭐ ИЗБРАННОЕ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Всего: {total}  |  Страница {page + 1} / {total_pages}\n\n"
            "Выберите аккаунт:"
        )
        markup = kbd.favorites_page(slice_, page, total_pages, PAGE_SIZE)

        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "storage")
    @admin_required
    async def cb_storage(call: types.CallbackQuery):
        """
        Открывает корень хранилища (избранные + папки стран).

        :param call: callback_query от кнопки "📦 ХРАНИЛИЩЕ".
        """
        await bot.answer_callback_query(call.id)
        await _render_storage_root(call.message.chat.id, call=call)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("storage_country_")
    )
    @admin_required
    async def cb_storage_country(call: types.CallbackQuery):
        """
        Открывает папку страны (с пагинацией).

        :param call: callback_query с data="storage_country_{токен}_{page}".
        """
        await bot.answer_callback_query(call.id)
        try:
            _, _, token, page_str = call.data.split("_")
            page = int(page_str)
        except (ValueError, IndexError):
            page, token = 0, None
        if token is None:
            await _render_storage_root(call.message.chat.id, call=call)
            return
        await _render_storage_country(call.message.chat.id, token, page,
                                      call=call)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("storage_back_")
    )
    @admin_required
    async def cb_storage_back(call: types.CallbackQuery):
        """
        Универсальная кнопка "Назад" с карточки аккаунта.

        :param call: callback_query с data="storage_back_{origin}".
        """
        await bot.answer_callback_query(call.id)
        origin = call.data[len("storage_back_"):]

        if origin.startswith("c:"):
            parts = origin.split(":")
            if len(parts) == 3:
                _, token, page_str = parts
                try:
                    page = int(page_str)
                except ValueError:
                    page = 0
                await _render_storage_country(call.message.chat.id, token,
                                              page, call=call)
                return

        if origin.startswith("f"):
            try:
                page = int(origin[1:])
                await _render_favorites_list(call.message.chat.id, page,
                                             call=call)
                return
            except ValueError:
                pass

        await _render_storage_root(call.message.chat.id, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "favorites")
    @admin_required
    async def cb_favorites(call: types.CallbackQuery):
        """
        Открывает полный список избранных с первой страницы.

        :param call: callback_query от кнопки "⭐ ИЗБРАННОЕ".
        """
        await bot.answer_callback_query(call.id)
        await _render_favorites_list(call.message.chat.id, 0, call=call)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("favorites_page_")
    )
    @admin_required
    async def cb_favorites_page(call: types.CallbackQuery):
        """
        Переключает страницу полного списка избранного.

        :param call: callback_query с data="favorites_page_N".
        """
        await bot.answer_callback_query(call.id)
        try:
            page = int(call.data.replace("favorites_page_", ""))
        except ValueError:
            page = 0
        await _render_favorites_list(call.message.chat.id, page, call=call)

    # ------------------------------------------------------------------- #
    #                            КАРТОЧКА АККАУНТА                         #
    # ------------------------------------------------------------------- #

    async def _render_account(call: types.CallbackQuery, acc_id: int,
                              origin):
        """
        Показывает карточку аккаунта с профилем из Telegram.

        :param call: callback_query.
        :param acc_id: ID аккаунта в БД.
        :param origin: токен навигации для возврата.
        """
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

        placeholder = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 ИНФОРМАЦИЯ ОБ АККАУНТЕ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷 Название: {name_display}{star_mark}\n"
            f"📞 Номер:    {acc.phone}\n"
            f"🌍 Страна:   {country_label(acc.phone)}\n"
            f"📆 Добавлен: {date_str}\n"
            f"🔐 2FA:      {two_fa}\n"
            f"📌 Статус:   {status_label(acc.status)}"
            f"{source_line}\n\n"
            "⏳ Загружаю профиль Telegram..."
        )
        await edit_or_send(bot, call, placeholder, kbd.account_card(acc, origin))

        info = await get_account_info(acc.session, acc.phone)

        if not info["ok"]:
            profile_block = f"  ⚠️ Ошибка: {info['error']}\n"
        else:
            first = info["first_name"] or "—"
            last = info["last_name"] or ""
            full_name = f"{first} {last}".strip() or "—"
            uname = info["username"] or "нет"
            profile_block = (
                f"  🏷 Имя:       {full_name}\n"
                f"  📛 Юзернейм:  {uname}\n"
                f"  🆔 ID:        {info['user_id']}\n\n"
                "💬 АКТИВНОСТЬ\n"
                f"  🔑 Сессий:    {info['sessions']}\n"
                f"  💬 Чатов:     {info['chats']}\n"
                f"  📢 Каналов:   {info['channels']}\n"
                f"  👥 Групп:     {info['groups']}\n"
                f"  📦 Всего:     {info['total_dialogs']}\n"
            )

        final_text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 ИНФОРМАЦИЯ ОБ АККАУНТЕ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷 Название: {name_display}{star_mark}\n"
            f"📞 Номер:    {acc.phone}\n"
            f"🌍 Страна:   {country_label(acc.phone)}\n"
            f"📆 Добавлен: {date_str}\n"
            f"🔐 2FA:      {two_fa}\n"
            f"📌 Статус:   {status_label(acc.status)}"
            f"{source_line}\n\n"
            "👤 ПРОФИЛЬ TELEGRAM\n"
            f"{profile_block}"
            f"{checked_line}"
            f"{cleaned_line}"
            f"{auto_line}"
        )
        await edit_or_send(bot, call, final_text, kbd.account_card(acc, origin))

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_view_"))
    @admin_required
    async def cb_acc_view(call: types.CallbackQuery):
        """
        Открывает карточку аккаунта по кнопке.

        :param call: callback_query с data="acc_view_{id}_{origin}".
        """
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, origin = call.data.split("_")
            acc_id = int(acc_id)
        except (ValueError, IndexError) as e:
            logger.error(f"[cb_acc_view] parse: {e}")
            return
        await _render_account(call, acc_id, origin)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_code_"))
    @admin_required
    async def cb_acc_code(call: types.CallbackQuery):
        """
        Читает код входа из переписки аккаунта с Telegram (777000).

        :param call: callback_query от кнопки "🔑 ПОЛУЧИТЬ КОД".
        """
        await bot.answer_callback_query(call.id, "⏳ Читаю...")
        try:
            _, _, acc_id, origin = call.data.split("_")
            acc_id = int(acc_id)
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
                f"📞 Номер: `{acc.phone}`\n"
                f"🔑 Код:   `{code}`\n"
            )
            if acc.two_fa:
                text += f"🔐 2FA:   `{acc.two_fa}`\n"
            text += "\n💡 Нажмите на код или 2FA, чтобы скопировать."
        else:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ КОД НЕ НАЙДЕН\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                f"Ошибки: {msg}"
            )

        markup = kbd.kb([
            [kbd.btn("🔄 ПОВТОРИТЬ", f"acc_code_{acc_id}_{origin}")],
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{origin}")],
        ])
        await edit_or_send(bot, call, text, markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_fav_"))
    @admin_required
    async def cb_acc_fav(call: types.CallbackQuery):
        """
        Переключает флаг избранного у аккаунта.

        :param call: callback_query от кнопки "☆ В ИЗБРАННОЕ" / "★ УБРАТЬ".
        """
        try:
            _, _, acc_id, origin = call.data.split("_")
            acc_id = int(acc_id)
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
        await _render_account(call, acc_id, origin)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_rename_"))
    @admin_required
    async def cb_acc_rename(call: types.CallbackQuery):
        """
        Запускает FSM переименования аккаунта.

        :param call: callback_query от кнопки "🏷 ДАТЬ НАЗВАНИЕ".
        """
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, origin = call.data.split("_")
            acc_id = int(acc_id)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return

        ctx.user_state[call.from_user.id] = {
            "state": "rename_account",
            "acc_id": acc_id,
            "page": origin,
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

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_email_"))
    @admin_required
    async def cb_acc_email(call: types.CallbackQuery):
        """
        Запускает флоу привязки email к аккаунту из карточки.

        Отправляет код на LOGIN_EMAIL и переводит пользователя в
        FSM-состояние email_verify.

        :param call: callback_query с data="acc_email_{id}_{origin}".
        """
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, origin = call.data.split("_")
            acc_id = int(acc_id)
        except (ValueError, IndexError):
            return

        if not LOGIN_EMAIL:
            await edit_or_send(
                bot, call,
                "❌ LOGIN_EMAIL не задан в config.py\n\n"
                "Добавьте email и перезапустите бота.",
                kbd.kb([
                    [kbd.btn("🔙 К АККАУНТУ",
                             f"acc_view_{acc_id}_{origin}")],
                ]),
            )
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
            "🔎 Запрашиваю код на email...",
        )

        try:
            client = await _get_client(acc.phone, acc.session)
        except Exception as e:
            logger.error(f"[email] connect: {type(e).__name__}: {e}")
            await edit_or_send(
                bot, call,
                f"❌ Не удалось подключиться: {e}",
                kbd.kb([
                    [kbd.btn("🔙 К АККАУНТУ",
                             f"acc_view_{acc_id}_{origin}")],
                ]),
            )
            return

        ok, msg, hash_val, length = await request_email_code(
            client, acc.phone, LOGIN_EMAIL
        )

        if not ok:
            await edit_or_send(
                bot, call,
                f"❌ Не удалось отправить код: {msg}",
                kbd.kb([
                    [kbd.btn("🔄 ПОВТОРИТЬ",
                             f"acc_email_{acc_id}_{origin}")],
                    [kbd.btn("🔙 К АККАУНТУ",
                             f"acc_view_{acc_id}_{origin}")],
                ]),
            )
            return

        ctx.user_state[call.from_user.id] = {
            "state": "email_verify",
            "acc_id": acc_id,
            "origin": origin,
            "email_hash": hash_val,
        }

        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📧 ПРИВЯЗКА ПОЧТЫ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📞 Номер: {acc.phone}\n"
            f"📧 Email: {LOGIN_EMAIL}\n\n"
            f"Введите {length}-значный код из письма:",
            kbd.cancel(),
        )

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_status_set_")
    )
    @admin_required
    async def cb_acc_status_set(call: types.CallbackQuery):
        """
        Устанавливает новый статус аккаунта.

        :param call: callback_query с data="acc_status_set_{id}_{status}_{origin}".
        """
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        try:
            _, _, _, acc_id, new_status, origin = call.data.split("_")
            acc_id = int(acc_id)
        except (ValueError, IndexError):
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return

        db.update_account_status(acc_id, new_status)
        db.log_action(uid, "status", details=new_status, account_id=acc_id)

        if new_status == "cleaned":
            task = ctx.pop_auto_clean_task(acc_id)
            if task and not task.done():
                task.cancel()

        await bot.answer_callback_query(
            call.id, f"✅ Статус: {status_label(new_status)}",
            show_alert=False,
        )
        await _render_account(call, acc_id, origin)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_status_")
    )
    @admin_required
    async def cb_acc_status(call: types.CallbackQuery):
        """
        Открывает меню выбора нового статуса аккаунта.

        :param call: callback_query от кнопки "✏️ ИЗМЕНИТЬ СТАТУС".
        """
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, origin = call.data.split("_")
            acc_id = int(acc_id)
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
        await edit_or_send(bot, call, text, kbd.status_menu(acc_id, origin))

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_del_yes_")
    )
    @admin_required
    async def cb_acc_del_yes(call: types.CallbackQuery):
        """
        Удаляет аккаунт из БД и из бэкапа.

        :param call: callback_query от кнопки "✅ ДА, УДАЛИТЬ".
        """
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        try:
            _, _, _, acc_id, origin = call.data.split("_")
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
        """
        Показывает подтверждение удаления аккаунта.

        :param call: callback_query от кнопки "🗑️ УДАЛИТЬ".
        """
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, origin = call.data.split("_")
            acc_id = int(acc_id)
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
        await edit_or_send(bot, call, text, kbd.confirm_delete(acc_id, origin))

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_export_")
    )
    @admin_required
    async def cb_acc_export(call: types.CallbackQuery):
        """
        Экспортирует аккаунт в CSV.

        :param call: callback_query от кнопки "📤 ЭКСПОРТ ЭТОГО АККАУНТА".
        """
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")
        try:
            _, _, acc_id, origin = call.data.split("_")
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