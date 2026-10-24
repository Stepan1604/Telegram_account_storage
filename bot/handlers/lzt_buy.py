import asyncio
import logging

from telebot import types

import database as db
from .. import keyboards as kbd
from .. import lzt
from config import LOGIN_EMAIL as LOGIN_EMAIL_SHOWN
from ..decorators import admin_required
from ..helpers import (
    edit_or_send,
    safe_edit,
    fmt_phone,
    get_country_code,
    lzt_error_hint,
    country_ru,
)

logger = logging.getLogger(__name__)

# Максимальная длина поля LZTPurchase.country в БД.
# Держим в одном месте, чтобы обрезать во всех вызовах add_lzt_purchase
# и не ловить в PostgreSQL "value too long for type character varying(64)".
COUNTRY_MAX_LEN = 64

# Сколько ждём код из письма, пока пользователь его не введёт
EMAIL_CODE_WAIT = 300
# uid -> Future, куда попадает введённый в чат код
_EMAIL_WAITERS = {}


def _safe_country(country) -> str:
    """Обрезает страну до безопасной длины для колонки VARCHAR(64)."""
    return (str(country or ""))[:COUNTRY_MAX_LEN]


def register(ctx):
    bot = ctx.bot

    def _lzt_err() -> str:
        if not lzt.LZT_AVAILABLE:
            return "LOLZTEAM не установлен: pip install LOLZTEAM"
        if not lzt.LZT_API_TOKEN:
            return "LZT_API_TOKEN не задан в config.py"
        return ""

    def _log_lzt_purchase(item_id, phone, price, country,
                          status, error="") -> None:
        """
        Единая точка регистрации покупки в lzt_purchases.

        Никогда не глушит ошибку — логирует тип и текст, чтобы такие
        случаи были видны в server-логе, а не пропадали молча.
        """
        try:
            db.add_lzt_purchase(
                item_id=item_id,
                phone=phone,
                price=int(price or 0),
                country=_safe_country(country),
                status=status,
                error=(error or "")[:500],
            )
        except Exception as e:
            logger.error(
                f"[lzt_buy.log_purchase] item={item_id} "
                f"phone={phone} status={status}: "
                f"{type(e).__name__}: {e}"
            )

    async def _render_buy_menu(chat_id, edit_id=None, call=None):
        err = _lzt_err()
        if err:
            text = (
                "🛒 ПОКУПКА НОМЕРОВ\n\n"
                f"❌ {err}\n\n"
                "Отредактируйте config.py и перезапустите бота."
            )
            markup = kbd.kb([[kbd.btn("🔙 НАЗАД", "main_menu")]])
            if call:
                await edit_or_send(bot, call, text, markup)
            else:
                await safe_edit(bot, chat_id, edit_id, text, markup)
            return

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🛒 ПОКУПКА НОМЕРОВ С LZT\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 Выберите режим:\n\n"
            "  • 🎲 Случайная страна — самый дешёвый лот\n"
            "  • 📦 Оптовая — купить N самых дешёвых\n"
            "  • 📥 Импорт — войти в уже купленные лоты\n\n"
            "💳 Все покупки идут с баланса «Для покупки аккаунтов».\n"
            "🚫 Лоты со спамблоком не покупаются."
        )
        markup = kbd.buy_menu()
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "buy_menu")
    @admin_required
    async def cb_buy_menu(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        ctx.buy_ctx.pop(call.from_user.id, None)
        await _render_buy_menu(call.message.chat.id, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "buy_single_random")
    @admin_required
    async def cb_buy_single_random(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        ctx.buy_ctx[uid] = {"count": 1}
        ctx.user_state[uid] = {"state": "buy_price"}

        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🎲 ПОКУПКА — СЛУЧАЙНАЯ СТРАНА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💰 Введите максимальную цену в ₽\n"
            "(например: 50)\n\n"
            "💳 Списание пойдёт с баланса «Для покупки аккаунтов».\n"
            "🚫 Лоты со спамблоком будут пропущены.",
            kbd.buy_back(),
        )

    @bot.callback_query_handler(func=lambda c: c.data == "buy_bulk")
    @admin_required
    async def cb_buy_bulk(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        ctx.buy_ctx[uid] = {"count": 0}
        ctx.user_state[uid] = {"state": "buy_bulk_count"}

        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📦 ОПТОВАЯ ЗАКУПКА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔢 Сколько номеров купить? (1–100)\n\n"
            "💳 Списание пойдёт с баланса «Для покупки аккаунтов».\n"
            "🚫 Лоты со спамблоком будут пропущены.",
            kbd.buy_back(),
        )

    @bot.callback_query_handler(func=lambda c: c.data == "buy_import")
    @admin_required
    async def cb_buy_import(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id

        err = _lzt_err()
        if err:
            await edit_or_send(
                bot, call, f"❌ {err}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "buy_menu")]]),
            )
            return

        if ctx.has_running_task(uid):
            await edit_or_send(bot, call, "⏳ Уже идёт...", kbd.back_main())
            return

        task = asyncio.create_task(
            _import_purchases(call.message.chat.id,
                              call.message.message_id, uid)
        )
        ctx.set_running_task(uid, task)

    @bot.callback_query_handler(func=lambda c: c.data == "buy_stop")
    @admin_required
    async def cb_buy_stop(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "⏹ Останавливаю...")
        ctx.buy_cancel[call.from_user.id] = True

    async def state_buy_price(message, state):
        uid = message.from_user.id
        try:
            max_price = float((message.text or "").strip().replace(",", "."))
            if max_price <= 0:
                raise ValueError
        except ValueError:
            await bot.send_message(message.chat.id, "❌ Введите число")
            return

        ctx.user_state.pop(uid, None)
        await _run_single_buy(message.chat.id, uid, max_price)

    async def state_buy_bulk_count(message, state):
        uid = message.from_user.id
        raw = (message.text or "").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= 100):
            await bot.send_message(message.chat.id, "❌ Число 1–100")
            return

        ctx.buy_ctx.setdefault(uid, {})["count"] = int(raw)
        ctx.user_state[uid] = {"state": "buy_bulk_price"}

        await bot.send_message(
            message.chat.id,
            f"📦 Номеров: {raw}\n\n"
            "💰 Введите максимальную цену в ₽ за штуку:",
            reply_markup=kbd.buy_back(),
        )

    async def state_buy_bulk_price(message, state):
        uid = message.from_user.id
        try:
            max_price = float((message.text or "").strip().replace(",", "."))
            if max_price <= 0:
                raise ValueError
        except ValueError:
            await bot.send_message(message.chat.id, "❌ Введите число")
            return

        count = ctx.buy_ctx.get(uid, {}).get("count", 1)
        ctx.user_state.pop(uid, None)
        await _run_bulk_buy(message.chat.id, uid, count, max_price)

    async def state_buy_email_code(message, state):
        uid = message.from_user.id
        fut = _EMAIL_WAITERS.get(uid)
        if fut is None or fut.done():
            await bot.send_message(message.chat.id,
                                   "ℹ️ Сейчас код не ожидается.")
            return
        fut.set_result((message.text or "").strip())

    if not hasattr(ctx, "_buy_state_handlers"):
        ctx._buy_state_handlers = {}
    ctx._buy_state_handlers.update({
        "buy_price": state_buy_price,
        "buy_bulk_count": state_buy_bulk_count,
        "buy_bulk_price": state_buy_bulk_price,
        "buy_email_code": state_buy_email_code,
    })

    async def _bind_email_interactive(chat_id, uid, phone, session_str):
        """
        Привязывает почту через чат: запрашивает код, ждёт его от
        пользователя и подтверждает. Возвращает (ok, msg, session_str).
        """
        if not LOGIN_EMAIL_SHOWN:
            return False, "LOGIN_EMAIL не задан", session_str

        ok, msg, bind_ctx, length = await lzt.request_bind_email(
            phone, session_str)
        if not ok:
            return False, msg, session_str

        fut = asyncio.get_running_loop().create_future()
        _EMAIL_WAITERS[uid] = fut
        ctx.user_state[uid] = {"state": "buy_email_code"}

        setup_note = ""
        if isinstance(bind_ctx, dict) and bind_ctx.get("mode") == "setup":
            setup_note = (
                "\nℹ️ У аккаунта ещё не было почты для входа, поэтому "
                "Telegram также отправил служебный код входа В САМ "
                "АККАУНТ — его вводить не нужно, жди именно код из "
                "письма на email.\n"
            )

        await bot.send_message(
            chat_id,
            "📧 ПРИВЯЗКА ПОЧТЫ\n\n"
            f"📞 Номер: {phone}\n"
            f"📧 Email: {LOGIN_EMAIL_SHOWN}\n"
            f"{setup_note}\n"
            f"Введи {length}-значный код из письма.\n"
            "Чтобы пропустить привязку — отправь «-».",
        )
        try:
            code = await asyncio.wait_for(fut, timeout=EMAIL_CODE_WAIT)
        except asyncio.TimeoutError:
            code = None
        finally:
            _EMAIL_WAITERS.pop(uid, None)
            ctx.user_state.pop(uid, None)

        if not code or code == "-":
            await bot.send_message(
                chat_id, f"⏭ Почта для {phone} не привязана (пропущено).")
            return False, "пропущено", session_str

        ok, msg, new_session = await lzt.confirm_bind_email(
            phone, session_str, bind_ctx, code)
        if ok:
            await bot.send_message(
                chat_id, f"✅ Почта {LOGIN_EMAIL_SHOWN} привязана к {phone}.")
            return True, "OK", new_session
        await bot.send_message(
            chat_id, f"❌ Почта для {phone} не привязана: {msg}")
        return False, msg, session_str

    async def _save_bought(phone, session_str, two_fa, price,
                           item_country, item_id):
        """
        Сохраняет купленный аккаунт в БД.

        Регистрация в lzt_purchases (status='bought') выполняется здесь же
        и НЕ глушится: если запись падает, пишем в лог error. Это гарантия,
        что даже при сбое add_account запись о покупке останется.
        """
        phone = fmt_phone(phone)
        safe_country = _safe_country(item_country)
        int_price = int(price or 0)

        # 1. Регистрируем покупку сразу — до любых других операций.
        _log_lzt_purchase(
            item_id=item_id, phone=phone, price=int_price,
            country=safe_country, status="bought",
        )

        # 2. Пытаемся сохранить сам аккаунт.
        try:
            db.add_account(
                phone, session_str, two_fa or "",
                name=phone,
                country_code=get_country_code(phone),
                price=int_price,
                bought_from="lzt",
                lzt_item_id=item_id,
            )
        except Exception as e:
            logger.error(f"[buy.db] add_account: {type(e).__name__}: {e}")
            try:
                db.update_lzt_purchase(item_id, "failed",
                                       phone=phone, error=str(e)[:200])
            except Exception as e2:
                logger.error(
                    f"[buy.db] update_lzt_purchase({item_id}) failed: "
                    f"{type(e2).__name__}: {e2}"
                )
            return None

        acc = db.get_account_by_phone(phone)
        if not acc:
            logger.error(f"[buy.db] {phone}: аккаунт не найден после add")
            try:
                db.update_lzt_purchase(item_id, "failed",
                                       phone=phone,
                                       error="account not in db")
            except Exception as e:
                logger.error(
                    f"[buy.db] update_lzt_purchase({item_id}) failed: "
                    f"{type(e).__name__}: {e}"
                )
            return None

        if ctx.schedule_auto_clean:
            try:
                ctx.schedule_auto_clean(acc.id)
            except Exception as e:
                logger.warning(f"[buy] schedule_auto_clean: {e}")

        try:
            db.update_lzt_purchase(item_id, "logged_in", phone=phone)
        except Exception as e:
            logger.error(
                f"[buy.db] update_lzt_purchase({item_id}, logged_in): "
                f"{type(e).__name__}: {e}"
            )

        db.stat_added(1)
        db.stat_bought_lzt(1, spent_rub=int_price)
        return acc

    async def _run_single_buy(chat_id, uid, max_price):
        ctx.buy_cancel[uid] = False

        msg = await bot.send_message(
            chat_id,
            "⏳ Покупка...\n\n"
            f"💰 до {max_price} ₽\n"
            f"🌍 любая страна\n"
            f"📄 Сканирую до 50 страниц LZT\n"
            f"🚫 Пропускаю спамблок",
            reply_markup=kbd.buy_stop_kb(),
        )

        async def _worker():
            market = lzt.make_market()
            balance, balance_id = await lzt.get_balance_with_retry(market)

            if balance_id is None:
                await safe_edit(
                    bot, chat_id, msg.message_id,
                    "❌ ПОКУПКА НЕ ЗАПУЩЕНА\n\n"
                    "Не удалось определить счёт для покупок: "
                    "LZT не ответил после 3 попыток.\n\n"
                    "Покупка остановлена, чтобы деньги не ушли "
                    "с основного баланса. Попробуйте позже.",
                    kbd.back_main(),
                )
                return

            balance_line = (
                f"💳 Счёт для покупок: {balance:.2f} ₽"
                if balance is not None
                else "💳 Счёт для покупок найден"
            )

            await safe_edit(
                bot, chat_id, msg.message_id,
                "⏳ Покупка...\n\n"
                f"💰 до {max_price} ₽\n"
                f"🌍 любая страна\n"
                f"{balance_line}\n"
                f"📄 Сканирую до 50 страниц LZT\n"
                f"🚫 Пропускаю спамблок",
                kbd.buy_stop_kb(),
            )

            if balance is not None and balance < 1:
                await safe_edit(
                    bot, chat_id, msg.message_id,
                    f"❌ Нулевой баланс: {balance:.2f} ₽",
                    kbd.back_main(),
                )
                return

            skip_ids = set()
            attempts = 0

            while not ctx.buy_cancel.get(uid) and attempts < 20:
                attempts += 1

                ok, item_id, phone, price, item_country, login_data, err = \
                    await lzt.buy_one(
                        market, max_price,
                        balance_id=balance_id,
                        skip_ids=skip_ids,
                    )

                if not ok:
                    if err == "only_spam":
                        await safe_edit(
                            bot, chat_id, msg.message_id,
                            "🚫 ВСЕ НАЙДЕННЫЕ ЛОТЫ — СПАМБЛОК\n\n"
                            f"💰 до {max_price} ₽\n\n"
                            "Просмотрено до 50 страниц LZT —\n"
                            "все лоты помечены спамблоком.\n"
                            "💡 Попробуйте увеличить цену — "
                            "чистые аккаунты обычно дороже.",
                            kbd.back_main(),
                        )
                        return
                    if err == "no_lots":
                        await safe_edit(
                            bot, chat_id, msg.message_id,
                            "📭 Лоты не найдены\n\n"
                            f"💰 до {max_price} ₽\n\n"
                            "Просмотрено до 50 страниц LZT — "
                            "подходящих лотов нет.\n"
                            "💡 Попробуйте увеличить цену.",
                            kbd.back_main(),
                        )
                        return
                    await asyncio.sleep(lzt.BUY_DELAY)
                    continue

                # Лот куплен — фиксируем в UI
                country_ru_label = (
                    country_ru(item_country) if item_country else "—"
                )
                await safe_edit(
                    bot, chat_id, msg.message_id,
                    f"✅ Куплен лот {item_id} за {price} ₽\n"
                    f"🌍 {country_ru_label}\n"
                    f"📞 {phone or '?'}\n\n⏳ Вход в аккаунт...",
                    kbd.buy_stop_kb(),
                )

                # Если номер не извлёкся — фиксируем покупку без телефона
                if not phone:
                    _log_lzt_purchase(
                        item_id=item_id, phone=None, price=price,
                        country=item_country, status="failed",
                        error="phone_not_found",
                    )
                    skip_ids.add(item_id)
                    continue

                phone = fmt_phone(phone)

                password = None
                for key in ("telegram_password", "password", "twofa", "2fa",
                            "two_fa", "cloud_password"):
                    v = (login_data or {}).get(key)
                    if v:
                        password = str(v)
                        break

                def _is_cancelled():
                    return ctx.buy_cancel.get(uid, False)

                session_str, status = await lzt.retry_login_to_account(
                    phone, item_id, market, password=password,
                    ctx_cancel=_is_cancelled, max_attempts=2,
                )

                if not session_str:
                    # ▼▼▼ ГЛАВНОЕ ИСПРАВЛЕНИЕ ▼▼▼
                    # Регистрируем покупку ДО любых early-return, включая
                    # отмену. Иначе купленный лот не появляется в LZT-логах.
                    _log_lzt_purchase(
                        item_id=item_id, phone=phone, price=price,
                        country=item_country, status="failed",
                        error=status,
                    )

                    if status == "cancelled":
                        await safe_edit(
                            bot, chat_id, msg.message_id,
                            "⏹ Покупка отменена пользователем.\n\n"
                            f"🆔 Лот: {item_id}\n"
                            f"📞 {phone}\n\n"
                            "Аккаунт не сохранён, но лот уже куплен — "
                            "он в «📥 LZT-ЛОГИ» со статусом 🔴. "
                            "Вход можно повторить оттуда.",
                            kbd.back_main(),
                        )
                        return

                    await bot.send_message(
                        chat_id,
                        f"⚠️ Лот {item_id} куплен, но вход: {status}\n\n"
                        f"📞 {phone}\n\n"
                        f"💡 {lzt_error_hint(status)}\n\n"
                        "Попробуй через «📥 LZT-ЛОГИ» → "
                        "«🔁 Повторить вход».",
                    )
                    skip_ids.add(item_id)
                    continue

                new_session, new_pw, ok2fa, msg2fa = \
                    await lzt.apply_password_2fa_after_login(
                        phone, session_str, password or "",
                    )
                if ok2fa:
                    session_str = new_session
                    password = new_pw
                    if msg2fa == "OK":
                        logger.info(f"[buy.single] {phone}: 2FA → PASSWORD_2FA")
                else:
                    logger.warning(
                        f"[buy.single] {phone}: 2FA не изменён: {msg2fa}"
                    )

                email_ok, email_msg, session_str = \
                    await _bind_email_interactive(
                        chat_id, uid, phone, session_str)
                logger.info(f"[buy.single] {phone}: почта: {email_msg}")

                acc = await _save_bought(phone, session_str, password,
                                         price, item_country, item_id)

                if acc:
                    try:
                        db.log_action(
                            uid, "buy_single",
                            details=f"{phone} {price}₽ item={item_id} "
                                    f"balance_id={balance_id}",
                            account_id=acc.id,
                        )
                    except Exception as e:
                        logger.warning(f"[buy.single] log_action: {e}")

                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ АККАУНТ КУПЛЕН\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🏷 Название: {phone}\n"
                    f"📞 Номер:    {phone}\n"
                    f"🌍 Страна:   {country_ru_label}\n"
                    f"💰 Цена:     {price} ₽\n"
                    + (f"🔐 2FA:      {password}\n" if password else "")
                    + (f"📧 Почта:   ✅ {LOGIN_EMAIL_SHOWN}\n"
                       if email_ok else f"📧 Почта:   ⚠️ {email_msg}\n")
                    + f"🆔 Лот:      {item_id}\n"
                    "📌 Статус:   ⚠️ Ожидает очистки"
                )
                await safe_edit(bot, chat_id, msg.message_id, text,
                                kbd.buy_after())
                return

            await safe_edit(bot, chat_id, msg.message_id,
                            "⏹ Остановлено", kbd.back_main())

        try:
            await asyncio.wait_for(_worker(), timeout=900)
        except asyncio.TimeoutError:
            await safe_edit(
                bot, chat_id, msg.message_id,
                "⏱️ Покупка заняла больше 5 минут — остановлено.\n\n"
                "Проверьте LZT-логи: возможно, лот был куплен, "
                "но вход не завершён.",
                kbd.back_main(),
            )
        except Exception as e:
            logger.exception(f"[buy.single] {e}")
            await safe_edit(bot, chat_id, msg.message_id,
                            f"❌ Ошибка: {type(e).__name__}",
                            kbd.back_main())
        finally:
            ctx.buy_cancel.pop(uid, None)

    async def _run_bulk_buy(chat_id, uid, count, max_price):
        ctx.buy_cancel[uid] = False

        msg = await bot.send_message(
            chat_id,
            "⏳ Оптовая покупка...\n\n"
            f"📦 {count} шт. по {max_price} ₽\n"
            f"📄 Сканирую до 50 страниц LZT\n"
            f"🚫 Пропускаю спамблок",
            reply_markup=kbd.buy_stop_kb(),
        )

        ok_count = fail_count = attempts = 0
        max_attempts = count * 10
        skip_ids = set()
        report = []

        try:
            market = lzt.make_market()
            balance, balance_id = await lzt.get_balance_with_retry(market)

            if balance_id is None:
                await safe_edit(
                    bot, chat_id, msg.message_id,
                    "❌ ОПТОВАЯ ПОКУПКА НЕ ЗАПУЩЕНА\n\n"
                    "Не удалось определить счёт для покупок: "
                    "LZT не ответил после 3 попыток.\n\n"
                    "Покупка остановлена, чтобы деньги не ушли "
                    "с основного баланса. Попробуйте позже.",
                    kbd.back_main(),
                )
                return

            logger.info(
                f"[buy.bulk] используем balance_id={balance_id} "
                f"({balance} ₽)"
            )

            if balance is not None and balance < max_price:
                await safe_edit(
                    bot, chat_id, msg.message_id,
                    f"❌ Мало средств на счёте для покупок: "
                    f"{balance:.2f} ₽ (нужно минимум {max_price:.2f} ₽)",
                    kbd.back_main(),
                )
                return

            while (ok_count < count and attempts < max_attempts
                   and not ctx.buy_cancel.get(uid)):
                attempts += 1

                balance_str = (
                    f"\n\n💳 Счёт для покупок: {balance:.2f} ₽"
                    if balance is not None else ""
                )
                await safe_edit(
                    bot, chat_id, msg.message_id,
                    "📦 ОПТОВАЯ ПОКУПКА\n\n"
                    f"✅ Куплено: {ok_count}/{count}\n"
                    f"❌ Ошибок:  {fail_count}\n"
                    f"🔁 Попытка: {attempts}/{max_attempts}"
                    f"{balance_str}",
                    kbd.buy_stop_kb(),
                )

                ok, item_id, phone, price, item_country, login_data, err = \
                    await lzt.buy_one(
                        market, max_price,
                        balance_id=balance_id,
                        skip_ids=skip_ids,
                    )

                if not ok:
                    if err == "no_lots":
                        report.append(
                            "📭 лоты закончились (просмотрено 50 страниц)"
                        )
                        break
                    if err == "only_spam":
                        report.append(
                            "🚫 остались только лоты со спамблоком"
                        )
                        break
                    fail_count += 1
                    await asyncio.sleep(lzt.BUY_DELAY)
                    continue

                new_balance, _ = await lzt.get_balance(market)
                if new_balance is not None:
                    balance = new_balance
                elif balance is not None:
                    balance -= float(price or 0) * 1.03

                if not phone:
                    _log_lzt_purchase(
                        item_id=item_id, phone=None, price=price,
                        country=item_country, status="failed",
                        error="phone_not_found",
                    )
                    skip_ids.add(item_id)
                    fail_count += 1
                    continue

                phone = fmt_phone(phone)

                password = None
                for key in ("telegram_password", "password", "twofa", "2fa",
                            "two_fa", "cloud_password"):
                    v = (login_data or {}).get(key)
                    if v:
                        password = str(v)
                        break

                def _is_cancelled():
                    return ctx.buy_cancel.get(uid, False)

                session_str, status = await lzt.retry_login_to_account(
                    phone, item_id, market, password=password,
                    ctx_cancel=_is_cancelled, max_attempts=2,
                )

                if not session_str:
                    fail_count += 1
                    skip_ids.add(item_id)
                    country_ru_label = (
                        country_ru(item_country) if item_country else "—"
                    )
                    report.append(
                        f"⚠️ {phone} ({country_ru_label}) — {status}"
                    )
                    _log_lzt_purchase(
                        item_id=item_id, phone=phone, price=price,
                        country=item_country, status="failed",
                        error=status,
                    )
                    if status == "cancelled":
                        break
                    continue

                new_session, new_pw, ok2fa, msg2fa = \
                    await lzt.apply_password_2fa_after_login(
                        phone, session_str, password or "",
                    )
                if ok2fa:
                    session_str = new_session
                    password = new_pw
                    if msg2fa == "OK":
                        logger.info(f"[buy.bulk] {phone}: 2FA → PASSWORD_2FA")
                else:
                    logger.warning(
                        f"[buy.bulk] {phone}: 2FA не изменён: {msg2fa}"
                    )

                email_ok, email_msg, session_str = \
                    await _bind_email_interactive(
                        chat_id, uid, phone, session_str)
                logger.info(f"[buy.bulk] {phone}: почта: {email_msg}")

                acc = await _save_bought(phone, session_str, password,
                                         price, item_country, item_id)
                if acc:
                    ok_count += 1
                    country_ru_label = (
                        country_ru(item_country) if item_country else "—"
                    )
                    report.append(
                        f"✅ {phone} ({country_ru_label}) — {price} ₽ · 📧 {'да' if email_ok else email_msg}"
                    )
                    try:
                        db.log_action(
                            uid, "buy_bulk",
                            details=f"{phone} {price}₽ item={item_id} "
                                    f"balance_id={balance_id}",
                            account_id=acc.id,
                        )
                    except Exception as e:
                        logger.warning(f"[buy.bulk] log_action: {e}")
                else:
                    fail_count += 1

                await asyncio.sleep(lzt.BUY_DELAY)

            header = (
                "📊 ОПТОВАЯ ЗАКУПКА ЗАВЕРШЕНА"
                if ok_count >= count
                else "⚠️ ЗАКУПКА ОСТАНОВЛЕНА"
            )
            text = (
                f"{header}\n\n"
                f"✅ Куплено: {ok_count}/{count}\n"
                f"❌ Ошибок:  {fail_count}\n\n"
                "Детали:\n" + "\n".join(report[:15])
            )
            db.log_action(uid, "buy_bulk_summary",
                          details=f"ok={ok_count} fail={fail_count} "
                                  f"balance_id={balance_id}")
            await safe_edit(bot, chat_id, msg.message_id, text,
                            kbd.back_main())

        except Exception as e:
            logger.exception(f"[buy.bulk] {e}")
            await safe_edit(bot, chat_id, msg.message_id,
                            f"❌ Ошибка: {type(e).__name__}",
                            kbd.back_main())
        finally:
            ctx.buy_cancel.pop(uid, None)

    async def _import_purchases(chat_id, msg_id, uid):
        ctx.buy_cancel[uid] = False

        try:
            market = lzt.make_market()
            items = await lzt.list_purchases(market)

            if not items:
                await safe_edit(
                    bot, chat_id, msg_id,
                    "📭 LZT не вернул покупок.",
                    kbd.back_main(),
                )
                return

            known_phones = {a.phone for a in db.get_accounts()}
            known_lzt_ids = db.get_known_lzt_item_ids()

            todo = []
            for it in items:
                iid = it.get("item_id") or it.get("id")
                if iid is None:
                    continue
                iid = int(iid)
                if iid in known_lzt_ids:
                    continue
                if not lzt.is_telegram_item(it):
                    continue
                todo.append((iid, it))

            if not todo:
                await safe_edit(
                    bot, chat_id, msg_id,
                    "✅ Новых лотов нет.",
                    kbd.back_main(),
                )
                return

            ok_count = fail_count = 0
            report = []

            for i, (item_id, it) in enumerate(todo, 1):
                if ctx.buy_cancel.get(uid):
                    break

                await safe_edit(
                    bot, chat_id, msg_id,
                    f"📥 ИМПОРТ {i}/{len(todo)}\n\n"
                    f"✅ {ok_count} | ❌ {fail_count}",
                    kbd.buy_stop_kb(),
                )

                login_data = await lzt.get_item_login_data(market, item_id)
                phone = None
                for key in ("phone", "phone_number", "number",
                            "telegram_phone"):
                    if login_data.get(key):
                        phone = fmt_phone(str(login_data[key]).strip())
                        break
                if not phone:
                    phone = lzt.extract_phone(str(it.get("title") or ""))
                if phone:
                    phone = fmt_phone(phone)

                if not phone:
                    fail_count += 1
                    _log_lzt_purchase(
                        item_id=item_id, phone=None, price=0,
                        country=lzt.get_country(it), status="failed",
                        error="phone_not_found",
                    )
                    continue

                if phone in known_phones:
                    # Аккаунт уже в хранилище, но записи в lzt_purchases нет —
                    # это значит, что предыдущий add_lzt_purchase когда-то
                    # упал. Заводим запись как минимум со статусом bought,
                    # чтобы лот не потерялся из LZT-логов.
                    if item_id not in known_lzt_ids:
                        _log_lzt_purchase(
                            item_id=item_id, phone=phone, price=0,
                            country=lzt.get_country(it), status="bought",
                        )
                    continue

                password = None
                for key in ("telegram_password", "password", "twofa", "2fa",
                            "two_fa", "cloud_password"):
                    if login_data.get(key):
                        password = str(login_data[key])
                        break

                def _is_cancelled():
                    return ctx.buy_cancel.get(uid, False)

                session_str, status = await lzt.retry_login_to_account(
                    phone, item_id, market, password=password,
                    ctx_cancel=_is_cancelled, max_attempts=2,
                )

                if not session_str:
                    fail_count += 1
                    report.append(f"⚠️ {phone} — {status}")
                    _log_lzt_purchase(
                        item_id=item_id, phone=phone, price=0,
                        country=lzt.get_country(it), status="failed",
                        error=status,
                    )
                    if status == "cancelled":
                        break
                    continue

                new_session, new_pw, ok2fa, msg2fa = \
                    await lzt.apply_password_2fa_after_login(
                        phone, session_str, password or "",
                    )
                if ok2fa:
                    session_str = new_session
                    password = new_pw
                    if msg2fa == "OK":
                        logger.info(f"[buy.import] {phone}: 2FA → PASSWORD_2FA")
                else:
                    logger.warning(
                        f"[buy.import] {phone}: 2FA не изменён: {msg2fa}"
                    )

                acc = await _save_bought(phone, session_str, password,
                                         0, lzt.get_country(it), item_id)
                if acc:
                    ok_count += 1
                    country_ru_label = country_ru(lzt.get_country(it)) or "—"
                    report.append(f"✅ {phone} ({country_ru_label})")
                    try:
                        db.log_action(
                            uid, "import",
                            details=f"{phone} item={item_id}",
                            account_id=acc.id,
                        )
                    except Exception as e:
                        logger.warning(f"[buy.import] log_action: {e}")
                else:
                    fail_count += 1

                await asyncio.sleep(lzt.BUY_DELAY)

            text = (
                "📊 ИМПОРТ ЗАВЕРШЁН\n\n"
                f"✅ Вошёл: {ok_count}\n"
                f"❌ Не удалось: {fail_count}\n\n"
                + "\n".join(report[:15])
            )
            db.log_action(uid, "import_summary",
                          details=f"ok={ok_count} fail={fail_count}")
            await safe_edit(bot, chat_id, msg_id, text, kbd.back_main())

        except Exception as e:
            logger.exception(f"[buy.import] {e}")
            await safe_edit(bot, chat_id, msg_id,
                            f"❌ Ошибка: {type(e).__name__}",
                            kbd.back_main())
        finally:
            ctx.buy_cancel.pop(uid, None)