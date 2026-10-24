import logging
import traceback

from telebot import types

import database as db
from .. import keyboards as kbd
from ..decorators import admin_required
from ..helpers import (
    edit_or_send,
    country_label,
    get_country_code,
    fmt_phone,
)
from ..telethon_ops import (
    start_login,
    confirm_code,
    confirm_2fa,
    set_2fa_password,
    request_email_code,
    confirm_email_code,
    _safe_disconnect,
    _get_client,
)
from config import PASSWORD_2FA, LOGIN_EMAIL

logger = logging.getLogger(__name__)


def register(ctx):
    """
    Регистрирует хендлеры добавления и переименования аккаунтов.

    Флоу добавления: имя → номер → код → [2FA] → [email].
    Шаг email выполняется только если в config.py задан LOGIN_EMAIL.
    """
    bot = ctx.bot

    @bot.callback_query_handler(func=lambda c: c.data == "add_account")
    @admin_required
    async def cb_add(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id

        ctx.user_state[uid] = {"state": "add_name_pre"}
        ctx.pending_names.pop(uid, None)

        auto_note = (
            "\n🔐 На аккаунт будет автоматически\n"
            "    установлен 2FA-пароль из конфига.\n"
            if PASSWORD_2FA
            else '\n🔓 Авто-2FA отключён.\n'
        )
        email_note = (
            f"\n📧 Будет привязана почта: {LOGIN_EMAIL}\n"
            if LOGIN_EMAIL else ""
        )

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "➕ ДОБАВЛЕНИЕ АККАУНТА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📝 Шаг 1 из 4\n\n"
            "🏷 Введите название аккаунта (до 64 символов).\n\n"
            "Или отправьте «-», чтобы использовать номер."
            f"{auto_note}{email_note}"
        )
        await edit_or_send(bot, call, text, kbd.cancel())

    async def state_add_name_pre(message, state):
        uid = message.from_user.id
        raw = (message.text or "").strip()

        if len(raw) > 64:
            await bot.send_message(message.chat.id, "❌ Максимум 64 символа")
            return

        if raw == "-" or not raw:
            ctx.pending_names[uid] = None
            shown = "по умолчанию = номер"
        else:
            ctx.pending_names[uid] = raw
            shown = raw

        ctx.user_state[uid] = {"state": "add_phone"}

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "➕ ДОБАВЛЕНИЕ АККАУНТА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📝 Шаг 2 из 4\n\n"
            f"🏷 Название: {shown}\n\n"
            "📞 Введите номер телефона:\n"
            "+79991234567"
        )
        await bot.send_message(message.chat.id, text,
                               reply_markup=kbd.cancel())

    async def state_add_phone(message, state):
        uid = message.from_user.id
        phone = fmt_phone(message.text.strip())

        if not phone or len(phone) < 10 or not phone[1:].isdigit():
            await bot.send_message(message.chat.id,
                                   "❌ Формат: +79991234567")
            return

        name_val = ctx.pending_names.get(uid)
        name_shown = name_val or "по умолчанию = номер"

        await bot.send_message(
            message.chat.id,
            f"⏳ Отправляю код на {phone}...\n\n"
            f"🏷 Название: {name_shown}",
        )

        client, phone_hash, msg = await start_login(phone)
        if not client:
            await bot.send_message(message.chat.id, f"❌ {msg}",
                                   reply_markup=kbd.cancel())
            return

        ctx.pending_signins[uid] = {
            "client": client,
            "phone": phone,
            "hash": phone_hash,
        }
        ctx.user_state[uid] = {"state": "add_code"}

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "➕ ДОБАВЛЕНИЕ АККАУНТА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📝 Шаг 3 из 4\n\n"
            f"🏷 Название: {name_shown}\n"
            f"📞 Номер: {phone}\n\n"
            "Введите код из Telegram:"
        )
        await bot.send_message(message.chat.id, text,
                               reply_markup=kbd.cancel())

    async def state_add_code(message, state):
        uid = message.from_user.id
        code = message.text.strip().replace(" ", "")

        data = ctx.pending_signins.get(uid)
        if not data:
            await bot.send_message(message.chat.id, "❌ Сессия потеряна")
            ctx.user_state.pop(uid, None)
            ctx.pending_names.pop(uid, None)
            return

        ok, msg, session = await confirm_code(
            data["client"], data["phone"], data["hash"], code
        )

        if ok:
            session, two_fa_value = await _apply_post_login(
                message, data, password_used=""
            )
            data["session"] = session
            data["two_fa_value"] = two_fa_value
            if await _maybe_send_email_step(message, uid, data):
                return
            await _finalize_add(message, uid, data, session, two_fa_value)
            return

        if msg == "NEED_2FA":
            ctx.user_state[uid] = {"state": "add_2fa"}
            name_shown = ctx.pending_names.get(uid) or "по умолчанию = номер"

            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "➕ ДОБАВЛЕНИЕ АККАУНТА\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📝 Шаг 4 из 4\n\n"
                f"🏷 Название: {name_shown}\n"
                f"📞 Номер: {data['phone']}\n\n"
                "🔐 Введите текущий пароль 2FA:"
            )
            await bot.send_message(message.chat.id, text,
                                   reply_markup=kbd.cancel())
            return

        await bot.send_message(message.chat.id, f"❌ {msg}")

    async def state_add_2fa(message, state):
        uid = message.from_user.id
        password = message.text.strip()

        data = ctx.pending_signins.get(uid)
        if not data:
            await bot.send_message(message.chat.id, "❌ Сессия потеряна")
            ctx.user_state.pop(uid, None)
            ctx.pending_names.pop(uid, None)
            return

        ok, msg, session = await confirm_2fa(data["client"], password)
        if not ok:
            await bot.send_message(message.chat.id, f"❌ {msg}")
            return

        session, two_fa_value = await _apply_post_login(
            message, data, password_used=password
        )
        data["session"] = session
        data["two_fa_value"] = two_fa_value
        if await _maybe_send_email_step(message, uid, data):
            return
        await _finalize_add(message, uid, data, session, two_fa_value)

    async def _apply_post_login(message, data, password_used: str):
        two_fa_value = password_used or ""
        session = ""

        try:
            session = data["client"].session.save()
        except Exception as e:
            logger.error(f"[post_login] save #1: {e}")

        if PASSWORD_2FA:
            try:
                if not password_used:
                    await bot.send_message(
                        message.chat.id,
                        f"🔐 Устанавливаю 2FA на {data['phone']}...",
                    )
                    s2, msg2 = await set_2fa_password(
                        data["client"], PASSWORD_2FA
                    )
                    if s2:
                        two_fa_value = PASSWORD_2FA
                        logger.info(f"[auto_2fa] {data['phone']}: установлен")
                    else:
                        logger.warning(f"[auto_2fa] {data['phone']}: {msg2}")
                        await bot.send_message(
                            message.chat.id,
                            f"⚠️ 2FA не установлен: {msg2}",
                        )
                elif password_used != PASSWORD_2FA:
                    await bot.send_message(
                        message.chat.id,
                        "🔐 Меняю 2FA на PASSWORD_2FA из конфига...",
                    )
                    s2, msg2 = await set_2fa_password(
                        data["client"], PASSWORD_2FA,
                        current_password=password_used,
                    )
                    if s2:
                        two_fa_value = PASSWORD_2FA
                        logger.info(f"[auto_2fa] {data['phone']}: заменён")
                    else:
                        logger.warning(f"[auto_2fa] {data['phone']}: {msg2}")
                        await bot.send_message(
                            message.chat.id,
                            f"⚠️ 2FA не заменён: {msg2}",
                        )
            except Exception as e:
                logger.error(f"[auto_2fa] {data['phone']}: "
                             f"{type(e).__name__}: {e}")

        try:
            session = data["client"].session.save()
        except Exception as e:
            logger.error(f"[post_login] save #2: {e}")

        return session, two_fa_value

    async def _maybe_send_email_step(message, uid, data):
        if not LOGIN_EMAIL:
            return False

        ok, msg, hash_val, length = await request_email_code(
            data["client"], data["phone"], LOGIN_EMAIL
        )
        if not ok:
            logger.warning(f"[email_step] {data['phone']}: {msg}")
            await bot.send_message(
                message.chat.id,
                f"⚠️ Не удалось отправить код на {LOGIN_EMAIL}: {msg}\n\n"
                "Пропускаю шаг привязки почты.",
            )
            return False

        data["email_hash"] = hash_val
        ctx.pending_signins[uid] = data
        ctx.user_state[uid] = {"state": "add_email_code"}

        await bot.send_message(
            message.chat.id,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📧 ПРИВЯЗКА ПОЧТЫ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📞 Номер: {data['phone']}\n"
            f"📧 Email: {LOGIN_EMAIL}\n\n"
            f"Введите {length}-значный код из письма.\n\n"
            "Если код не пришёл — отправьте «-», чтобы пропустить шаг.",
            reply_markup=kbd.cancel(),
        )
        return True

    async def state_add_email_code(message, state):
        uid = message.from_user.id
        code = (message.text or "").strip()

        data = ctx.pending_signins.get(uid)
        if not data or "email_hash" not in data:
            await bot.send_message(message.chat.id, "❌ Сессия потеряна")
            ctx.user_state.pop(uid, None)
            ctx.pending_signins.pop(uid, None)
            return

        session = data.get("session", "")
        two_fa_value = data.get("two_fa_value", "")

        if code == "-" or not code:
            await bot.send_message(
                message.chat.id,
                "⏭ Пропускаю привязку почты.",
            )
            await _finalize_add(message, uid, data, session, two_fa_value)
            return

        ok, msg, email = await confirm_email_code(
            data["client"], data["phone"], data["email_hash"], code
        )

        if ok:
            email_shown = email or LOGIN_EMAIL
            await bot.send_message(
                message.chat.id,
                f"✅ Почта {email_shown} привязана для входа",
            )
            # Пока аккаунт ещё не в БД (id неизвестен), пишем в лог
            # без account_id — потом, после _finalize_add, увидим
            # привязку по номеру телефона.
            try:
                db.log_action(
                    uid, "email_bind",
                    details=f"{email_shown} (new account)",
                )
            except Exception:
                pass
        else:
            await bot.send_message(
                message.chat.id,
                f"❌ Не удалось привязать почту: {msg}",
            )
            try:
                db.log_action(
                    uid, "email_bind_fail",
                    details=f"{LOGIN_EMAIL}: {msg[:100]} (new account)",
                )
            except Exception:
                pass

        await _finalize_add(message, uid, data, session, two_fa_value)

    async def _finalize_add(message, uid, data, session, two_fa_value):
        name_val = ctx.pending_names.get(uid)
        final_name = name_val or data["phone"]
        phone = fmt_phone(data["phone"])

        saved = False
        db_error = ""
        try:
            saved = db.add_account(
                phone, session, two_fa_value,
                name=final_name,
                country_code=get_country_code(phone),
                bought_from="manual",
            )
            if not saved:
                db_error = "add_account вернул False"
                logger.error(f"[db] add_account({phone}) → False")
        except TypeError as e:
            db_error = f"TypeError: {e}"
            logger.error(f"[db] add_account TypeError: {e}")
            try:
                saved = db.add_account(phone, session, two_fa_value)
            except Exception as e2:
                logger.error(f"[db] fallback add_account: "
                             f"{type(e2).__name__}: {e2}")
        except Exception as e:
            db_error = f"{type(e).__name__}: {e}"
            logger.error(f"[db] add_account({phone}): "
                         f"{type(e).__name__}: {e}")
            logger.debug(traceback.format_exc())

        acc = None
        try:
            acc = db.get_account_by_phone(phone)
        except Exception as e:
            logger.error(f"[db] get_account_by_phone: {type(e).__name__}: {e}")

        if not acc:
            logger.error(f"[finalize] {phone} НЕ сохранён в БД: "
                         f"{db_error or 'неизвестно'}")
            await bot.send_message(
                message.chat.id,
                "❌ НЕ УДАЛОСЬ СОХРАНИТЬ АККАУНТ В БД\n\n"
                f"📞 {phone}\n\n"
                f"Ошибка: {db_error or 'аккаунт не найден'}",
            )
            client = data.get("client")
            if client:
                await _safe_disconnect(client)
            ctx.pending_signins.pop(uid, None)
            ctx.pending_names.pop(uid, None)
            ctx.user_state.pop(uid, None)
            return

        logger.info(f"[finalize] {phone}: сохранён id={acc.id}")

        client = data.get("client")
        if client:
            await _safe_disconnect(client)

        if ctx.schedule_auto_clean:
            try:
                ctx.schedule_auto_clean(acc.id)
            except Exception as e:
                logger.warning(f"[finalize] schedule_auto_clean: {e}")

        ctx.pending_signins.pop(uid, None)
        ctx.pending_names.pop(uid, None)
        ctx.user_state.pop(uid, None)

        db.stat_added(1)
        db.log_action(uid, "add", details=phone, account_id=acc.id)

        two_fa_line = (
            f"🔐 2FA:      {two_fa_value}"
            if two_fa_value else "🔐 2FA:      не задан"
        )

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ АККАУНТ ДОБАВЛЕН\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷 Название: {acc.name or final_name}\n"
            f"📞 Номер:    {phone}\n"
            f"🌍 Страна:   {country_label(phone)}\n"
            f"{two_fa_line}\n"
            "📌 Статус:   ⚠️ Ожидает очистки"
        )
        markup = kbd.kb([
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc.id}_r")],
            [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
            [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
        ])
        try:
            await bot.send_message(message.chat.id, text, reply_markup=markup)
        except Exception as e:
            logger.error(f"[finalize] send: {type(e).__name__}: {e}")

    async def state_rename_account(message, state):
        uid = message.from_user.id
        new_name = (message.text or "").strip()
        acc_id = state.get("acc_id")
        origin = state.get("page", "r")

        if not acc_id:
            ctx.user_state.pop(uid, None)
            await bot.send_message(message.chat.id, "❌ Сессия потеряна")
            return

        acc = db.get_account(acc_id)
        if not acc:
            ctx.user_state.pop(uid, None)
            await bot.send_message(message.chat.id, "❌ Аккаунт не найден")
            return

        if len(new_name) > 64:
            await bot.send_message(message.chat.id, "❌ Максимум 64 символа")
            return

        if new_name == "-" or not new_name:
            new_name = acc.phone

        db.update_account_name(acc_id, new_name)
        db.log_action(uid, "rename", details=new_name, account_id=acc_id)
        ctx.user_state.pop(uid, None)

        await bot.send_message(
            message.chat.id,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ НАЗВАНИЕ ОБНОВЛЕНО\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📞 {acc.phone}\n"
            f"🏷 Новое: {new_name}",
            reply_markup=kbd.kb([
                [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{origin}")],
                [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
                [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
            ]),
        )

    async def state_email_verify(message, state):
        uid = message.from_user.id
        code = (message.text or "").strip()
        acc_id = state.get("acc_id")
        origin = state.get("origin", "r")
        email_hash = state.get("email_hash")

        if not acc_id:
            ctx.user_state.pop(uid, None)
            await bot.send_message(message.chat.id, "❌ Сессия потеряна")
            return

        acc = db.get_account(acc_id)
        if not acc:
            ctx.user_state.pop(uid, None)
            await bot.send_message(message.chat.id, "❌ Аккаунт не найден")
            return

        try:
            client = await _get_client(acc.phone, acc.session)
        except Exception as e:
            logger.error(f"[email_verify] connect: {type(e).__name__}: {e}")
            ctx.user_state.pop(uid, None)
            try:
                db.log_action(
                    uid, "email_bind_fail",
                    details=f"connect: {str(e)[:100]}",
                    account_id=acc_id,
                )
            except Exception:
                pass
            await bot.send_message(
                message.chat.id, f"❌ Не удалось подключиться: {e}")
            return

        ok, msg, email = await confirm_email_code(
            client, acc.phone, email_hash, code
        )

        ctx.user_state.pop(uid, None)
        ctx.pending_signins.pop(uid, None)

        if ok:
            email_shown = email or LOGIN_EMAIL
            try:
                db.log_action(
                    uid, "email_bind",
                    details=email_shown,
                    account_id=acc_id,
                )
            except Exception:
                pass
            await bot.send_message(
                message.chat.id,
                f"✅ Почта {email_shown} привязана для входа\n\n"
                f"📞 {acc.phone}",
                reply_markup=kbd.kb([
                    [kbd.btn("🔙 К АККАУНТУ",
                             f"acc_view_{acc_id}_{origin}")],
                    [kbd.btn("🏠 МЕНЮ", "main_menu")],
                ]),
            )
        else:
            try:
                db.log_action(
                    uid, "email_bind_fail",
                    details=f"{LOGIN_EMAIL}: {msg[:100]}",
                    account_id=acc_id,
                )
            except Exception:
                pass
            await bot.send_message(
                message.chat.id,
                f"❌ Не удалось привязать почту: {msg}",
                reply_markup=kbd.kb([
                    [kbd.btn("🔙 К АККАУНТУ",
                             f"acc_view_{acc_id}_{origin}")],
                ]),
            )

    local_handlers = {
        "add_name_pre": state_add_name_pre,
        "add_phone": state_add_phone,
        "add_code": state_add_code,
        "add_2fa": state_add_2fa,
        "add_email_code": state_add_email_code,
        "rename_account": state_rename_account,
        "email_verify": state_email_verify,
    }

    @bot.message_handler(func=lambda m: True, content_types=["text"])
    @admin_required
    async def text_router(message: types.Message):
        uid = message.from_user.id

        if message.text and message.text.startswith("/"):
            return

        state = ctx.user_state.get(uid, {})
        state_name = state.get("state")

        all_handlers = dict(local_handlers)
        all_handlers.update(getattr(ctx, "_buy_state_handlers", {}))

        handler = all_handlers.get(state_name)
        if handler:
            try:
                await handler(message, state)
            except Exception as e:
                logger.error(f"[text_router] {type(e).__name__}: {e}")
                logger.debug(traceback.format_exc())
                ctx.user_state.pop(uid, None)
                ctx.pending_names.pop(uid, None)
                await bot.send_message(message.chat.id,
                                       f"❌ {str(e)[:150]}")
            return

        await bot.send_message(message.chat.id, "Используйте /start")