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
    status_label,
    build_accounts_csv,
    build_logs_csv,
    build_account_logs_csv,
    _fmt_dt,
)

logger = logging.getLogger(__name__)


def register(ctx):
    bot = ctx.bot

    # ═══════════════════════════════════════════════════════════════════ #
    #                       МЕНЮ ОТЧЁТОВ                                    #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _render_reports_menu(chat_id, edit_id=None, call=None):
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 ОТЧЁТЫ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 Выберите тип:\n\n"
            "  • 📊 Общий — сводка по хранилищу\n"
            "  • 📅 За 30 дней — динамика\n"
            "  • 📱 По аккаунтам — детально каждый\n"
            "  • 🏆 Топ источников — LZT / ручные\n"
            "  • 📤 Экспорт всех — CSV со всеми\n"
            "  • 📜 Экспорт логов — все действия"
        )
        markup = kbd.reports_menu()
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "reports_menu")
    @admin_required
    async def cb_reports_menu(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        await _render_reports_menu(call.message.chat.id, call=call)

    # ═══════════════════════════════════════════════════════════════════ #
    #                          ОБЩИЙ ОТЧЁТ                                  #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "report_total")
    @admin_required
    async def cb_report_total(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)

        s = db.get_accounts_stats()
        lzt_s = db.get_lzt_stats()
        totals = db.get_totals_stats()

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 ОБЩИЙ ОТЧЁТ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📦 ХРАНИЛИЩЕ\n\n"
            f"  📱 Всего:            {s['total']}\n"
            f"  ⭐ Избранных:        {s['favorites']}\n"
            f"  🧹 Очищено:          {s['cleaned']}\n"
            f"  ⚠️ Не очищено:       {s['uncleaned']}\n"
            f"  ❌ Мёртвых:          {s['dead']}\n"
            f"  📦 В архиве:         {s['archived']}\n\n"
            "🛒 LZT MARKET\n\n"
            f"  🎯 Всего покупок:    {lzt_s['total']}\n"
            f"  ✅ Вошёл:            {lzt_s['logged_in']}\n"
            f"  🟡 Без входа:        {lzt_s['bought']}\n"
            f"  ❌ Провалилось:      {lzt_s['failed']}\n"
            f"  💸 Потрачено:        {lzt_s['spent_rub']} ₽\n\n"
            "📈 ЗА ВСЁ ВРЕМЯ\n\n"
            f"  ➕ Добавлено:        {totals['added']}\n"
            f"  🧹 Очищено:          {totals['cleaned']}\n"
            f"  ❌ Помечено dead:    {totals['dead']}\n"
            f"  🛒 Куплено LZT:      {totals['bought_lzt']}\n"
            f"  💸 Потрачено:        {totals['spent_rub']} ₽"
        )
        await edit_or_send(
            bot, call, text,
            kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
        )

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ОТЧЁТ ЗА 30 ДНЕЙ                                #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "report_30d")
    @admin_required
    async def cb_report_30d(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)

        rows = db.get_daily_stats(days=30)
        if not rows:
            await edit_or_send(
                bot, call,
                "📅 ОТЧЁТ ЗА 30 ДНЕЙ\n\n😔 Пока нет данных.",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
            )
            return

        lines = []
        for r in rows[:14]:
            parts = []
            if r.added:
                parts.append(f"+{r.added}📱")
            if r.cleaned:
                parts.append(f"{r.cleaned}🧹")
            if r.dead:
                parts.append(f"{r.dead}❌")
            if r.bought_lzt:
                parts.append(f"{r.bought_lzt}🛒")
            if r.spent_rub:
                parts.append(f"{r.spent_rub}₽")
            lines.append(f"{r.day} · " + " ".join(parts or ["—"]))

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📅 ОТЧЁТ ЗА 30 ДНЕЙ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Формат: +добавлено, 🧹очищено,\n"
            "❌dead, 🛒LZT, ₽потрачено\n\n"
            + "\n".join(lines)
        )
        if len(rows) > 14:
            text += f"\n\n… всего дней с данными: {len(rows)}"

        await edit_or_send(
            bot, call, text,
            kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
        )

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ОТЧЁТ ПО АККАУНТАМ (список)                     #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _render_report_accounts(chat_id, page, edit_id=None, call=None):
        accounts = db.get_accounts()

        if not accounts:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📱 ОТЧЁТЫ ПО АККАУНТАМ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "😔 Хранилище пусто."
            )
            markup = kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]])
            if call:
                await edit_or_send(bot, call, text, markup)
            else:
                await safe_edit(bot, chat_id, edit_id, text, markup)
            return

        total = len(accounts)
        total_pages = (total + 5) // 6
        page = max(0, min(page, total_pages - 1))
        slice_ = accounts[page * 6: page * 6 + 6]

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 ОТЧЁТЫ ПО АККАУНТАМ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Всего: {total}  |  Страница {page + 1} / {total_pages}\n\n"
            "Выберите аккаунт:"
        )
        markup = kbd.reports_accounts_page(slice_, page, total_pages, 6)
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "report_accounts")
    @admin_required
    async def cb_report_accounts(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        await _render_report_accounts(call.message.chat.id, 0, call=call)

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("report_accounts_page_")
    )
    @admin_required
    async def cb_report_accounts_page(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        try:
            page = int(call.data.replace("report_accounts_page_", ""))
        except ValueError:
            page = 0
        await _render_report_accounts(call.message.chat.id, page, call=call)

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ДЕТАЛЬНЫЙ ОТЧЁТ ОБ АККАУНТЕ                     #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _render_account_report(call: types.CallbackQuery, acc_id: int,
                                     page: int = 0):
        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден",
                               kbd.reports_menu())
            return

        counts = db.get_action_counts(acc_id)
        actions = db.get_account_actions(acc_id, limit=10)

        two_fa = f"«{acc.two_fa}»" if acc.two_fa else "не задан"
        name = acc.name or acc.phone
        star = " ⭐" if (acc.favorite or 0) == 1 else ""

        status_emoji = {
            "cleaned": "🧹",
            "uncleaned": "⚠️",
            "dead": "❌",
        }.get(acc.status, "•")

        source_line = acc.bought_from or "manual"
        if acc.bought_from == "lzt" and acc.lzt_item_id:
            source_line = f"LZT (лот #{acc.lzt_item_id})"

        actions_block = ""
        if actions:
            actions_block = "\n📜 Последние действия:\n" + "\n".join(
                f"  • {a.created_at.strftime('%d.%m %H:%M')} "
                f"`{a.action}`"
                + (f" · {a.details[:40]}" if a.details else "")
                for a in actions[:8]
            )

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 ОТЧЁТ ПО АККАУНТУ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📱 ИДЕНТИФИКАЦИЯ\n"
            f"  🏷 Название:   {name}{star}\n"
            f"  📞 Номер:      {acc.phone}\n"
            f"  🌍 Страна:     {country_label(acc.phone)}\n"
            f"  📆 Добавлен:   {_fmt_dt(acc.registered_at)}\n"
            f"  {status_emoji} Статус:  {status_label(acc.status)}\n\n"
            "🔐 БЕЗОПАСНОСТЬ\n"
            f"  🔒 2FA:        {two_fa}\n"
            f"  🕓 Проверен:   {_fmt_dt(acc.last_checked_at)}\n"
            f"  🧹 Очищен:     {_fmt_dt(acc.last_cleaned_at)}\n"
            f"  ❗ Фейлов:      {acc.health_fail_count or 0}\n\n"
            "💸 ИСТОЧНИК\n"
            f"  🛒 Откуда:      {source_line}\n"
            f"  💰 Цена:        {acc.price or 0} ₽\n\n"
            "📈 СТАТИСТИКА ДЕЙСТВИЙ\n"
            f"  🔍 Проверок:    {counts.get('check', 0)}\n"
            f"  🧹 Очисток:     {counts.get('clean', 0)}\n"
            f"  🗑️ Чисток чатов: {counts.get('clear_chats', 0)}\n"
            f"  ✏️ Переименован: {counts.get('rename', 0)}\n"
            f"  🏷 Смен статуса: {counts.get('status', 0)}\n"
            f"  ⭐ Избранное:   {counts.get('favorite', 0)}"
            f"{actions_block}"
        )
        await edit_or_send(bot, call, text, kbd.account_report_kb(acc, page))

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_report_"))
    @admin_required
    async def cb_acc_report(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError) as e:
            logger.error(f"[cb_acc_report] parse: {e}")
            return
        await _render_account_report(call, acc_id, page)

    # ═══════════════════════════════════════════════════════════════════ #
    #                          ИСТОРИЯ АККАУНТА                             #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_history_"))
    @admin_required
    async def cb_acc_history(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "📜")
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except (ValueError, IndexError) as e:
            logger.error(f"[cb_acc_history] parse: {e}")
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден",
                               kbd.back_main())
            return

        actions = db.get_account_actions(acc_id, limit=30)

        if not actions:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📜 ИСТОРИЯ ДЕЙСТВИЙ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                "Пока нет записанных действий."
            )
        else:
            lines = []
            for a in actions:
                ts = a.created_at.strftime("%d.%m %H:%M")
                det = f" · {a.details[:60]}" if a.details else ""
                lines.append(f"{ts} · `{a.action}`{det}")

            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📜 ИСТОРИЯ ДЕЙСТВИЙ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n"
                f"📊 Всего записей: {len(actions)}\n\n"
                + "\n".join(lines)
            )

        await edit_or_send(bot, call, text,
                           kbd.account_history_kb(acc_id, page))

    # ═══════════════════════════════════════════════════════════════════ #
    #                          ТОП ИСТОЧНИКОВ                               #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "report_sources")
    @admin_required
    async def cb_report_sources(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)

        accounts = db.get_accounts()
        manual = sum(
            1 for a in accounts if (a.bought_from or "manual") == "manual"
        )
        lzt_cnt = sum(1 for a in accounts if a.bought_from == "lzt")
        imp = sum(1 for a in accounts if a.bought_from == "import")
        other = len(accounts) - manual - lzt_cnt - imp

        total_spent = sum(
            (a.price or 0) for a in accounts if a.bought_from == "lzt"
        )
        avg_lzt = (total_spent / lzt_cnt) if lzt_cnt else 0

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏆 ТОП ИСТОЧНИКОВ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✍️ Вручную:      {manual}\n"
            f"🛒 LZT Market:   {lzt_cnt}\n"
            f"📥 Импорт LZT:   {imp}\n"
            + (f"❔ Прочее:       {other}\n" if other else "")
            + "\n💸 ФИНАНСЫ\n"
            f"  Потрачено на LZT: {total_spent} ₽\n"
            f"  Средняя цена:     {avg_lzt:.2f} ₽"
        )
        await edit_or_send(
            bot, call, text,
            kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
        )

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ЭКСПОРТ: ВСЕ АККАУНТЫ                          #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "report_export_all")
    @admin_required
    async def cb_report_export_all(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")

        accounts = db.get_accounts()
        if not accounts:
            await edit_or_send(
                bot, call,
                "📤 ЭКСПОРТ\n\n😔 Хранилище пусто.",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
            )
            return

        try:
            data = build_accounts_csv(accounts)
        except Exception as e:
            logger.error(f"[export_all] build: {type(e).__name__}: {e}")
            await edit_or_send(
                bot, call, f"❌ Ошибка: {type(e).__name__}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
            )
            return

        fname = datetime.now().strftime("accounts_%Y-%m-%d_%H-%M.csv")

        try:
            await bot.send_document(
                call.message.chat.id,
                document=types.InputFile(io.BytesIO(data), file_name=fname),
                caption=(
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "📤 ЭКСПОРТ АККАУНТОВ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📱 Аккаунтов: {len(accounts)}\n"
                    f"📄 {fname}\n"
                    f"💾 {len(data) // 1024 + 1} КБ"
                ),
            )
            db.log_action(call.from_user.id, "export_csv_all",
                          details=f"count={len(accounts)}")
        except Exception as e:
            logger.error(f"[export_all] send: {type(e).__name__}: {e}")
            await edit_or_send(
                bot, call, f"❌ Не отправлено: {type(e).__name__}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
            )

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ЭКСПОРТ: ВСЕ ЛОГИ                              #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "report_export_logs")
    @admin_required
    async def cb_report_export_logs(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")

        actions = db.get_recent_actions(limit=5000)
        if not actions:
            await edit_or_send(
                bot, call,
                "📜 ЭКСПОРТ ЛОГОВ\n\n😔 Логов нет.",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
            )
            return

        try:
            data = build_logs_csv(actions)
        except Exception as e:
            logger.error(f"[export_logs] build: {type(e).__name__}: {e}")
            await edit_or_send(
                bot, call, f"❌ Ошибка: {type(e).__name__}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
            )
            return

        fname = datetime.now().strftime("logs_%Y-%m-%d_%H-%M.csv")

        try:
            await bot.send_document(
                call.message.chat.id,
                document=types.InputFile(io.BytesIO(data), file_name=fname),
                caption=(
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "📜 ЭКСПОРТ ЛОГОВ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 Записей: {len(actions)}\n"
                    f"📄 {fname}"
                ),
            )
            db.log_action(call.from_user.id, "export_logs",
                          details=f"count={len(actions)}")
        except Exception as e:
            logger.error(f"[export_logs] send: {type(e).__name__}: {e}")
            await edit_or_send(
                bot, call, f"❌ Не отправлено: {type(e).__name__}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]),
            )

    # ═══════════════════════════════════════════════════════════════════ #
    #                     ЭКСПОРТ: ИСТОРИЯ ОДНОГО                          #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_history_export_")
    )
    @admin_required
    async def cb_acc_history_export(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")
        try:
            parts = call.data.split("_")
            acc_id = int(parts[3])
            page = int(parts[4])
        except (ValueError, IndexError) as e:
            logger.error(f"[acc_history_export] parse: {e}")
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден",
                               kbd.back_main())
            return

        actions = db.get_account_actions(acc_id, limit=500)
        try:
            data = build_account_logs_csv(acc, actions)
        except Exception as e:
            logger.error(f"[acc_history_export] build: {type(e).__name__}: {e}")
            await edit_or_send(bot, call, f"❌ Ошибка: {type(e).__name__}",
                               kbd.back_main())
            return

        safe_phone = acc.phone.lstrip("+") if acc.phone else str(acc.id)
        fname = f"logs_{safe_phone}.csv"

        try:
            await bot.send_document(
                call.message.chat.id,
                document=types.InputFile(io.BytesIO(data), file_name=fname),
                caption=(
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "📤 ЭКСПОРТ ИСТОРИИ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📞 {acc.phone}\n"
                    f"📄 {fname}\n"
                    f"📊 Записей: {len(actions)}"
                ),
            )
            db.log_action(call.from_user.id, "export_history",
                          details=acc.phone, account_id=acc.id)
        except Exception as e:
            logger.error(f"[acc_history_export] send: {type(e).__name__}: {e}")
            await edit_or_send(
                bot, call, f"❌ Не отправлено: {type(e).__name__}",
                kbd.back_main(),
            )