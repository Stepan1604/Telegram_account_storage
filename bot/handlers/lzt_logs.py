import asyncio
import io
import logging
from datetime import datetime

from telebot import types

import database as db
from .. import keyboards as kbd
from .. import lzt
from ..decorators import admin_required
from ..helpers import (
    edit_or_send,
    safe_edit,
    country_label,
    _fmt_dt,
    lzt_status_label,
    lzt_error_hint,
    build_lzt_logs_csv,
    fmt_phone,
)

logger = logging.getLogger(__name__)


def register(ctx):
    """
    Регистрирует хендлеры LZT-логов.

    Включает: меню LZT-логов со статистикой, список покупок с
    фильтрами (все / вошёл / не удалось), детали одной записи,
    повторный вход в аккаунт по неудавшейся покупке, экспорт
    LZT-логов в CSV.

    :param ctx: объект Context с общим состоянием.
    """
    bot = ctx.bot

    async def _render_lzt_logs_menu(chat_id, edit_id=None, call=None):
        """
        Рисует меню LZT-логов со статистикой покупок.

        :param chat_id: ID чата.
        :param edit_id: ID сообщения для редактирования.
        :param call: callback_query (если вызвано из кнопки).
        """
        s = db.get_lzt_stats()
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📥 LZT-ЛОГИ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 Статистика покупок:\n\n"
            f"  🎯 Всего:            {s['total']}\n"
            f"  🟢 Вошёл:            {s['logged_in']}\n"
            f"  🟡 Куплен, без входа: {s['bought']}\n"
            f"  🔴 Провалился:       {s['failed']}\n"
            f"  💸 Потрачено:        {s['spent_rub']} ₽\n\n"
            "💡 Выберите фильтр:"
        )
        markup = kbd.lzt_logs_menu()
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs")
    @admin_required
    async def cb_lzt_logs(call: types.CallbackQuery):
        """
        Открывает меню LZT-логов.

        :param call: callback_query от кнопки "📥 LZT-ЛОГИ".
        """
        await bot.answer_callback_query(call.id)
        await _render_lzt_logs_menu(call.message.chat.id, call=call)

    async def _render_lzt_list(chat_id, filter_type, page,
                               edit_id=None, call=None):
        """
        Рисует страницу списка LZT-покупок по фильтру.

        :param chat_id: ID чата.
        :param filter_type: фильтр ("all", "logged", "failed", "bought").
        :param page: номер страницы (0-индексация).
        :param edit_id: ID сообщения для редактирования.
        :param call: callback_query (если вызвано из кнопки).
        """
        status_map = {
            "all": None,
            "logged": "logged_in",
            "failed": "failed",
            "bought": "bought",
        }
        titles = {
            "all": "📥 ВСЕ ПОКУПКИ",
            "logged": "🟢 ВОШЁЛ В АККАУНТ",
            "failed": "🔴 НЕ УДАЛОСЬ ВОЙТИ",
            "bought": "🟡 КУПЛЕН, БЕЗ ВХОДА",
        }

        status = status_map.get(filter_type)
        items = db.get_lzt_purchases(status=status, limit=500)
        title = titles.get(filter_type, "📥 LZT-ЛОГИ")

        if not items:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{title}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "😔 Записей нет."
            )
            markup = kbd.kb([[kbd.btn("🔙 НАЗАД", "lzt_logs")]])
            if call:
                await edit_or_send(bot, call, text, markup)
            else:
                await safe_edit(bot, chat_id, edit_id, text, markup)
            return

        total = len(items)
        total_pages = (total + 4) // 5
        page = max(0, min(page, total_pages - 1))
        slice_ = items[page * 5: page * 5 + 5]

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{title}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Всего: {total}  |  Страница {page + 1} / {total_pages}\n\n"
            "🟢 вошёл · 🟡 куплен · 🔴 провалился\n\n"
            "Выберите запись:"
        )
        markup = kbd.lzt_logs_page(slice_, page, total_pages, filter_type, 5)

        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs_all")
    @admin_required
    async def cb_lzt_all(call: types.CallbackQuery):
        """
        Открывает список всех LZT-покупок.

        :param call: callback_query от кнопки "📥 ВСЕ ПОКУПКИ".
        """
        await bot.answer_callback_query(call.id)
        await _render_lzt_list(call.message.chat.id, "all", 0, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs_logged")
    @admin_required
    async def cb_lzt_logged(call: types.CallbackQuery):
        """
        Открывает список успешно залогиненных LZT-покупок.

        :param call: callback_query от кнопки "🟢 ВОШЁЛ В АККАУНТ".
        """
        await bot.answer_callback_query(call.id)
        await _render_lzt_list(call.message.chat.id, "logged", 0, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs_failed")
    @admin_required
    async def cb_lzt_failed(call: types.CallbackQuery):
        """
        Открывает список неудавшихся LZT-покупок.

        :param call: callback_query от кнопки "🔴 НЕ УДАЛОСЬ ВОЙТИ".
        """
        await bot.answer_callback_query(call.id)
        await _render_lzt_list(call.message.chat.id, "failed", 0, call=call)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("lzt_logs_")
        and c.data not in ("lzt_logs", "lzt_logs_all",
                           "lzt_logs_logged", "lzt_logs_failed",
                           "lzt_logs_export")
    )
    @admin_required
    async def cb_lzt_page(call: types.CallbackQuery):
        """
        Переключает страницу LZT-логов.

        :param call: callback_query с data="lzt_logs_{filter}_{page}".
        """
        try:
            parts = call.data.split("_")
            if len(parts) != 4:
                return
            _, _, filter_type, page_str = parts
            if filter_type not in ("all", "logged", "failed", "bought"):
                return
            page = int(page_str)
        except Exception:
            return
        await bot.answer_callback_query(call.id)
        await _render_lzt_list(call.message.chat.id, filter_type, page,
                               call=call)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("lzt_log_view_")
    )
    @admin_required
    async def cb_lzt_view(call: types.CallbackQuery):
        """
        Открывает детали одной LZT-записи.

        Показывает: ID лота, номер, страну, цену, дату, статус,
        наличие в хранилище, ошибку (если была). Кнопка "Повторить
        вход" появляется, если запись провалилась, но номер известен
        и аккаунта в хранилище ещё нет.

        :param call: callback_query с data="lzt_log_view_{id}_{filter}_{page}".
        """
        await bot.answer_callback_query(call.id)
        try:
            parts = call.data.split("_")
            item_id = int(parts[3])
            filter_type = parts[4]
            page = int(parts[5])
        except (ValueError, IndexError) as e:
            logger.error(f"[cb_lzt_view] parse: {e}")
            return

        it = db.get_lzt_purchase(item_id)
        if not it:
            await edit_or_send(bot, call, "❌ Запись не найдена",
                               kbd.back_main())
            return

        in_storage = False
        if it.phone:
            in_storage = db.get_account_by_phone(it.phone) is not None

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📥 LZT-ЗАПИСЬ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 Лот:        #{it.item_id}\n"
            f"📞 Номер:      {it.phone or '—'}\n"
            f"🌍 Страна:     {it.country or '—'}\n"
            f"💰 Цена:       {it.price or 0} ₽\n"
            f"📆 Куплен:     {_fmt_dt(it.bought_at)}\n"
            f"📌 Статус:     {lzt_status_label(it.status)}\n"
            f"📦 В хранилище: {'✅ да' if in_storage else '❌ нет'}"
        )

        if it.error:
            text += (
                f"\n\n⚠️ Ошибка: {it.error[:200]}\n\n"
                f"💡 {lzt_error_hint(it.error)}"
            )

        can_retry = (
            bool(it.phone)
            and not in_storage
            and it.status != "logged_in"
        )
        await edit_or_send(
            bot, call, text,
            kbd.lzt_log_detail_kb(item_id, filter_type, page, can_retry),
        )

    def _lzt_err() -> str:
        """
        Проверяет готовность LZT к работе.

        :return: пустая строка если всё ок, иначе текст ошибки.
        """
        if not lzt.LZT_AVAILABLE:
            return "LOLZTEAM не установлен: pip install LOLZTEAM"
        if not lzt.LZT_API_TOKEN:
            return "LZT_API_TOKEN не задан в config.py"
        return ""

    @bot.callback_query_handler(func=lambda c: c.data.startswith("lzt_retry_"))
    @admin_required
    async def cb_lzt_retry(call: types.CallbackQuery):
        """
        Запускает повторный вход в аккаунт по LZT-записи.

        :param call: callback_query с data="lzt_retry_{item_id}_...".
        """
        await bot.answer_callback_query(call.id, "🔁 Пробую войти...")
        try:
            parts = call.data.split("_")
            item_id = int(parts[2])
        except (ValueError, IndexError):
            return
        await _retry_login_for_purchase(call, item_id)

    async def _retry_login_for_purchase(call: types.CallbackQuery,
                                        item_id: int):
        """
        Пытается повторно войти в аккаунт по LZT-записи.

        Проверяет, что запись есть, номер известен, аккаунта ещё нет
        в хранилище. Получает свежие данные лота (для 2FA), вызывает
        lzt.login_to_account. При успехе сохраняет аккаунт в БД,
        планирует автоочистку, обновляет статус LZT-записи на logged_in.

        :param call: callback_query.
        :param item_id: ID лота LZT.
        """
        it = db.get_lzt_purchase(item_id)
        if not it:
            await edit_or_send(bot, call, "❌ Запись не найдена",
                               kbd.back_main())
            return

        phone = fmt_phone(it.phone) if it.phone else ""

        await edit_or_send(
            bot, call,
            "⏳ ПОВТОРНЫЙ ВХОД\n\n"
            f"🆔 Лот: #{item_id}\n"
            f"📞 Номер: {phone or '?'}\n\n"
            "Пробую получить код и войти...",
        )

        err = _lzt_err()
        if err:
            await edit_or_send(
                bot, call, f"❌ {err}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "lzt_logs")]]),
            )
            return

        if not phone:
            await edit_or_send(
                bot, call,
                "❌ Не знаю номер телефона для этого лота.",
                kbd.back_main(),
            )
            return

        existing = db.get_account_by_phone(phone)
        if existing:
            db.update_lzt_purchase(item_id, "logged_in", phone=phone)
            await edit_or_send(
                bot, call,
                f"✅ Этот аккаунт уже в хранилище.\n\n📞 {phone}",
                kbd.kb([
                    [kbd.btn("🔙 К АККАУНТУ",
                             f"acc_view_{existing.id}_0")],
                    [kbd.btn("📥 LZT-ЛОГИ", "lzt_logs")],
                ]),
            )
            return

        try:
            market = lzt.make_market()
        except Exception as e:
            await edit_or_send(bot, call, f"❌ Ошибка LZT: {e}",
                               kbd.back_main())
            return

        login_data = await lzt.get_item_login_data(market, item_id)
        password = None
        for key in ("telegram_password", "password", "twofa", "2fa",
                    "two_fa", "cloud_password"):
            if login_data.get(key):
                password = str(login_data[key])
                break

        session_str, status = await lzt.login_to_account(
            phone, item_id, market, password=password
        )

        if not session_str:
            db.update_lzt_purchase(item_id, "failed",
                                   phone=phone, error=str(status)[:200])
            db.log_action(call.from_user.id, "lzt_retry_fail",
                          details=f"item={item_id} {status}")
            await edit_or_send(
                bot, call,
                "❌ ПОВТОРНЫЙ ВХОД НЕ УДАЛСЯ\n\n"
                f"🆔 Лот: #{item_id}\n"
                f"📞 Номер: {phone}\n"
                f"❌ Причина: {status}\n\n"
                f"💡 {lzt_error_hint(status)}",
                kbd.kb([
                    [kbd.btn("🔁 ПОВТОРИТЬ",
                             f"lzt_retry_{item_id}_all_0")],
                    [kbd.btn("🔙 К LZT-ЛОГАМ", "lzt_logs")],
                ]),
            )
            return

        try:
            db.add_account(
                phone, session_str, password or "",
                name=phone,
                country_code=_get_country_code(phone),
                price=int(it.price or 0),
                bought_from="lzt",
                lzt_item_id=item_id,
            )
        except Exception as e:
            logger.error(f"[retry.db] {type(e).__name__}: {e}")
            await edit_or_send(
                bot, call,
                f"❌ Вход удался, но аккаунт не сохранился: {e}",
                kbd.back_main(),
            )
            return

        acc = db.get_account_by_phone(phone)
        if acc and ctx.schedule_auto_clean:
            try:
                ctx.schedule_auto_clean(acc.id)
            except Exception:
                pass

        db.update_lzt_purchase(item_id, "logged_in", phone=phone)
        db.stat_added(1)
        db.log_action(call.from_user.id, "lzt_retry_ok",
                      details=f"item={item_id} {phone}",
                      account_id=acc.id if acc else None)

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ ПОВТОРНЫЙ ВХОД УДАЛСЯ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷 Название: {phone}\n"
            f"📞 Номер:    {phone}\n"
            f"🌍 Страна:   {country_label(phone)}\n"
            + (f"🔐 2FA:      {password}\n" if password else "")
            + f"🆔 Лот:      {item_id}\n"
            "📌 Статус:   ⚠️ Ожидает очистки"
        )
        markup = kbd.kb([
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc.id}_0")]
            if acc else kbd.btn("📦 ХРАНИЛИЩЕ", "storage"),
            [kbd.btn("📥 LZT-ЛОГИ", "lzt_logs")],
            [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
        ])
        await edit_or_send(bot, call, text, markup)

    def _get_country_code(phone: str) -> str:
        """
        Определяет код страны по номеру телефона через config.COUNTRIES.

        :param phone: номер телефона.
        :return: код страны (например, "US") или пустая строка.
        """
        from config import COUNTRIES
        p = fmt_phone(phone)
        for c in sorted(COUNTRIES,
                        key=lambda x: len(x["phone_code"]),
                        reverse=True):
            if p.startswith(c["phone_code"]):
                return c.get("code", "")
        return ""

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs_export")
    @admin_required
    async def cb_lzt_logs_export(call: types.CallbackQuery):
        """
        Экспортирует все LZT-покупки в CSV.

        Собирает до 5000 записей и отправляет файлом в чат.

        :param call: callback_query от кнопки "📤 ЭКСПОРТ LZT-ЛОГОВ (CSV)".
        """
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")

        items = db.get_lzt_purchases(limit=5000)
        if not items:
            await edit_or_send(
                bot, call,
                "📤 ЭКСПОРТ LZT-ЛОГОВ\n\n😔 Записей нет.",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "lzt_logs")]]),
            )
            return

        try:
            data = build_lzt_logs_csv(items)
        except Exception as e:
            logger.error(f"[export_lzt] build: {type(e).__name__}: {e}")
            await edit_or_send(
                bot, call, f"❌ Ошибка: {type(e).__name__}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "lzt_logs")]]),
            )
            return

        fname = datetime.now().strftime("lzt_purchases_%Y-%m-%d_%H-%M.csv")

        try:
            await bot.send_document(
                call.message.chat.id,
                document=types.InputFile(io.BytesIO(data), file_name=fname),
                caption=(
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "📤 ЭКСПОРТ LZT-ЛОГОВ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 Записей: {len(items)}\n"
                    f"📄 {fname}"
                ),
            )
            db.log_action(call.from_user.id, "export_lzt_logs",
                          details=f"count={len(items)}")
        except Exception as e:
            logger.error(f"[export_lzt] send: {type(e).__name__}: {e}")
            await edit_or_send(
                bot, call, f"❌ Не отправлено: {type(e).__name__}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "lzt_logs")]]),
            )