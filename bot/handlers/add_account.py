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
    _safe_disconnect,
)
from config import PASSWORD_2FA

logger = logging.getLogger(__name__)


def register(ctx):
    """
    Регистрирует хендлеры добавления и переименования аккаунтов.

    Флоу добавления состоит из 4 шагов: имя → номер → код → 2FA.
    В конце вызывается _finalize_add, который сохраняет аккаунт в БД
    и планирует автоочистку. Также сюда входит FSM переименования
    и общий text_router для текстовых сообщений.

    :param ctx: объект Context с общим состоянием.
    """
    bot = ctx.bot

    @bot.callback_query_handler(func=lambda c: c.data == "add_account")
    @admin_required
    async def cb_add(call: types.CallbackQuery):
        """
        Начинает флоу добавления аккаунта — шаг 1 (ввод имени).

        Сбрасывает предыдущее pending_names, переводит пользователя
        в состояние add_name_pre, показывает инструкцию и заметку
        о 2FA.

        :param call: callback_query от кнопки "➕ ДОБАВИТЬ АККАУНТ".
        """
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

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "➕ ДОБАВЛЕНИЕ АККАУНТА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📝 Шаг 1 из 4\n\n"
            "🏷 Введите название аккаунта (до 64 символов).\n\n"
            "Или отправьте «-», чтобы использовать номер."
            f"{auto_note}"
        )
        await edit_or_send(bot, call, text, kbd.cancel())

    async def state_add_name_pre(message, state):
        """
        Шаг 1: принимает имя аккаунта или "-" для дефолтного.

        Валидирует длину (до 64 символов), сохраняет в pending_names
        (или None, если дефолт), переводит пользователя в состояние
        add_phone.

        :param message: объект Message с именем.
        :param state: текущее FSM-состояние пользователя.
        """
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
        """
        Шаг 2: принимает номер телефона и отправляет код.

        Валидирует формат, вызывает start_login, сохраняет клиент и
        хэш кода в pending_signins, переводит в состояние add_code.

        :param message: объект Message с номером.
        :param state: текущее FSM-состояние пользователя.
        """
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
        """
        Шаг 3: принимает код из Telegram.

        Вызывает confirm_code. Если вход удался — идёт в _apply_post_login
        и _finalize_add. Если нужен 2FA — переводит в состояние add_2fa.
        При ошибке — показывает сообщение.

        :param message: объект Message с кодом.
        :param state: текущее FSM-состояние пользователя.
        """
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
        """
        Шаг 4: принимает пароль 2FA.

        Вызывает confirm_2fa, после успеха идёт в _apply_post_login
        и _finalize_add.

        :param message: объект Message с паролем 2FA.
        :param state: текущее FSM-состояние пользователя.
        """
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
        await _finalize_add(message, uid, data, session, two_fa_value)

    async def _apply_post_login(message, data, password_used: str):
        """
        Пост-логин: устанавливает или меняет 2FA-пароль из config.

        Все ошибки логируются и сообщаются пользователю, но не
        прерывают сохранение. Возвращает актуальную session (после
        edit_2fa она меняется) и итоговое значение 2FA.

        :param message: объект Message для отправки уведомлений.
        :param data: словарь из pending_signins с клиентом и номером.
        :param password_used: пароль, введённый пользователем (если был).
        :return: (session_string, two_fa_value).
        """
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

    async def _finalize_add(message, uid, data, session, two_fa_value):
        """
        Сохраняет аккаунт в БД, закрывает клиент, планирует автоочистку.

        Если сохранение не удалось — уведомляет пользователя и
        закрывает подвисший клиент. При успехе показывает финальную
        карточку с кнопками перехода к аккаунту, хранилищу и меню.

        :param message: объект Message для ответа.
        :param uid: Telegram ID пользователя.
        :param data: словарь из pending_signins.
        :param session: строка сессии.
        :param two_fa_value: финальное значение 2FA.
        """
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
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc.id}_0")],
            [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
            [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
        ])
        try:
            await bot.send_message(message.chat.id, text, reply_markup=markup)
        except Exception as e:
            logger.error(f"[finalize] send: {type(e).__name__}: {e}")

    async def state_rename_account(message, state):
        """
        FSM-обработчик переименования аккаунта.

        Принимает новое имя, валидирует длину, обновляет в БД,
        логирует действие и показывает карточку с новым названием.

        :param message: объект Message с новым именем.
        :param state: словарь с acc_id и page.
        """
        uid = message.from_user.id
        new_name = (message.text or "").strip()
        acc_id = state.get("acc_id")
        page = state.get("page", 0)

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
                [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{page}")],
                [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
                [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
            ]),
        )

    local_handlers = {
        "add_name_pre": state_add_name_pre,
        "add_phone": state_add_phone,
        "add_code": state_add_code,
        "add_2fa": state_add_2fa,
        "rename_account": state_rename_account,
    }

    @bot.message_handler(func=lambda m: True, content_types=["text"])
    @admin_required
    async def text_router(message: types.Message):
        """
        Централизованный роутер текстовых сообщений.

        Определяет, в каком FSM-состоянии находится пользователь,
        и вызывает соответствующий обработчик. Объединяет локальные
        хендлеры этого модуля и FSM из lzt_buy через
        ctx._buy_state_handlers.

        Если пользователь не в состоянии — подсказывает /start.
        Команды (начинающиеся на "/") игнорирует.

        :param message: объект Message с текстом.
        """
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