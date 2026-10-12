import asyncio
import logging
from datetime import datetime

from telebot import types
from telethon import TelegramClient
from telethon.sessions import StringSession

import database as db
from .. import keyboards as kbd
from ..decorators import admin_required
from ..helpers import edit_or_send, safe_edit
from ..telethon_ops import (
    is_alive,
    reset_other_sessions,
    clear_dialogs_only,
    close_client,
)
from config import ADMIN_ID, API_ID, API_HASH
from config import AUTO_CLEAN_DELAY

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


def register(ctx):
    bot = ctx.bot

    # ═══════════════════════════════════════════════════════════════════ #
    #                       АВТООЧИСТКА (24ч + 10сек)                       #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _auto_clean_after(account_id: int, delay: float):
        """Через delay секунд делает ResetAuthorizations + чистку чатов."""
        try:
            await asyncio.sleep(delay)
            acc = db.get_account(account_id)
            if not acc or acc.status != "uncleaned":
                return

            ok, msg = await reset_other_sessions(acc.session, acc.phone)

            if ok:
                db.mark_cleaned(account_id)
                db.stat_cleaned(1)
                db.log_action(0, "auto_clean", details=acc.phone,
                              account_id=account_id)
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        "⏰ АВТООЧИСТКА СРАБОТАЛА\n\n"
                        f"🏷 {acc.name or acc.phone}\n"
                        f"📞 {acc.phone}\n"
                        "🔒 Сессии закрыты, чаты удалены\n"
                        f"ℹ️ {msg}",
                    )
                except Exception:
                    pass

            elif msg == "FROZEN":
                db.update_account_status(account_id, "dead")
                db.log_action(0, "frozen", details="auto_clean",
                              account_id=account_id)
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        "🧊 АККАУНТ ЗАМОРОЖЕН TELEGRAM\n\n"
                        f"🏷 {acc.name or acc.phone}\n"
                        f"📞 {acc.phone}\n\n"
                        "Аккаунт помечен как ❌ Мёртвый.",
                    )
                except Exception:
                    pass
            else:
                logger.warning(f"[auto_clean] {acc.phone}: {msg}")

        except asyncio.CancelledError:
            logger.info(f"[auto_clean] {account_id}: отменена")
        except Exception as e:
            logger.error(f"[auto_clean] {account_id}: {type(e).__name__}: {e}")
        finally:
            ctx.auto_clean_tasks.pop(account_id, None)

    def schedule_auto_clean(account_id: int, delay: float = AUTO_CLEAN_DELAY):
        if account_id in ctx.auto_clean_tasks:
            return
        task = asyncio.create_task(_auto_clean_after(account_id, delay))
        ctx.auto_clean_tasks[account_id] = task

    # Сохраняем в Context — нужно другим модулям
    ctx.schedule_auto_clean = schedule_auto_clean

    # Восстанавливаем таймеры для уже добавленных аккаунтов при старте бота
    async def _restore_auto_clean():
        try:
            now = datetime.now()
            for acc in db.get_accounts():
                if acc.status != "uncleaned" or not acc.registered_at:
                    continue
                elapsed = (now - acc.registered_at).total_seconds()
                if elapsed >= AUTO_CLEAN_DELAY:
                    schedule_auto_clean(acc.id, 1)
                else:
                    schedule_auto_clean(acc.id, AUTO_CLEAN_DELAY - elapsed)
        except Exception as e:
            logger.error(f"[auto_clean] restore: {type(e).__name__}: {e}")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_restore_auto_clean())
    except Exception as e:
        logger.warning(f"[clean] restore schedule: {e}")

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ПРОГРЕВ СЕССИЙ ПРИ СТАРТЕ                      #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _warmup_sessions():
        await asyncio.sleep(5)
        accounts = db.get_accounts()

        if not accounts:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    "🔥 ПРОГРЕВ СЕССИЙ\n\n📭 Хранилище пусто.",
                    reply_markup=kbd.kb([
                        [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
                        [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
                    ]),
                )
            except Exception as e:
                logger.warning(f"[warmup] send: {e}")
            return

        total = len(accounts)
        try:
            msg = await bot.send_message(
                ADMIN_ID,
                "🔥 ПРОГРЕВ СЕССИЙ ЗАПУЩЕН\n\n"
                f"📱 К обработке: {total} аккаунтов\n\n⏳ Начинаю...",
            )
            msg_id, chat_id = msg.message_id, msg.chat.id
        except Exception as e:
            logger.warning(f"[warmup] send start: {e}")
            msg_id, chat_id = None, ADMIN_ID

        ok = fail = 0
        detail = []

        for i, acc in enumerate(accounts, 1):
            name = acc.name or acc.phone
            icon, txt = "⏳", ""

            try:
                c = TelegramClient(StringSession(acc.session),
                                   API_ID, API_HASH)
                await asyncio.wait_for(c.connect(), timeout=15)

                if not await c.is_user_authorized():
                    fail += 1
                    icon, txt = "❌", "не авторизована"
                    await c.disconnect()
                    db.update_last_checked(acc.id, alive=False)
                else:
                    await asyncio.wait_for(c.get_me(), timeout=10)
                    await c.disconnect()
                    ok += 1
                    icon, txt = "✅", "активен"
                    db.update_last_checked(acc.id, alive=True)

            except asyncio.TimeoutError:
                fail += 1; icon, txt = "⏱", "таймаут"
            except Exception as e:
                fail += 1
                icon, txt = "⚠️", f"{type(e).__name__}"
                logger.error(f"[warmup] {acc.phone}: {type(e).__name__}: {e}")

            detail.append(f"{icon} {name} ({acc.phone}) — {txt}")

            if msg_id:
                tail = "\n".join(detail[-5:])
                await safe_edit(
                    bot, chat_id, msg_id,
                    "🔥 ПРОГРЕВ СЕССИЙ\n\n"
                    f"📊 Прогресс: {i}/{total}\n\n"
                    f"✅ Успешно: {ok}\n❌ Ошибок: {fail}\n\n"
                    f"📞 Текущий: {acc.phone}\n\n"
                    f"Последние:\n{tail}",
                )
            await asyncio.sleep(3)

        lines = "\n".join(detail)
        if len(lines) > 3500:
            lines = lines[:3500] + "\n… (обрезано)"

        final = (
            "🔥 ПРОГРЕВ СЕССИЙ ЗАВЕРШЁН\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Успешно: {ok}\n❌ Ошибок: {fail}\n📱 Всего: {total}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Детали:\n{lines}"
        )
        markup = kbd.kb([
            [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
            [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
        ])

        if msg_id:
            await safe_edit(bot, chat_id, msg_id, final, markup)
        else:
            try:
                await bot.send_message(ADMIN_ID, final, reply_markup=markup)
            except Exception as e:
                logger.warning(f"[warmup] send final: {e}")

    try:
        asyncio.get_running_loop().create_task(_warmup_sessions())
    except Exception as e:
        logger.warning(f"[clean] warmup schedule: {e}")

    # ═══════════════════════════════════════════════════════════════════ #
    #                          ПРОВЕРКА ВСЕХ                                #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _check_all(chat_id: int, msg_id: int, accounts, uid: int):
        total = len(accounts)
        ok = dead = 0

        for i, acc in enumerate(accounts, 1):
            await safe_edit(
                bot, chat_id, msg_id,
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🔍 ПРОВЕРКА АККАУНТОВ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 Прогресс: {i}/{total}\n\n"
                f"✅ Живых:   {ok}\n❌ Мёртвых: {dead}\n\n"
                f"📞 Проверяю: {acc.phone}",
                kbd.back_main(),
            )

            alive, info = await is_alive(acc.session, acc.phone)
            db.update_last_checked(acc.id, alive=alive)

            if alive:
                ok += 1
                if acc.status == "dead":
                    db.update_account_status(acc.id, "uncleaned")
            else:
                dead += 1
                logger.warning(f"[check_all] {acc.phone}: мёртв — {info}")
                db.update_account_status(acc.id, "dead")
                db.stat_dead(1)

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 ПРОВЕРКА ЗАВЕРШЕНА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Живых:   {ok}\n"
            f"❌ Мёртвых: {dead}\n"
            f"📱 Всего:   {total}"
        )
        db.log_action(uid, "check", details=f"ok={ok} dead={dead}")
        await safe_edit(bot, chat_id, msg_id, text,
                        kbd.after_check(dead > 0))

    @bot.callback_query_handler(func=lambda c: c.data == "check_accounts")
    @admin_required
    async def cb_check(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        accounts = db.get_accounts()

        if not accounts:
            await edit_or_send(bot, call, "📦 Хранилище пусто.",
                               kbd.back_main())
            return
        if ctx.has_running_task(uid):
            await edit_or_send(bot, call, "⏳ Уже идёт...", kbd.back_main())
            return

        task = asyncio.create_task(
            _check_all(call.message.chat.id, call.message.message_id,
                       accounts, uid)
        )
        ctx.set_running_task(uid, task)

    # ═══════════════════════════════════════════════════════════════════ #
    #                     МАССОВАЯ ОЧИСТКА (Reset + чаты)                   #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _clean_all(chat_id: int, msg_id: int, accounts, uid: int):
        ctx.cancel_flags[uid] = False
        total = len(accounts)
        ok = fail = 0
        cancelled = False
        error_lines = []

        for i, acc in enumerate(accounts, 1):
            if ctx.cancel_flags.get(uid):
                cancelled = True
                break

            errors_block = ""
            if error_lines:
                errors_block = "\n\nОшибки:\n" + "\n".join(
                    f"• {e}" for e in error_lines[-5:]
                )

            await safe_edit(
                bot, chat_id, msg_id,
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🧹 ОЧИСТКА СЕССИЙ + ЧАТОВ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 Прогресс: {i - 1}/{total}\n\n"
                f"✅ Очищено: {ok}\n❌ Ошибок:  {fail}\n\n"
                f"📞 Текущий: {acc.phone}\n\n"
                "⏹ Нажмите «ОТМЕНИТЬ ОЧИСТКУ» для остановки."
                f"{errors_block}",
                kbd.cleaning_progress(),
            )

            # Отменяем автоочистку — сейчас делаем вручную
            task = ctx.pop_auto_clean_task(acc.id)
            if task and not task.done():
                task.cancel()

            s, msg = await reset_other_sessions(acc.session, acc.phone)

            if s:
                ok += 1
                db.mark_cleaned(acc.id)
                db.stat_cleaned(1)
            elif msg == "FROZEN":
                db.update_account_status(acc.id, "dead")
                db.log_action(0, "frozen", details="clean_all",
                              account_id=acc.id)
                fail += 1
                name = acc.name or acc.phone
                error_lines.append(f"{name} ({acc.phone}): 🧊 заморожен")
            else:
                fail += 1
                name = acc.name or acc.phone
                error_lines.append(f"{name} ({acc.phone}): {msg}")

        ctx.cancel_flags.pop(uid, None)
        header = "⏹ ОЧИСТКА ОТМЕНЕНА" if cancelled else "📊 ОЧИСТКА ЗАВЕРШЕНА"

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{header}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Очищено: {ok}\n❌ Ошибок:  {fail}\n📱 Всего: {total}\n\n"
            "🔒 Сессии закрыты, выход из групп/каналов выполнен."
        )
        if error_lines:
            text += "\n\nОшибки:\n" + "\n".join(
                f"• {e}" for e in error_lines[:15]
            )
            if len(error_lines) > 15:
                text += f"\n… и ещё {len(error_lines) - 15}"
        else:
            text += "\nОшибки: —"

        db.log_action(uid, "clean_all", details=f"ok={ok} fail={fail}")
        await safe_edit(bot, chat_id, msg_id, text, kbd.back_main())

    @bot.callback_query_handler(func=lambda c: c.data == "clean_sessions")
    @admin_required
    async def cb_clean(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        accounts = [a for a in db.get_accounts() if a.status == "uncleaned"]

        if not accounts:
            await edit_or_send(
                bot, call,
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "✅ ВСЁ УЖЕ ОЧИЩЕНО\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "🧹 Нечего очищать.",
                kbd.back_main(),
            )
            return

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ ОЧИСТКА СЕССИЙ И ЧАТОВ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 К очистке: {len(accounts)} аккаунтов\n\n"
            "💡 Что произойдёт:\n"
            "• Все чужие сессии завершатся\n"
            "• Выход из всех групп и каналов\n"
            "• Удаление всей переписки\n\n"
            "❗ Продолжить?"
        )
        await edit_or_send(bot, call, text, kbd.confirm_clean())

    @bot.callback_query_handler(func=lambda c: c.data == "clean_confirm")
    @admin_required
    async def cb_clean_confirm(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        accounts = [a for a in db.get_accounts() if a.status == "uncleaned"]

        if not accounts:
            await edit_or_send(bot, call, "✅ Нечего", kbd.back_main())
            return
        if ctx.has_running_task(uid):
            await edit_or_send(bot, call, "⏳ Уже идёт...", kbd.back_main())
            return

        task = asyncio.create_task(
            _clean_all(call.message.chat.id, call.message.message_id,
                       accounts, uid)
        )
        ctx.set_running_task(uid, task)

    @bot.callback_query_handler(func=lambda c: c.data == "clean_cancel")
    @admin_required
    async def cb_clean_cancel(call: types.CallbackQuery):
        ctx.cancel_flags[call.from_user.id] = True
        await bot.answer_callback_query(call.id, "⏹ Отмена...",
                                        show_alert=False)

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ОЧИСТКА ОДНОГО АККАУНТА                        #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_clean_")
    )
    @admin_required
    async def cb_acc_clean(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "⏳ Очищаю...")
        uid = call.from_user.id
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except Exception:
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return

        await edit_or_send(
            bot, call,
            "⏳ ОЧИЩАЮ СЕССИИ И ЧАТЫ...\n\n"
            f"🏷 {acc.name or acc.phone}\n"
            f"📞 {acc.phone}\n\n"
            "1️⃣ Завершаю чужие сессии\n"
            "2️⃣ Выхожу из групп и каналов\n"
            "3️⃣ Удаляю всю переписку\n\n"
            "⏳ Это может занять до минуты...",
        )

        # Отменяем автоочистку
        task = ctx.pop_auto_clean_task(acc_id)
        if task and not task.done():
            task.cancel()

        ok, msg = await reset_other_sessions(acc.session, acc.phone)

        if ok:
            db.mark_cleaned(acc.id)
            db.stat_cleaned(1)
            db.log_action(uid, "clean", details=acc.phone, account_id=acc.id)
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "✅ ОЧИСТКА ЗАВЕРШЕНА\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                "🔒 Все чужие сессии завершены.\n"
                "📤 Выход из групп и каналов.\n"
                "🗑️ Переписка удалена.\n\n"
                f"ℹ️ {msg}"
            )
        elif msg == "FROZEN":
            db.update_account_status(acc.id, "dead")
            db.log_action(uid, "frozen", details="ручная очистка",
                          account_id=acc.id)
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🧊 АККАУНТ ЗАМОРОЖЕН TELEGRAM\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                "ResetAuthorizations недоступен.\n\n"
                "📌 Статус изменён на ❌ Мёртвый."
            )
        else:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "❌ НЕ УДАЛОСЬ ОЧИСТИТЬ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                f"Ошибки: {msg}"
            )

        markup = kbd.kb([
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{page}")],
            [kbd.btn("🏠 МЕНЮ", "main_menu")],
        ])
        await edit_or_send(bot, call, text, markup)

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ОЧИСТКА ТОЛЬКО ЧАТОВ                            #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("acc_clear_chats_")
    )
    @admin_required
    async def cb_acc_clear_chats(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "⏳ Чищу чаты...")
        uid = call.from_user.id
        try:
            _, _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except Exception as e:
            logger.error(f"[cb_acc_clear_chats] parse: {e}")
            return

        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return

        await edit_or_send(
            bot, call,
            "⏳ УДАЛЯЮ ЧАТЫ...\n\n"
            f"🏷 {acc.name or acc.phone}\n"
            f"📞 {acc.phone}\n\n"
            "📤 Выхожу из групп и каналов\n"
            "🗑️ Удаляю всю переписку\n"
            "⏳ Это может занять до минуты...",
        )

        ok, msg, deleted, failed, total = await clear_dialogs_only(
            acc.session, acc.phone
        )

        if ok:
            db.log_action(uid, "clear_chats",
                          details=f"{acc.phone} del={deleted} fail={failed}",
                          account_id=acc.id)
            if total == 0:
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ ЧАТОВ НЕТ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📞 {acc.phone}\n\n"
                    "🗑️ У аккаунта не было диалогов."
                )
            else:
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ ЧАТЫ ОЧИЩЕНЫ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📞 {acc.phone}\n\n"
                    f"📤 Обработано:   {deleted}\n"
                    f"⚠️ Ошибок:       {failed}\n"
                    f"📊 Всего было:   {total}\n\n"
                    "ℹ️ Сессии аккаунта НЕ тронуты."
                )
        elif msg == "FROZEN":
            db.update_account_status(acc.id, "dead")
            db.log_action(uid, "frozen", details="clear_chats",
                          account_id=acc.id)
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🧊 АККАУНТ ЗАМОРОЖЕН TELEGRAM\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                "Очистка чатов недоступна.\n\n"
                "📌 Статус изменён на ❌ Мёртвый."
            )
        else:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "❌ НЕ УДАЛОСЬ ОЧИСТИТЬ ЧАТЫ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                f"Ошибки: {msg}"
            )

        markup = kbd.kb([
            [kbd.btn("🔄 ПОВТОРИТЬ", f"acc_clear_chats_{acc_id}_{page}")],
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{page}")],
            [kbd.btn("🏠 МЕНЮ", "main_menu")],
        ])
        await edit_or_send(bot, call, text, markup)

    # ═══════════════════════════════════════════════════════════════════ #
    #                          УДАЛЕНИЕ МЁРТВЫХ                             #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "del_dead")
    @admin_required
    async def cb_del_dead(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        dead_accs = [a for a in db.get_accounts() if a.status == "dead"]

        if not dead_accs:
            await edit_or_send(
                bot, call,
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "✅ МЁРТВЫХ НЕТ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "🗑️ В хранилище нет мёртвых аккаунтов.",
                kbd.back_main(),
            )
            return

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🗑️ УДАЛИТЬ МЁРТВЫЕ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 Найдено мёртвых: {len(dead_accs)}\n\n"
            "Будут удалены:\n"
            + "\n".join(f"• {a.name or a.phone}" for a in dead_accs[:15])
            + (f"\n… и ещё {len(dead_accs) - 15}"
               if len(dead_accs) > 15 else "")
            + "\n\n❗ Нельзя отменить."
        )
        await edit_or_send(bot, call, text,
                           kbd.confirm_del_dead(len(dead_accs)))

    @bot.callback_query_handler(func=lambda c: c.data == "del_dead_confirm")
    @admin_required
    async def cb_del_dead_confirm(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        cnt = 0
        errors = []

        for acc in db.get_accounts():
            if acc.status == "dead":
                try:
                    task = ctx.pop_auto_clean_task(acc.id)
                    if task and not task.done():
                        task.cancel()
                    await close_client(acc.phone)
                    db.delete_account(acc.id)
                    cnt += 1
                except Exception as e:
                    errors.append(f"{acc.phone}: {e}")

        db.log_action(uid, "del_dead", details=f"count={cnt}")

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🗑️ МЁРТВЫЕ УДАЛЕНЫ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Удалено: {cnt}"
        )
        if errors:
            text += "\n\nОшибки:\n" + "\n".join(
                f"• {e}" for e in errors[:10]
            )
        await edit_or_send(bot, call, text, kbd.back_main())