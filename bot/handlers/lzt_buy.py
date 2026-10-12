import asyncio
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
    lzt_error_hint,
    fmt_phone,
    get_country_code,
)

logger = logging.getLogger(__name__)


def register(ctx):
    bot = ctx.bot

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ПРОВЕРКА ГОТОВНОСТИ LZT                          #
    # ═══════════════════════════════════════════════════════════════════ #

    def _lzt_err() -> str:
        if not lzt.LZT_AVAILABLE:
            return "LOLZTEAM не установлен: pip install LOLZTEAM"
        if not lzt.LZT_API_TOKEN:
            return "LZT_API_TOKEN не задан в config.py"
        return ""

    # ═══════════════════════════════════════════════════════════════════ #
    #                          МЕНЮ ПОКУПКИ                                 #
    # ═══════════════════════════════════════════════════════════════════ #

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
            "  • 🌍 Конкретная страна — фильтр по стране\n"
            "  • 📦 Оптовая — купить N самых дешёвых\n"
            "  • 📥 Импорт — войти в уже купленные лоты"
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

    # ═══════════════════════════════════════════════════════════════════ #
    #                     FSM: СЛУЧАЙНАЯ СТРАНА                             #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "buy_single_random")
    @admin_required
    async def cb_buy_single_random(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        ctx.buy_ctx[uid] = {"country": None, "count": 1}
        ctx.user_state[uid] = {"state": "buy_price"}

        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🎲 ПОКУПКА — СЛУЧАЙНАЯ СТРАНА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💰 Введите максимальную цену в ₽\n"
            "(например: 50)",
            kbd.buy_back(),
        )

    # ═══════════════════════════════════════════════════════════════════ #
    #                     FSM: КОНКРЕТНАЯ СТРАНА                            #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "buy_single_country")
    @admin_required
    async def cb_buy_single_country(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        ctx.buy_ctx[uid] = {"country": "", "count": 1}
        ctx.user_state[uid] = {"state": "buy_country"}

        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🌍 ПОКУПКА — КОНКРЕТНАЯ СТРАНА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🌐 Введите название страны (можно часть):\n"
            "Индонезия, Indonesia, США…",
            kbd.buy_back(),
        )

    # ═══════════════════════════════════════════════════════════════════ #
    #                          FSM: ОПТОВАЯ                                 #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "buy_bulk")
    @admin_required
    async def cb_buy_bulk(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        ctx.buy_ctx[uid] = {"country": None, "count": 0}
        ctx.user_state[uid] = {"state": "buy_bulk_count"}

        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📦 ОПТОВАЯ ЗАКУПКА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔢 Сколько номеров купить? (1–100)",
            kbd.buy_back(),
        )

    # ═══════════════════════════════════════════════════════════════════ #
    #                          FSM: ИМПОРТ                                  #
    # ═══════════════════════════════════════════════════════════════════ #

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

    # ═══════════════════════════════════════════════════════════════════ #
    #                          СТОП                                         #
    # ═══════════════════════════════════════════════════════════════════ #

    @bot.callback_query_handler(func=lambda c: c.data == "buy_stop")
    @admin_required
    async def cb_buy_stop(call: types.CallbackQuery):
        await bot.answer_callback_query(call.id, "⏹ Останавливаю...")
        ctx.buy_cancel[call.from_user.id] = True

    # ═══════════════════════════════════════════════════════════════════ #
    #                       STATE HANDLERS (FSM)                            #
    # ═══════════════════════════════════════════════════════════════════ #

    async def state_buy_country(message, state):
        uid = message.from_user.id
        country = (message.text or "").strip()
        if len(country) < 2:
            await bot.send_message(message.chat.id, "❌ Слишком короткое")
            return

        ctx.buy_ctx.setdefault(uid, {})["country"] = country
        ctx.user_state[uid] = {"state": "buy_price"}

        await bot.send_message(
            message.chat.id,
            f"🌍 Страна: {country}\n\n"
            "💰 Введите максимальную цену в ₽:",
            reply_markup=kbd.buy_back(),
        )

    async def state_buy_price(message, state):
        uid = message.from_user.id
        try:
            max_price = float((message.text or "").strip().replace(",", "."))
            if max_price <= 0:
                raise ValueError
        except ValueError:
            await bot.send_message(message.chat.id, "❌ Введите число")
            return

        country = ctx.buy_ctx.get(uid, {}).get("country")
        ctx.user_state.pop(uid, None)
        await _run_single_buy(message.chat.id, uid, max_price, country)

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

    # Регистрируем FSM в ctx (используется в add_account.py → text_router)
    if not hasattr(ctx, "_buy_state_handlers"):
        ctx._buy_state_handlers = {}
    ctx._buy_state_handlers.update({
        "buy_country": state_buy_country,
        "buy_price": state_buy_price,
        "buy_bulk_count": state_buy_bulk_count,
        "buy_bulk_price": state_buy_bulk_price,
    })

    # ═══════════════════════════════════════════════════════════════════ #
    #                       СОХРАНЕНИЕ КУПЛЕННОГО                           #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _save_bought(phone, session_str, two_fa, price,
                           item_country, item_id):
        """Сохраняет купленный аккаунт в БД + записывает LZT-покупку."""
        phone = fmt_phone(phone)

        # Запись в lzt_purchases
        try:
            db.add_lzt_purchase(
                item_id=item_id, phone=phone, price=int(price or 0),
                country=item_country or "", status="bought",
            )
        except Exception as e:
            logger.warning(f"[buy.db] add_lzt_purchase: {e}")

        # Запись в accounts
        try:
            db.add_account(
                phone, session_str, two_fa or "",
                name=phone,
                country_code=get_country_code(phone),
                price=int(price or 0),
                bought_from="lzt",
                lzt_item_id=item_id,
            )
        except Exception as e:
            logger.error(f"[buy.db] add_account: {type(e).__name__}: {e}")
            try:
                db.update_lzt_purchase(item_id, "failed", error=str(e)[:200])
            except Exception:
                pass
            return None

        acc = db.get_account_by_phone(phone)
        if not acc:
            try:
                db.update_lzt_purchase(item_id, "failed",
                                       error="account not in db")
            except Exception:
                pass
            return None

        # Планируем автоочистку
        if ctx.schedule_auto_clean:
            try:
                ctx.schedule_auto_clean(acc.id)
            except Exception as e:
                logger.warning(f"[buy] schedule_auto_clean: {e}")

        # Обновляем LZT-запись
        try:
            db.update_lzt_purchase(item_id, "logged_in", phone=phone)
        except Exception:
            pass

        db.stat_added(1)
        db.stat_bought_lzt(1, spent_rub=int(price or 0))
        return acc

    # ═══════════════════════════════════════════════════════════════════ #
    #                       РАЗОВАЯ ПОКУПКА                                 #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _run_single_buy(chat_id, uid, max_price, country):
        ctx.buy_cancel[uid] = False
        msg = await bot.send_message(
            chat_id,
            "⏳ Покупка...\n\n"
            f"💰 до {max_price} ₽\n"
            f"🌍 {country or 'любая страна'}",
            reply_markup=kbd.buy_stop_kb(),
        )

        try:
            market = lzt.make_market()
            balance, balance_id = await lzt.get_balance(market)

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
                    await lzt.buy_one(market, max_price, balance_id, balance,
                                      skip_ids=skip_ids, country=country)

                if not ok:
                    if err == "no_money":
                        await safe_edit(
                            bot, chat_id, msg.message_id,
                            "💸 Кончились деньги на LZT",
                            kbd.back_main(),
                        )
                        return
                    if err == "no_lots":
                        await safe_edit(
                            bot, chat_id, msg.message_id,
                            "📭 Лоты не найдены\n\n"
                            f"💰 до {max_price} ₽\n"
                            f"🌍 {country or 'любая'}",
                            kbd.back_main(),
                        )
                        return
                    await asyncio.sleep(lzt.BUY_DELAY)
                    continue

                # Лот куплен → показываем
                await safe_edit(
                    bot, chat_id, msg.message_id,
                    f"✅ Куплен лот {item_id} за {price} ₽\n"
                    f"📞 {phone or '?'}\n\n⏳ Вход в аккаунт...",
                    kbd.buy_stop_kb(),
                )

                # Извлекаем 2FA-пароль
                password = None
                for key in ("telegram_password", "password", "twofa", "2fa",
                            "two_fa", "cloud_password"):
                    v = (login_data or {}).get(key)
                    if v:
                        password = str(v)
                        break

                if not phone:
                    skip_ids.add(item_id)
                    continue

                phone = fmt_phone(phone)
                session_str, status = await lzt.login_to_account(
                    phone, item_id, market, password=password
                )

                if not session_str:
                    try:
                        db.add_lzt_purchase(
                            item_id=item_id, phone=phone,
                            price=int(price or 0),
                            country=item_country or "",
                            status="failed", error=status,
                        )
                    except Exception:
                        pass
                    await bot.send_message(
                        chat_id,
                        f"⚠️ Лот {item_id} куплен, но вход: {status}\n\n"
                        f"💡 {lzt_error_hint(status)}\n\n"
                        "Попробуй через «📥 LZT-ЛОГИ» → "
                        "«🔁 Повторить вход».",
                    )
                    skip_ids.add(item_id)
                    continue

                # Успех → сохраняем
                acc = await _save_bought(phone, session_str, password,
                                         price, item_country, item_id)

                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ АККАУНТ КУПЛЕН\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🏷 Название: {phone}\n"
                    f"📞 Номер:    {phone}\n"
                    f"🌍 Страна:   {item_country or '—'}\n"
                    f"💰 Цена:     {price} ₽\n"
                    + (f"🔐 2FA:      {password}\n" if password else "")
                    + f"🆔 Лот:      {item_id}\n"
                    "📌 Статус:   ⚠️ Ожидает очистки"
                )
                await safe_edit(bot, chat_id, msg.message_id, text,
                                kbd.buy_after())
                db.log_action(
                    uid, "buy_single",
                    details=f"{phone} {price}₽ item={item_id}",
                    account_id=acc.id if acc else None,
                )
                return

            await safe_edit(bot, chat_id, msg.message_id,
                            "⏹ Остановлено", kbd.back_main())

        except Exception as e:
            logger.exception(f"[buy.single] {e}")
            await safe_edit(bot, chat_id, msg.message_id,
                            f"❌ Ошибка: {type(e).__name__}",
                            kbd.back_main())
        finally:
            ctx.buy_cancel.pop(uid, None)

    # ═══════════════════════════════════════════════════════════════════ #
    #                       ОПТОВАЯ ПОКУПКА                                 #
    # ═══════════════════════════════════════════════════════════════════ #

    async def _run_bulk_buy(chat_id, uid, count, max_price):
        ctx.buy_cancel[uid] = False
        msg = await bot.send_message(
            chat_id,
            "⏳ Оптовая покупка...\n\n"
            f"📦 {count} шт. по {max_price} ₽",
            reply_markup=kbd.buy_stop_kb(),
        )

        ok_count = fail_count = attempts = 0
        max_attempts = count * 10
        skip_ids = set()
        report = []

        try:
            market = lzt.make_market()
            balance, balance_id = await lzt.get_balance(market)

            if balance is not None and balance < max_price:
                await safe_edit(
                    bot, chat_id, msg.message_id,
                    f"❌ Мало средств: {balance:.2f} ₽",
                    kbd.back_main(),
                )
                return

            while (ok_count < count and attempts < max_attempts
                   and not ctx.buy_cancel.get(uid)):
                attempts += 1

                await safe_edit(
                    bot, chat_id, msg.message_id,
                    "📦 ОПТОВАЯ ПОКУПКА\n\n"
                    f"✅ Куплено: {ok_count}/{count}\n"
                    f"❌ Ошибок:  {fail_count}\n"
                    f"🔁 Попытка: {attempts}/{max_attempts}"
                    + (f"\n\n💰 Баланс: {balance:.2f} ₽"
                       if balance else ""),
                    kbd.buy_stop_kb(),
                )

                ok, item_id, phone, price, item_country, login_data, err = \
                    await lzt.buy_one(market, max_price, balance_id, balance,
                                      skip_ids=skip_ids)

                if not ok:
                    if err == "no_money":
                        report.append("💸 кончились деньги")
                        break
                    if err == "no_lots":
                        report.append("📭 лоты закончились")
                        break
                    fail_count += 1
                    await asyncio.sleep(lzt.BUY_DELAY)
                    continue

                # Обновляем баланс
                new_balance, _ = await lzt.get_balance(market)
                if new_balance is not None:
                    balance = new_balance
                elif balance is not None:
                    balance -= float(price or 0) * 1.03

                password = None
                for key in ("telegram_password", "password", "twofa", "2fa",
                            "two_fa", "cloud_password"):
                    v = (login_data or {}).get(key)
                    if v:
                        password = str(v)
                        break

                if not phone:
                    skip_ids.add(item_id)
                    fail_count += 1
                    continue

                phone = fmt_phone(phone)
                session_str, status = await lzt.login_to_account(
                    phone, item_id, market, password=password
                )

                if not session_str:
                    fail_count += 1
                    skip_ids.add(item_id)
                    report.append(f"⚠️ {phone} — {status}")
                    try:
                        db.add_lzt_purchase(
                            item_id=item_id, phone=phone,
                            price=int(price or 0),
                            country=item_country or "",
                            status="failed", error=status,
                        )
                    except Exception:
                        pass
                    continue

                acc = await _save_bought(phone, session_str, password,
                                         price, item_country, item_id)
                if acc:
                    ok_count += 1
                    report.append(f"✅ {phone} — {price} ₽")
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
            db.log_action(uid, "buy_bulk",
                          details=f"ok={ok_count} fail={fail_count}")
            await safe_edit(bot, chat_id, msg.message_id, text,
                            kbd.back_main())

        except Exception as e:
            logger.exception(f"[buy.bulk] {e}")
            await safe_edit(bot, chat_id, msg.message_id,
                            f"❌ Ошибка: {type(e).__name__}",
                            kbd.back_main())
        finally:
            ctx.buy_cancel.pop(uid, None)

    # ═══════════════════════════════════════════════════════════════════ #
    #                          ИМПОРТ                                       #
    # ═══════════════════════════════════════════════════════════════════ #

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

                # Пробуем вытащить номер
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
                    try:
                        db.add_lzt_purchase(
                            item_id=item_id, phone=None, price=0,
                            country=lzt.get_country(it),
                            status="failed", error="phone_not_found",
                        )
                    except Exception:
                        pass
                    continue

                if phone in known_phones:
                    continue

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
                    fail_count += 1
                    report.append(f"⚠️ {phone} — {status}")
                    try:
                        db.add_lzt_purchase(
                            item_id=item_id, phone=phone, price=0,
                            country=lzt.get_country(it),
                            status="failed", error=status,
                        )
                    except Exception:
                        pass
                    continue

                acc = await _save_bought(phone, session_str, password,
                                         0, lzt.get_country(it), item_id)
                if acc:
                    ok_count += 1
                    report.append(f"✅ {phone}")
                else:
                    fail_count += 1

                await asyncio.sleep(lzt.BUY_DELAY)

            text = (
                "📊 ИМПОРТ ЗАВЕРШЁН\n\n"
                f"✅ Вошёл: {ok_count}\n"
                f"❌ Не удалось: {fail_count}\n\n"
                + "\n".join(report[:15])
            )
            db.log_action(uid, "import",
                          details=f"ok={ok_count} fail={fail_count}")
            await safe_edit(bot, chat_id, msg_id, text, kbd.back_main())

        except Exception as e:
            logger.exception(f"[buy.import] {e}")
            await safe_edit(bot, chat_id, msg_id,
                            f"❌ Ошибка: {type(e).__name__}",
                            kbd.back_main())
        finally:
            ctx.buy_cancel.pop(uid, None)