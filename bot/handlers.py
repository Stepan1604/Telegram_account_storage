import asyncio
import csv
import io
import logging
import re
import traceback
from datetime import datetime, timedelta

from telebot import types
from telethon import TelegramClient, errors, functions
from telethon.sessions import StringSession
from telethon.tl.functions.auth import ResetAuthorizationsRequest
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.messages import (
    DeleteChatUserRequest,
    DeleteHistoryRequest,
)
from telethon.tl.types import AccountDaysTTL

import database as db
from . import keyboards as kbd
from . import lzt
from config import (
    API_ID, API_HASH, ADMINS, COUNTRIES, ADMIN_ID,
    PASSWORD_2FA, SESSION_TTL_DAYS,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 5
AUTO_CLEAN_DELAY = 24 * 3600 + 10

_clients: dict = {}
_lock = asyncio.Lock()
auto_clean_tasks: dict = {}


# ═══════════════════════════════════════════════════════════════════════════ #
#                                TELETHON                                     #
# ═══════════════════════════════════════════════════════════════════════════ #

async def _get_client(phone: str, session_string: str) -> TelegramClient:
    async with _lock:
        c = _clients.get(phone)
        if c and c.is_connected():
            return c
        if c:
            try:
                await c.disconnect()
            except Exception as e:
                logger.warning(f"[client] disconnect {phone}: {e}")
        c = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await asyncio.wait_for(c.connect(), timeout=15)
        _clients[phone] = c
        return c


async def close_client(phone: str) -> None:
    async with _lock:
        c = _clients.pop(phone, None)
    if c:
        try:
            await c.disconnect()
        except Exception as e:
            logger.warning(f"[client] disconnect {phone}: {e}")


async def is_alive(session_string: str, phone: str):
    if not session_string:
        return False, "Нет session"
    try:
        c = await _get_client(phone, session_string)
        if not await asyncio.wait_for(c.is_user_authorized(), timeout=10):
            await close_client(phone)
            return False, "Сессия не авторизована"
        me = await asyncio.wait_for(c.get_me(), timeout=10)
        return (True, me.first_name or "OK") if me else (False, "Нет данных")
    except errors.AuthKeyUnregisteredError:
        await close_client(phone)
        return False, "Ключ аннулирован"
    except errors.UserDeactivatedBanError:
        return False, "Аккаунт забанен"
    except errors.UserDeactivatedError:
        return False, "Аккаунт деактивирован"
    except asyncio.TimeoutError:
        return False, "Таймаут"
    except Exception as e:
        logger.error(f"[is_alive] {phone}: {type(e).__name__}: {e}")
        logger.debug(traceback.format_exc())
        return False, f"Ошибка: {str(e)[:120]}"


async def read_login_code(session_string: str, phone: str):
    if not session_string:
        return None, "Нет session"
    try:
        c = await _get_client(phone, session_string)
        if not await c.is_user_authorized():
            await close_client(phone)
            return None, "Сессия не авторизована"
        await asyncio.sleep(2)
        async for msg in c.iter_messages(777000, limit=15):
            if msg and msg.message:
                m = re.search(r"\b(\d{5})\b", msg.message)
                if m:
                    return m.group(1), "OK"
        return None, "Код не найден в переписке Telegram"
    except errors.AuthKeyUnregisteredError:
        await close_client(phone)
        return None, "Ключ аннулирован"
    except Exception as e:
        logger.error(f"[read_code] {phone}: {type(e).__name__}: {e}")
        return None, f"Ошибка: {str(e)[:150]}"


async def get_account_info(session_string: str, phone: str) -> dict:
    """Расширенная инфа: профиль + сессии + чаты/каналы/группы."""
    result = {
        "ok": False, "error": "",
        "first_name": "", "last_name": "", "username": "",
        "user_id": 0,
        "sessions": 0, "chats": 0, "channels": 0, "groups": 0,
        "total_dialogs": 0,
    }
    if not session_string:
        result["error"] = "Нет session"
        return result

    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
        if not await client.is_user_authorized():
            result["error"] = "Сессия не авторизована"
            return result

        # 1) Профиль
        try:
            me = await asyncio.wait_for(client.get_me(), timeout=10)
            if me:
                result["first_name"] = me.first_name or ""
                result["last_name"] = me.last_name or ""
                result["username"] = f"@{me.username}" if me.username else ""
                result["user_id"] = me.id
        except Exception as e:
            logger.warning(f"[acc_info] {phone}: get_me: {e}")

        # 2) Активные сессии
        try:
            from telethon.tl.functions.account import GetAuthorizationsRequest
            auths = await asyncio.wait_for(
                client(GetAuthorizationsRequest()), timeout=15)
            result["sessions"] = len(auths.authorizations or [])
        except Exception as e:
            logger.warning(f"[acc_info] {phone}: get_authorizations: {e}")

        # 3) Диалоги
        try:
            dialogs = await asyncio.wait_for(
                client.get_dialogs(limit=None), timeout=30)
            chats = channels = groups = 0
            for d in dialogs:
                if getattr(d, "is_user", False):
                    chats += 1
                elif getattr(d, "is_channel", False):
                    channels += 1
                elif getattr(d, "is_group", False):
                    groups += 1
            result["chats"] = chats
            result["channels"] = channels
            result["groups"] = groups
            result["total_dialogs"] = len(dialogs)
        except Exception as e:
            logger.warning(f"[acc_info] {phone}: get_dialogs: {e}")

        result["ok"] = True
        return result
    except errors.AuthKeyUnregisteredError:
        await close_client(phone)
        result["error"] = "Ключ аннулирован"
    except errors.UserDeactivatedBanError:
        result["error"] = "Аккаунт забанен"
    except errors.UserDeactivatedError:
        result["error"] = "Аккаунт деактивирован"
    except errors.FrozenMethodInvalidError:
        result["error"] = "Аккаунт заморожен"
    except asyncio.TimeoutError:
        result["error"] = "Таймаут"
    except Exception as e:
        logger.error(f"[acc_info] {phone}: {type(e).__name__}: {e}")
        result["error"] = f"Ошибка: {str(e)[:120]}"
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return result


async def clear_all_dialogs(client: TelegramClient, phone: str):
    """Полная очистка: каналы, группы, личные чаты."""
    try:
        dialogs = await client.get_dialogs(limit=None)
    except Exception as e:
        logger.error(f"[clear_dialogs] {phone}: get_dialogs: "
                     f"{type(e).__name__}: {e}")
        return 0, 0, 0

    total = len(dialogs)
    if total == 0:
        return 0, 0, 0

    try:
        me = await client.get_me()
        my_id = me.id if me else "me"
    except Exception:
        my_id = "me"

    deleted = 0
    failed = 0

    for dialog in dialogs:
        try:
            entity = dialog.entity
            handled = False

            if getattr(dialog, "is_channel", False):
                try:
                    await asyncio.wait_for(
                        client(LeaveChannelRequest(channel=entity)),
                        timeout=15)
                    deleted += 1
                    handled = True
                except errors.FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 60))
                    try:
                        await asyncio.wait_for(
                            client(LeaveChannelRequest(channel=entity)),
                            timeout=15)
                        deleted += 1
                        handled = True
                    except Exception:
                        pass
                except Exception:
                    pass

            if not handled and getattr(dialog, "is_group", False):
                try:
                    await asyncio.wait_for(
                        client(DeleteChatUserRequest(
                            chat_id=entity.id, user_id=my_id)),
                        timeout=15)
                    deleted += 1
                    handled = True
                except errors.FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 60))
                    try:
                        await asyncio.wait_for(
                            client(DeleteChatUserRequest(
                                chat_id=entity.id, user_id=my_id)),
                            timeout=15)
                        deleted += 1
                        handled = True
                    except Exception:
                        pass
                except Exception:
                    pass

            if not handled and getattr(dialog, "is_user", False):
                try:
                    await asyncio.wait_for(
                        client(DeleteHistoryRequest(
                            peer=entity, max_id=0,
                            just_clear=False, revoke=True)),
                        timeout=15)
                    handled = True
                except Exception:
                    pass

            try:
                await asyncio.wait_for(client.delete_dialog(dialog), timeout=15)
                if not handled:
                    deleted += 1
            except Exception:
                if not handled:
                    failed += 1
        except Exception:
            failed += 1

        await asyncio.sleep(0.4)

    logger.info(f"[clear_dialogs] {phone}: удалено/вышло={deleted}, "
                f"ошибок={failed}, всего={total}")
    return deleted, failed, total


async def reset_other_sessions(session_string: str, phone: str):
    """ResetAuthorizations + полная очистка чатов."""
    if not session_string:
        return False, "Нет session"
    try:
        c = await _get_client(phone, session_string)
        if not await c.is_user_authorized():
            await close_client(phone)
            return False, "Сессия не авторизована"

        try:
            await asyncio.wait_for(c(ResetAuthorizationsRequest()), timeout=20)
        except errors.FrozenMethodInvalidError:
            logger.warning(f"[reset_sessions] {phone}: аккаунт ЗАМОРОЖЕН")
            return False, "FROZEN"

        try:
            deleted, failed, total = await clear_all_dialogs(c, phone)
            if deleted or failed:
                return True, (f"OK · сессии закрыты, "
                              f"чатов/каналов обработано: {deleted}, "
                              f"ошибок: {failed}")
            return True, "OK · сессии закрыты, чатов не было"
        except errors.FrozenMethodInvalidError:
            return False, "FROZEN"
        except Exception as e:
            logger.error(f"[reset_sessions] {phone}: clear: "
                         f"{type(e).__name__}: {e}")
            return True, "OK · сессии закрыты (чаты не удалось очистить)"
    except errors.FrozenMethodInvalidError:
        logger.warning(f"[reset_sessions] {phone}: аккаунт ЗАМОРОЖЕН")
        return False, "FROZEN"
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек."
    except errors.AuthKeyUnregisteredError:
        await close_client(phone)
        return False, "Ключ аннулирован"
    except errors.UserDeactivatedBanError:
        return False, "Аккаунт забанен"
    except errors.UserDeactivatedError:
        return False, "Аккаунт деактивирован"
    except asyncio.TimeoutError:
        return False, "Таймаут при завершении сессий"
    except Exception as e:
        logger.error(f"[reset_sessions] {phone}: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:150]}"


async def clear_dialogs_only(session_string: str, phone: str):
    """Только очистка чатов, без сброса сессий."""
    if not session_string:
        return False, "Нет session", 0, 0, 0
    try:
        c = await _get_client(phone, session_string)
        if not await c.is_user_authorized():
            await close_client(phone)
            return False, "Сессия не авторизована", 0, 0, 0
        deleted, failed, total = await clear_all_dialogs(c, phone)
        return True, "OK", deleted, failed, total
    except errors.FrozenMethodInvalidError:
        return False, "FROZEN", 0, 0, 0
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек.", 0, 0, 0
    except errors.AuthKeyUnregisteredError:
        await close_client(phone)
        return False, "Ключ аннулирован", 0, 0, 0
    except errors.UserDeactivatedBanError:
        return False, "Аккаунт забанен", 0, 0, 0
    except errors.UserDeactivatedError:
        return False, "Аккаунт деактивирован", 0, 0, 0
    except asyncio.TimeoutError:
        return False, "Таймаут", 0, 0, 0
    except Exception as e:
        logger.error(f"[clear_dialogs_only] {phone}: "
                     f"{type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:150]}", 0, 0, 0


async def start_login(phone: str):
    phone = fmt_phone(phone)
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
        sent = await asyncio.wait_for(client.send_code_request(phone), timeout=15)
        return client, sent.phone_code_hash, "OK"
    except errors.FloodWaitError as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        return None, None, f"FloodWait: {e.seconds} сек."
    except errors.PhoneNumberBannedError:
        try:
            await client.disconnect()
        except Exception:
            pass
        return None, None, "Номер забанен"
    except errors.PhoneNumberInvalidError:
        try:
            await client.disconnect()
        except Exception:
            pass
        return None, None, "Неверный номер"
    except errors.PhoneNumberFloodError:
        try:
            await client.disconnect()
        except Exception:
            pass
        return None, None, "Слишком много запросов на номер"
    except Exception as e:
        logger.error(f"[start_login] {phone}: {type(e).__name__}: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return None, None, f"Ошибка: {str(e)[:200]}"


async def confirm_code(client, phone, phone_code_hash, code):
    try:
        await asyncio.wait_for(
            client.sign_in(phone, code, phone_code_hash=phone_code_hash),
            timeout=15)
        return True, "OK", client.session.save()
    except errors.SessionPasswordNeededError:
        return False, "NEED_2FA", None
    except errors.PhoneCodeInvalidError:
        return False, "Неверный код", None
    except errors.PhoneCodeExpiredError:
        return False, "Код истёк", None
    except Exception as e:
        logger.error(f"[confirm_code] {phone}: {type(e).__name__}: {e}")
        return False, str(e)[:200], None


async def confirm_2fa(client, password):
    try:
        await asyncio.wait_for(client.sign_in(password=password), timeout=15)
        return True, "OK", client.session.save()
    except errors.PasswordHashInvalidError:
        return False, "Неверный пароль 2FA", None
    except Exception as e:
        logger.error(f"[confirm_2fa]: {type(e).__name__}: {e}")
        return False, str(e)[:200], None


async def set_2fa_password(client, new_password: str,
                           current_password: str = None):
    try:
        if current_password:
            await asyncio.wait_for(
                client.edit_2fa(current_password=current_password,
                                new_password=new_password, hint=""),
                timeout=30)
        else:
            await asyncio.wait_for(
                client.edit_2fa(new_password=new_password, hint=""),
                timeout=30)
        return True, "OK"
    except errors.PasswordHashInvalidError:
        return False, "Неверный текущий пароль 2FA"
    except Exception as e:
        logger.error(f"[set_2fa]: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:150]}"


async def set_session_ttl(client, days: int = 365):
    try:
        days = max(1, min(int(days), 366))
        await asyncio.wait_for(
            client(functions.account.SetAccountTTLRequest(
                ttl=AccountDaysTTL(days=days))),
            timeout=15)
        return True, "OK"
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек."
    except Exception as e:
        logger.error(f"[set_ttl]: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:150]}"


# ═══════════════════════════════════════════════════════════════════════════ #
#                                 ХЕЛПЕРЫ                                     #
# ═══════════════════════════════════════════════════════════════════════════ #

def fmt_phone(phone: str) -> str:
    if not phone:
        return phone
    s = str(phone).strip()
    s = re.sub(r"[\s\-\(\)\.]", "", s)
    s = re.sub(r"[^\d+]", "", s)
    if s.startswith("+"):
        s = "+" + s[1:].replace("+", "")
    else:
        s = "+" + s.replace("+", "")
    return s


def is_admin(uid: int) -> bool:
    return uid in ADMINS


def get_country_code(phone: str) -> str:
    if not phone:
        return ""
    p = fmt_phone(phone)
    for c in sorted(COUNTRIES, key=lambda x: len(x["phone_code"]), reverse=True):
        if p.startswith(c["phone_code"]):
            return c.get("code", "")
    return ""


def country_label(phone: str) -> str:
    if not phone:
        return "🌍 Неизвестно"
    p = fmt_phone(phone)
    for c in sorted(COUNTRIES, key=lambda x: len(x["phone_code"]), reverse=True):
        if p.startswith(c["phone_code"]):
            return f"{c['flag']} {c['name']} ({c['phone_code']})"
    return "🌍 Неизвестно"


def status_label(s: str) -> str:
    return {
        "cleaned": "🧹 Очищен от чужих сессий",
        "uncleaned": "⚠️ Ожидает очистки",
        "dead": "❌ Мёртвый аккаунт",
    }.get(s, s)


def format_remaining(seconds: float) -> str:
    if seconds <= 0:
        return "ожидается"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}ч {m}м"


def _fmt_dt(dt) -> str:
    if not dt:
        return "—"
    try:
        return dt.strftime("%d.%m.%Y в %H:%M")
    except Exception:
        return str(dt)


def _lzt_status_label(s: str) -> str:
    return {
        "bought": "🟡 Куплен (вход не выполнялся)",
        "logged_in": "🟢 Успешный вход в аккаунт",
        "failed": "🔴 Не удалось войти",
    }.get(s, s)


def _lzt_error_hint(status: str) -> str:
    hints = {
        "no_code_from_market": "LZT не отдал код за 2 минуты.\n💡 Подожди 5–10 минут и повтори.",
        "invalid_code": "Код пришёл, но Telegram его не принял.\n💡 Повтори вход.",
        "needs_2fa": "Нужен 2FA, но его нет в карточке лота.\n💡 Проверь данные лота.",
        "bad_2fa_password": "Пароль 2FA не подошёл.\n💡 Проверь telegram_password на LZT.",
        "send_unavailable": "Telegram не даёт запросить код.\n💡 Подожди 1–2 минуты.",
        "phone_banned": "Номер забанен Telegram — восстановить нельзя.",
        "phone_invalid": "Неверный формат номера. Обратись в LZT.",
        "phone_flood": "Слишком частые запросы. Подожди.",
        "timeout": "Таймаут соединения. Попробуй ещё раз.",
    }
    if status.startswith("flood_"):
        return "FloodWait — подожди и попробуй позже."
    if status.startswith("error:"):
        return f"Техническая ошибка: {status[6:]}"
    return hints.get(status, "Неизвестная причина. Смотри логи.")


async def edit_or_send(bot, call, text, markup=None, parse_mode=None):
    try:
        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text, parse_mode=parse_mode, reply_markup=markup)
    except Exception as e:
        logger.warning(f"[edit_or_send] edit failed: {type(e).__name__}: {e}")
        try:
            await bot.send_message(
                call.message.chat.id, text,
                parse_mode=parse_mode, reply_markup=markup)
        except Exception as e2:
            logger.error(f"[edit_or_send] send failed: {type(e2).__name__}: {e2}")


async def safe_edit(bot, chat_id, msg_id, text, markup=None, parse_mode=None):
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=text, parse_mode=parse_mode, reply_markup=markup)
    except Exception as e:
        if "not modified" in str(e).lower():
            return
        logger.warning(f"[safe_edit] {type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════ #
#                            CSV ЭКСПОРТ ХЕЛПЕРЫ                              #
# ═══════════════════════════════════════════════════════════════════════════ #

def _fmt_dt_csv(dt) -> str:
    if not dt:
        return ""
    try:
        return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        return str(dt)


def build_accounts_csv(accounts) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quotechar='"',
                        quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([
        "Название", "Номер", "Страна", "Код страны", "Статус",
        "2FA", "Цена (₽)", "Источник", "Лот LZT", "Избранное",
        "Добавлен", "Проверен", "Очищен", "Ошибок проверки",
    ])
    status_map = {"cleaned": "🧹 Очищен", "uncleaned": "⚠️ Ожидает очистки",
                  "dead": "❌ Мёртвый"}
    for acc in accounts:
        writer.writerow([
            acc.name or acc.phone,
            acc.phone,
            country_label(acc.phone) or "—",
            acc.country_code or "",
            status_map.get(acc.status, acc.status or ""),
            acc.two_fa or "",
            acc.price or 0,
            acc.bought_from or "manual",
            acc.lzt_item_id or "",
            "⭐" if (acc.favorite or 0) == 1 else "",
            _fmt_dt_csv(acc.registered_at),
            _fmt_dt_csv(acc.last_checked_at),
            _fmt_dt_csv(acc.last_cleaned_at),
            acc.health_fail_count or 0,
        ])
    return buf.getvalue().encode("utf-8-sig")


def build_single_account_csv(acc) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quotechar='"',
                        quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    status_map = {"cleaned": "🧹 Очищен", "uncleaned": "⚠️ Ожидает очистки",
                  "dead": "❌ Мёртвый"}
    writer.writerow(["Поле", "Значение"])
    writer.writerow(["ID", acc.id])
    writer.writerow(["Название", acc.name or acc.phone])
    writer.writerow(["Номер", acc.phone])
    writer.writerow(["Страна", country_label(acc.phone) or "—"])
    writer.writerow(["Код страны", acc.country_code or ""])
    writer.writerow(["Статус", status_map.get(acc.status, acc.status or "")])
    writer.writerow(["2FA", acc.two_fa or ""])
    writer.writerow(["Цена (₽)", acc.price or 0])
    writer.writerow(["Источник", acc.bought_from or "manual"])
    writer.writerow(["Лот LZT", acc.lzt_item_id or ""])
    writer.writerow(["Избранное", "⭐" if (acc.favorite or 0) == 1 else ""])
    writer.writerow(["Добавлен", _fmt_dt_csv(acc.registered_at)])
    writer.writerow(["Проверен", _fmt_dt_csv(acc.last_checked_at)])
    writer.writerow(["Очищен", _fmt_dt_csv(acc.last_cleaned_at)])
    writer.writerow(["Ошибок проверки", acc.health_fail_count or 0])
    writer.writerow([])
    writer.writerow(["Session (StringSession)", acc.session or ""])
    return buf.getvalue().encode("utf-8-sig")


def build_logs_csv(actions) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quotechar='"',
                        quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(["Дата", "Пользователь", "Действие",
                     "Аккаунт (ID)", "Детали"])
    for a in actions:
        writer.writerow([
            _fmt_dt_csv(a.created_at),
            a.user_id or 0,
            a.action or "",
            a.account_id or "",
            a.details or "",
        ])
    return buf.getvalue().encode("utf-8-sig")


def build_account_logs_csv(acc, actions) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quotechar='"',
                        quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(["Аккаунт", acc.name or acc.phone])
    writer.writerow(["Номер", acc.phone])
    writer.writerow(["Страна", country_label(acc.phone) or "—"])
    writer.writerow(["Всего записей", len(actions)])
    writer.writerow([])
    writer.writerow(["Дата", "Пользователь", "Действие", "Детали"])
    for a in actions:
        writer.writerow([
            _fmt_dt_csv(a.created_at),
            a.user_id or 0,
            a.action or "",
            a.details or "",
        ])
    return buf.getvalue().encode("utf-8-sig")


def build_lzt_logs_csv(items) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quotechar='"',
                        quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(["Лот", "Номер", "Страна", "Цена (₽)", "Статус",
                     "Ошибка", "Куплен"])
    status_map = {"bought": "🟡 Куплен (без входа)",
                  "logged_in": "🟢 Вошёл",
                  "failed": "🔴 Провалился"}
    for it in items:
        writer.writerow([
            it.item_id,
            it.phone or "",
            it.country or "",
            it.price or 0,
            status_map.get(it.status, it.status or ""),
            it.error or "",
            _fmt_dt_csv(it.bought_at),
        ])
    return buf.getvalue().encode("utf-8-sig")


# ═══════════════════════════════════════════════════════════════════════════ #
#                            РЕГИСТРАЦИЯ ХЕНДЛЕРОВ                            #
# ═══════════════════════════════════════════════════════════════════════════ #

def register_handlers(bot):
    user_state: dict = {}
    pending_signins: dict = {}
    pending_names: dict = {}
    running_tasks: dict = {}
    cancel_flags: dict = {}
    buy_ctx: dict = {}
    buy_cancel: dict = {}

    # ---------- ПРОГРЕВ СЕССИЙ ---------- #

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
                    ]))
            except Exception as e:
                logger.warning(f"[warmup] send: {e}")
            return

        total = len(accounts)
        try:
            msg = await bot.send_message(
                ADMIN_ID,
                "🔥 ПРОГРЕВ СЕССИЙ ЗАПУЩЕН\n\n"
                f"📱 К обработке: {total} аккаунтов\n\n⏳ Начинаю...")
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
                c = TelegramClient(StringSession(acc.session), API_ID, API_HASH)
                await asyncio.wait_for(c.connect(), timeout=15)
                if not await c.is_user_authorized():
                    fail += 1; icon, txt = "❌", "не авторизована"
                    await c.disconnect()
                    db.update_last_checked(acc.id, alive=False)
                else:
                    await asyncio.wait_for(c.get_me(), timeout=10)
                    await c.disconnect()
                    ok += 1; icon, txt = "✅", "активен"
                    db.update_last_checked(acc.id, alive=True)
            except asyncio.TimeoutError:
                fail += 1; icon, txt = "⏱", "таймаут"
            except errors.AuthKeyUnregisteredError:
                fail += 1; icon, txt = "🔑", "ключ аннулирован"
                db.update_last_checked(acc.id, alive=False)
            except errors.UserDeactivatedBanError:
                fail += 1; icon, txt = "🚫", "забанен"
                db.update_last_checked(acc.id, alive=False)
            except errors.UserDeactivatedError:
                fail += 1; icon, txt = "💀", "деактивирован"
                db.update_last_checked(acc.id, alive=False)
            except errors.FloodWaitError as e:
                fail += 1; icon, txt = "⏳", f"FloodWait {e.seconds}с"
                await asyncio.sleep(min(e.seconds, 60))
            except Exception as e:
                fail += 1; icon, txt = "⚠️", f"{type(e).__name__}"
                logger.error(f"[warmup] {acc.phone}: {type(e).__name__}: {e}")

            detail.append(f"{icon} {name} ({acc.phone}) — {txt}")
            if msg_id:
                tail = "\n".join(detail[-5:])
                await safe_edit(bot, chat_id, msg_id,
                    "🔥 ПРОГРЕВ СЕССИЙ\n\n"
                    f"📊 Прогресс: {i}/{total}\n\n"
                    f"✅ Успешно: {ok}\n❌ Ошибок: {fail}\n\n"
                    f"📞 Текущий: {acc.phone}\n\n"
                    f"Последние:\n{tail}")
            await asyncio.sleep(3)

        lines = "\n".join(detail)
        if len(lines) > 3500:
            lines = lines[:3500] + "\n… (обрезано)"
        final = (
            "🔥 ПРОГРЕВ СЕССИЙ ЗАВЕРШЁН\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Успешно: {ok}\n❌ Ошибок: {fail}\n📱 Всего: {total}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Детали:\n{lines}")
        markup = kbd.kb([
            [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
            [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")]])
        if msg_id:
            await safe_edit(bot, chat_id, msg_id, final, markup)
        else:
            try:
                await bot.send_message(ADMIN_ID, final, reply_markup=markup)
            except Exception as e:
                logger.warning(f"[warmup] send final: {e}")

    # ---------- АВТООЧИСТКА ---------- #

    async def _auto_clean_after(account_id: int, delay: float):
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
                        f"ℹ️ {msg}")
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
                        "Аккаунт помечен как ❌ Мёртвый.")
                except Exception:
                    pass
            else:
                logger.warning(f"[auto_clean] {acc.phone}: {msg}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[auto_clean] {account_id}: {type(e).__name__}: {e}")
        finally:
            auto_clean_tasks.pop(account_id, None)

    def schedule_auto_clean(account_id: int, delay: float = AUTO_CLEAN_DELAY):
        if account_id in auto_clean_tasks:
            return
        task = asyncio.create_task(_auto_clean_after(account_id, delay))
        auto_clean_tasks[account_id] = task

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
        loop.create_task(_warmup_sessions())
    except Exception as e:
        logger.warning(f"[startup] schedule: {e}")

    # ---------- ГЛАВНОЕ МЕНЮ ---------- #

    async def _send_main_menu(chat_id, edit_id=None):
        s = db.get_accounts_stats()
        fav_line = (f"  ⭐ Избранных:         {s['favorites']}\n"
                    if s.get("favorites") else "")
        archive_line = (f"  📦 В архиве:          {s['archived']}\n"
                        if s.get("archived") else "")
        text = (
            "🏛 ХРАНИЛИЩЕ TELEGRAM-АККАУНТОВ\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 Общая статистика:\n\n"
            f"  📱 Всего аккаунтов:   {s['total']}\n"
            f"  🧹 Очищено:           {s['cleaned']}\n"
            f"  ⚠️ Ожидают очистки:   {s['uncleaned']}\n"
            f"  ❌ Мёртвых:           {s['dead']}\n"
            f"{fav_line}{archive_line}"
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
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
            f"📆 TTL сессий: {SESSION_TTL_DAYS} дней\n\n"
            "Выберите действие:")
        if edit_id:
            await safe_edit(bot, chat_id, edit_id, text, kbd.main_menu())
        else:
            await bot.send_message(chat_id, text, reply_markup=kbd.main_menu())

    @bot.message_handler(commands=["start"])
    async def cmd_start(message: types.Message):
        if not is_admin(message.from_user.id):
            await bot.send_message(message.chat.id, "❌ Нет доступа.")
            return
        await _send_main_menu(message.chat.id)

    @bot.callback_query_handler(func=lambda c: c.data == "main_menu")
    async def cb_main_menu(call):
        await bot.answer_callback_query(call.id)
        user_state.pop(call.from_user.id, None)
        pending_names.pop(call.from_user.id, None)
        buy_ctx.pop(call.from_user.id, None)
        await _send_main_menu(call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == "noop")
    async def cb_noop(call):
        await bot.answer_callback_query(call.id)

    # ---------- ХРАНИЛИЩЕ / ИЗБРАННОЕ ---------- #

    async def _render_storage(chat_id, page, edit_id=None, call=None,
                              favorites_only=False):
        accounts = db.get_favorites() if favorites_only else db.get_accounts()
        if not accounts:
            if favorites_only:
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⭐ ИЗБРАННОЕ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "😔 Пока нет избранных аккаунтов.")
                markup = kbd.kb([
                    [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
                    [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")]])
            else:
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "📦 ХРАНИЛИЩЕ АККАУНТОВ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "😔 Хранилище пусто")
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
            "Выберите аккаунт:")
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "storage")
    async def cb_storage(call):
        await bot.answer_callback_query(call.id)
        await _render_storage(call.message.chat.id, 0, call=call)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("storage_page_"))
    async def cb_storage_page(call):
        await bot.answer_callback_query(call.id)
        page = int(call.data.replace("storage_page_", ""))
        await _render_storage(call.message.chat.id, page, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "favorites")
    async def cb_favorites(call):
        await bot.answer_callback_query(call.id)
        await _render_storage(call.message.chat.id, 0, call=call,
                              favorites_only=True)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("favorites_page_"))
    async def cb_favorites_page(call):
        await bot.answer_callback_query(call.id)
        page = int(call.data.replace("favorites_page_", ""))
        await _render_storage(call.message.chat.id, page, call=call,
                              favorites_only=True)

    # ---------- КАРТОЧКА АККАУНТА ---------- #

    async def _render_account(call, acc_id, page):
        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден",
                               kbd.back_main())
            return

        two_fa = f"«{acc.two_fa}»" if acc.two_fa else "не задан"
        date_str = (acc.registered_at.strftime("%d.%m.%Y в %H:%M")
                    if acc.registered_at else "—")

        auto_line = ""
        if acc.status == "uncleaned" and acc.registered_at:
            elapsed = (datetime.now() - acc.registered_at).total_seconds()
            remaining = AUTO_CLEAN_DELAY - elapsed
            auto_line = f"\n⏰ Автоочистка: через {format_remaining(remaining)}"

        source_line = ""
        if acc.bought_from and acc.bought_from != "manual":
            price_str = f" за {acc.price} ₽" if acc.price else ""
            item_str = f" (лот #{acc.lzt_item_id})" if acc.lzt_item_id else ""
            source_line = f"\n🛒 Источник: {acc.bought_from}{price_str}{item_str}"
        elif acc.price:
            source_line = f"\n💰 Цена: {acc.price} ₽"

        checked_line = ""
        if acc.last_checked_at:
            checked_line = (f"\n🕓 Проверен: "
                            f"{acc.last_checked_at.strftime('%d.%m %H:%M')}")
        cleaned_line = ""
        if acc.last_cleaned_at:
            cleaned_line = (f"\n🧹 Очищен: "
                            f"{acc.last_cleaned_at.strftime('%d.%m %H:%M')}")

        name_display = acc.name or acc.phone
        star_mark = " ⭐" if (acc.favorite or 0) == 1 else ""

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

        # Подгрузка деталей из Telegram
        info = await get_account_info(acc.session, acc.phone)
        if not info["ok"]:
            text = text.replace("⏳ Загружаю профиль Telegram...\n\n", "")
            text += f"\n\n⚠️ Профиль: {info['error']}"
        else:
            first = info["first_name"] or "—"
            last = info["last_name"] or ""
            full_name = f"{first} {last}".strip() or "—"
            uname = info["username"] or "нет"

            text = text.replace("⏳ Загружаю профиль Telegram...\n\n", "")
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
    async def cb_acc_view(call):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            await _render_account(call, int(acc_id), int(page))
        except Exception as e:
            logger.error(f"[cb_acc_view] {type(e).__name__}: {e}")

    # ---------- ИЗБРАННОЕ (toggle) ---------- #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_fav_"))
    async def cb_acc_fav(call):
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except Exception:
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
            show_alert=False)
        await _render_account(call, acc_id, page)

    # ---------- ЭКСПОРТ ОДНОГО АККАУНТА ---------- #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_export_"))
    async def cb_acc_export(call):
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id = int(acc_id)
        except Exception as e:
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
            "💡 Все поля + session внутри.")
        try:
            await bot.send_document(
                call.message.chat.id,
                document=types.InputFile(io.BytesIO(data), file_name=fname),
                caption=caption)
            db.log_action(call.from_user.id, "export_csv_one",
                          details=acc.phone, account_id=acc.id)
        except Exception as e:
            logger.error(f"[acc_export] send: {type(e).__name__}: {e}")
            await edit_or_send(bot, call, f"❌ Не отправлено: {type(e).__name__}",
                               kbd.back_main())

    # ---------- ДАТЬ НАЗВАНИЕ ---------- #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_rename_"))
    async def cb_acc_rename(call):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except Exception:
            return
        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return
        user_state[call.from_user.id] = {
            "state": "rename_account", "acc_id": acc_id, "page": page}
        current = acc.name or acc.phone
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏷 ДАТЬ НАЗВАНИЕ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📞 Номер: {acc.phone}\n"
            f"🏷 Текущее: {current}\n\n"
            "💡 Отправьте новое название одним сообщением.\n"
            "Максимум 64 символа.\n\n"
            "Сбросить на номер — отправьте «-».")
        await edit_or_send(bot, call, text, kbd.cancel())

    async def state_rename_account(message, state):
        uid = message.from_user.id
        new_name = (message.text or "").strip()
        acc_id = state.get("acc_id")
        page = state.get("page", 0)
        if not acc_id:
            user_state.pop(uid, None)
            await bot.send_message(message.chat.id, "❌ Сессия потеряна")
            return
        acc = db.get_account(acc_id)
        if not acc:
            user_state.pop(uid, None)
            await bot.send_message(message.chat.id, "❌ Аккаунт не найден")
            return
        if len(new_name) > 64:
            await bot.send_message(message.chat.id, "❌ Максимум 64 символа")
            return
        if new_name == "-" or not new_name:
            new_name = acc.phone
        db.update_account_name(acc_id, new_name)
        db.log_action(uid, "rename", details=new_name, account_id=acc_id)
        user_state.pop(uid, None)
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
                [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")]]))

    # ---------- ПОЛУЧИТЬ КОД ---------- #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_code_"))
    async def cb_acc_code(call):
        await bot.answer_callback_query(call.id, "⏳ Читаю...")
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
            "⏳ ПОДКЛЮЧАЮСЬ К АККАУНТУ...\n\n"
            f"🏷 {acc.name or acc.phone}\n"
            f"📞 {acc.phone}\n\n"
            "🔎 Читаю переписку с сервисным Telegram...")
        code, msg = await read_login_code(acc.session, acc.phone)
        if code:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🔑 КОД ВХОДА ПОЛУЧЕН\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 Номер: {acc.phone}\n"
                f"🔑 Код:   {code}\n")
            if acc.two_fa:
                text += f"🔐 2FA:   {acc.two_fa}\n"
            text += "\n💡 Введите код в Telegram."
        else:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ КОД НЕ НАЙДЕН\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                f"Ошибки: {msg}")
        markup = kbd.kb([
            [kbd.btn("🔄 ПОВТОРИТЬ", f"acc_code_{acc_id}_{page}")],
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{page}")]])
        await edit_or_send(bot, call, text, markup)

    # ---------- ОЧИСТИТЬ СЕССИИ + ЧАТЫ ---------- #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_clean_"))
    async def cb_acc_clean(call):
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
            "⏳ Это может занять до минуты...")
        task = auto_clean_tasks.pop(acc_id, None)
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
                f"ℹ️ {msg}")
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
                "📌 Статус изменён на ❌ Мёртвый.")
        else:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "❌ НЕ УДАЛОСЬ ОЧИСТИТЬ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                f"Ошибки: {msg}")
        markup = kbd.kb([
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{page}")],
            [kbd.btn("🏠 МЕНЮ", "main_menu")]])
        await edit_or_send(bot, call, text, markup)

    # ---------- ОЧИСТИТЬ ЧАТЫ ---------- #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_clear_chats_"))
    async def cb_acc_clear_chats(call):
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
            "⏳ Это может занять до минуты...")
        ok, msg, deleted, failed, total = await clear_dialogs_only(
            acc.session, acc.phone)
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
                    "🗑️ У аккаунта не было диалогов.")
            else:
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ ЧАТЫ ОЧИЩЕНЫ\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📞 {acc.phone}\n\n"
                    f"📤 Обработано:   {deleted}\n"
                    f"⚠️ Ошибок:       {failed}\n"
                    f"📊 Всего было:   {total}\n\n"
                    "ℹ️ Сессии аккаунта НЕ тронуты.")
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
                "📌 Статус изменён на ❌ Мёртвый.")
        else:
            text = (
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "❌ НЕ УДАЛОСЬ ОЧИСТИТЬ ЧАТЫ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 {acc.phone}\n\n"
                f"Ошибки: {msg}")
        markup = kbd.kb([
            [kbd.btn("🔄 ПОВТОРИТЬ", f"acc_clear_chats_{acc_id}_{page}")],
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc_id}_{page}")],
            [kbd.btn("🏠 МЕНЮ", "main_menu")]])
        await edit_or_send(bot, call, text, markup)

    # ---------- СМЕНА СТАТУСА ---------- #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_status_set_"))
    async def cb_acc_status_set(call):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        try:
            _, _, _, acc_id, new_status, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except Exception:
            return
        acc = db.get_account(acc_id)
        if not acc:
            await edit_or_send(bot, call, "❌ Аккаунт не найден")
            return
        db.update_account_status(acc_id, new_status)
        db.log_action(uid, "status", details=new_status, account_id=acc_id)
        if new_status == "cleaned":
            task = auto_clean_tasks.pop(acc_id, None)
            if task and not task.done():
                task.cancel()
        await call.answer(f"✅ Статус: {status_label(new_status)}",
                          show_alert=False)
        await _render_account(call, acc_id, page)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_status_"))
    async def cb_acc_status(call):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except Exception:
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
            "💡 Выберите новый статус:")
        await edit_or_send(bot, call, text, kbd.status_menu(acc_id, page))

    # ---------- УДАЛИТЬ ОДИН ---------- #

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_del_yes_"))
    async def cb_acc_del_yes(call):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        try:
            _, _, _, acc_id, page = call.data.split("_")
            acc_id = int(acc_id)
        except Exception:
            return
        acc = db.get_account(acc_id)
        if acc:
            task = auto_clean_tasks.pop(acc_id, None)
            if task and not task.done():
                task.cancel()
            await close_client(acc.phone)
            db.delete_account(acc_id)
            db.log_action(uid, "delete", details=acc.phone, account_id=acc_id)
        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ АККАУНТ УДАЛЁН\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🗑️ Аккаунт убран из хранилища.",
            kbd.kb([[kbd.btn("🔙 В ХРАНИЛИЩЕ", "storage")]]))

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_del_"))
    async def cb_acc_del(call):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except Exception:
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
            "❗ Это действие нельзя отменить.")
        await edit_or_send(bot, call, text, kbd.confirm_delete(acc_id, page))

    # ---------- ПРОВЕРКА ВСЕХ ---------- #

    async def _check_all(chat_id, msg_id, accounts, uid):
        total = len(accounts); ok = dead = 0
        for i, acc in enumerate(accounts, 1):
            await safe_edit(
                bot, chat_id, msg_id,
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🔍 ПРОВЕРКА АККАУНТОВ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 Прогресс: {i}/{total}\n\n"
                f"✅ Живых:   {ok}\n❌ Мёртвых: {dead}\n\n"
                f"📞 Проверяю: {acc.phone}",
                kbd.back_main())
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
            f"📱 Всего:   {total}")
        db.log_action(uid, "check", details=f"ok={ok} dead={dead}")
        await safe_edit(bot, chat_id, msg_id, text, kbd.after_check(dead > 0))

    @bot.callback_query_handler(func=lambda c: c.data == "check_accounts")
    async def cb_check(call):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        accounts = db.get_accounts()
        if not accounts:
            await edit_or_send(bot, call, "📦 Хранилище пусто.",
                               kbd.back_main())
            return
        if uid in running_tasks and not running_tasks[uid].done():
            await edit_or_send(bot, call, "⏳ Уже идёт...", kbd.back_main())
            return
        running_tasks[uid] = asyncio.create_task(
            _check_all(call.message.chat.id, call.message.message_id,
                       accounts, uid))

    # ---------- УДАЛИТЬ МЁРТВЫЕ ---------- #

    @bot.callback_query_handler(func=lambda c: c.data == "del_dead")
    async def cb_del_dead(call):
        await bot.answer_callback_query(call.id)
        dead_accs = [a for a in db.get_accounts() if a.status == "dead"]
        if not dead_accs:
            await edit_or_send(
                bot, call,
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "✅ МЁРТВЫХ НЕТ\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "🗑️ В хранилище нет мёртвых аккаунтов.",
                kbd.back_main())
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
            + "\n\n❗ Нельзя отменить.")
        await edit_or_send(bot, call, text, kbd.confirm_del_dead(len(dead_accs)))

    @bot.callback_query_handler(func=lambda c: c.data == "del_dead_confirm")
    async def cb_del_dead_confirm(call):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        cnt = 0; errors = []
        for acc in db.get_accounts():
            if acc.status == "dead":
                try:
                    task = auto_clean_tasks.pop(acc.id, None)
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
            f"✅ Удалено: {cnt}")
        if errors:
            text += "\n\nОшибки:\n" + "\n".join(f"• {e}" for e in errors[:10])
        await edit_or_send(bot, call, text, kbd.back_main())

    # ---------- МАССОВАЯ ОЧИСТКА ---------- #

    async def _clean_all(chat_id, msg_id, accounts, uid):
        cancel_flags[uid] = False
        total = len(accounts); ok = fail = 0
        cancelled = False; error_lines = []
        for i, acc in enumerate(accounts, 1):
            if cancel_flags.get(uid):
                cancelled = True
                break
            errors_block = ""
            if error_lines:
                errors_block = "\n\nОшибки:\n" + "\n".join(
                    f"• {e}" for e in error_lines[-5:])
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
                kbd.cleaning_progress())
            task = auto_clean_tasks.pop(acc.id, None)
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
        cancel_flags.pop(uid, None)
        header = "⏹ ОЧИСТКА ОТМЕНЕНА" if cancelled else "📊 ОЧИСТКА ЗАВЕРШЕНА"
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{header}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Очищено: {ok}\n❌ Ошибок:  {fail}\n📱 Всего: {total}\n\n"
            "🔒 Сессии закрыты, выход из групп/каналов выполнен.")
        if error_lines:
            text += "\n\nОшибки:\n" + "\n".join(f"• {e}" for e in error_lines[:15])
            if len(error_lines) > 15:
                text += f"\n… и ещё {len(error_lines) - 15}"
        else:
            text += "\nОшибки: —"
        db.log_action(uid, "clean_all", details=f"ok={ok} fail={fail}")
        await safe_edit(bot, chat_id, msg_id, text, kbd.back_main())

    @bot.callback_query_handler(func=lambda c: c.data == "clean_sessions")
    async def cb_clean(call):
        await bot.answer_callback_query(call.id)
        accounts = [a for a in db.get_accounts() if a.status == "uncleaned"]
        if not accounts:
            await edit_or_send(
                bot, call,
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "✅ ВСЁ УЖЕ ОЧИЩЕНО\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "🧹 Нечего очищать.",
                kbd.back_main())
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
            "❗ Продолжить?")
        await edit_or_send(bot, call, text, kbd.confirm_clean())

    @bot.callback_query_handler(func=lambda c: c.data == "clean_confirm")
    async def cb_clean_confirm(call):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        accounts = [a for a in db.get_accounts() if a.status == "uncleaned"]
        if not accounts:
            await edit_or_send(bot, call, "✅ Нечего", kbd.back_main())
            return
        if uid in running_tasks and not running_tasks[uid].done():
            await edit_or_send(bot, call, "⏳ Уже идёт...", kbd.back_main())
            return
        running_tasks[uid] = asyncio.create_task(
            _clean_all(call.message.chat.id, call.message.message_id,
                       accounts, uid))

    @bot.callback_query_handler(func=lambda c: c.data == "clean_cancel")
    async def cb_clean_cancel(call):
        cancel_flags[call.from_user.id] = True
        await bot.answer_callback_query(call.id, "⏹ Отмена...", show_alert=False)

    # ---------- ПОКУПКА LZT ---------- #

    def _lzt_err() -> str:
        if not lzt.LZT_AVAILABLE:
            return "LOLZTEAM не установлен: pip install LOLZTEAM"
        if not lzt.LZT_API_TOKEN:
            return "LZT_API_TOKEN не задан в config.py"
        return ""

    async def _render_buy_menu(chat_id, edit_id=None, call=None):
        err = _lzt_err()
        if err:
            text = ("🛒 ПОКУПКА НОМЕРОВ\n\n"
                    f"❌ {err}\n\n"
                    "Отредактируйте config.py и перезапустите бота.")
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
            "  • 📥 Импорт — войти в уже купленные лоты")
        markup = kbd.buy_menu()
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "buy_menu")
    async def cb_buy_menu(call):
        await bot.answer_callback_query(call.id)
        buy_ctx.pop(call.from_user.id, None)
        await _render_buy_menu(call.message.chat.id, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "buy_single_random")
    async def cb_buy_single_random(call):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        buy_ctx[uid] = {"country": None, "count": 1}
        user_state[uid] = {"state": "buy_price"}
        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🎲 ПОКУПКА — СЛУЧАЙНАЯ СТРАНА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💰 Введите максимальную цену в ₽\n"
            "(например: 50)",
            kbd.buy_back())

    @bot.callback_query_handler(func=lambda c: c.data == "buy_single_country")
    async def cb_buy_single_country(call):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        buy_ctx[uid] = {"country": "", "count": 1}
        user_state[uid] = {"state": "buy_country"}
        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🌍 ПОКУПКА — КОНКРЕТНАЯ СТРАНА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🌐 Введите название страны (можно часть):\n"
            "Индонезия, Indonesia, США…",
            kbd.buy_back())

    @bot.callback_query_handler(func=lambda c: c.data == "buy_bulk")
    async def cb_buy_bulk(call):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        buy_ctx[uid] = {"country": None, "count": 0}
        user_state[uid] = {"state": "buy_bulk_count"}
        await edit_or_send(
            bot, call,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📦 ОПТОВАЯ ЗАКУПКА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔢 Сколько номеров купить? (1–100)",
            kbd.buy_back())

    @bot.callback_query_handler(func=lambda c: c.data == "buy_import")
    async def cb_buy_import(call):
        await bot.answer_callback_query(call.id)
        uid = call.from_user.id
        err = _lzt_err()
        if err:
            await edit_or_send(bot, call, f"❌ {err}", kbd.kb([
                [kbd.btn("🔙 НАЗАД", "buy_menu")]]))
            return
        if uid in running_tasks and not running_tasks[uid].done():
            await edit_or_send(bot, call, "⏳ Уже идёт...", kbd.back_main())
            return
        task = asyncio.create_task(
            _import_purchases(call.message.chat.id,
                              call.message.message_id, uid))
        running_tasks[uid] = task

    @bot.callback_query_handler(func=lambda c: c.data == "buy_stop")
    async def cb_buy_stop(call):
        await bot.answer_callback_query(call.id, "⏹ Останавливаю...")
        buy_cancel[call.from_user.id] = True

    async def state_buy_country(message, state):
        uid = message.from_user.id
        country = (message.text or "").strip()
        if len(country) < 2:
            await bot.send_message(message.chat.id, "❌ Слишком короткое")
            return
        ctx = buy_ctx.setdefault(uid, {})
        ctx["country"] = country
        user_state[uid] = {"state": "buy_price"}
        await bot.send_message(
            message.chat.id,
            f"🌍 Страна: {country}\n\n💰 Введите максимальную цену в ₽:",
            reply_markup=kbd.buy_back())

    async def state_buy_price(message, state):
        uid = message.from_user.id
        try:
            max_price = float((message.text or "").strip().replace(",", "."))
            if max_price <= 0:
                raise ValueError
        except ValueError:
            await bot.send_message(message.chat.id, "❌ Введите число")
            return
        ctx = buy_ctx.get(uid, {})
        country = ctx.get("country")
        user_state.pop(uid, None)
        await _run_single_buy(message.chat.id, uid, max_price, country)

    async def state_buy_bulk_count(message, state):
        uid = message.from_user.id
        raw = (message.text or "").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= 100):
            await bot.send_message(message.chat.id, "❌ Число 1–100")
            return
        buy_ctx.setdefault(uid, {})["count"] = int(raw)
        user_state[uid] = {"state": "buy_bulk_price"}
        await bot.send_message(
            message.chat.id,
            f"📦 Номеров: {raw}\n\n💰 Введите максимальную цену в ₽ за штуку:",
            reply_markup=kbd.buy_back())

    async def state_buy_bulk_price(message, state):
        uid = message.from_user.id
        try:
            max_price = float((message.text or "").strip().replace(",", "."))
            if max_price <= 0:
                raise ValueError
        except ValueError:
            await bot.send_message(message.chat.id, "❌ Введите число")
            return
        count = buy_ctx.get(uid, {}).get("count", 1)
        user_state.pop(uid, None)
        await _run_bulk_buy(message.chat.id, uid, count, max_price)

    async def _save_bought(phone, session_str, two_fa, price,
                           item_country, item_id):
        phone = fmt_phone(phone)
        try:
            db.add_lzt_purchase(
                item_id=item_id, phone=phone, price=int(price or 0),
                country=item_country or "", status="bought")
        except Exception as e:
            logger.warning(f"[buy.db] add_lzt_purchase: {e}")
        try:
            db.add_account(
                phone, session_str, two_fa or "",
                name=phone,
                country_code=get_country_code(phone),
                price=int(price or 0),
                bought_from="lzt",
                lzt_item_id=item_id)
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
        try:
            schedule_auto_clean(acc.id)
        except Exception:
            pass
        try:
            db.update_lzt_purchase(item_id, "logged_in", phone=phone)
        except Exception:
            pass
        db.stat_added(1)
        db.stat_bought_lzt(1, spent_rub=int(price or 0))
        return acc

    async def _run_single_buy(chat_id, uid, max_price, country):
        buy_cancel[uid] = False
        msg = await bot.send_message(
            chat_id,
            "⏳ Покупка...\n\n"
            f"💰 до {max_price} ₽\n"
            f"🌍 {country or 'любая страна'}",
            reply_markup=kbd.buy_stop_kb())
        try:
            market = lzt.make_market()
            balance, balance_id = await lzt.get_balance(market)
            if balance is not None and balance < 1:
                await safe_edit(bot, chat_id, msg.message_id,
                    f"❌ Нулевой баланс: {balance:.2f} ₽", kbd.back_main())
                return
            skip_ids = set(); attempts = 0
            while not buy_cancel.get(uid) and attempts < 20:
                attempts += 1
                ok, item_id, phone, price, item_country, login_data, err = \
                    await lzt.buy_one(market, max_price, balance_id, balance,
                                      skip_ids=skip_ids, country=country)
                if not ok:
                    if err == "no_money":
                        await safe_edit(bot, chat_id, msg.message_id,
                            "💸 Кончились деньги на LZT", kbd.back_main())
                        return
                    if err == "no_lots":
                        await safe_edit(bot, chat_id, msg.message_id,
                            "📭 Лоты не найдены", kbd.back_main())
                        return
                    await asyncio.sleep(lzt.BUY_DELAY)
                    continue
                await safe_edit(bot, chat_id, msg.message_id,
                    f"✅ Куплен лот {item_id} за {price} ₽\n"
                    f"📞 {phone or '?'}\n\n⏳ Вход в аккаунт...",
                    kbd.buy_stop_kb())
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
                    phone, item_id, market, password=password)
                if not session_str:
                    try:
                        db.add_lzt_purchase(
                            item_id=item_id, phone=phone,
                            price=int(price or 0), country=item_country or "",
                            status="failed", error=status)
                    except Exception:
                        pass
                    await bot.send_message(
                        chat_id,
                        f"⚠️ Лот {item_id} куплен, но вход: {status}\n\n"
                        f"💡 {_lzt_error_hint(status)}")
                    skip_ids.add(item_id)
                    continue
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
                    "📌 Статус:   ⚠️ Ожидает очистки")
                await safe_edit(bot, chat_id, msg.message_id, text,
                                kbd.buy_after())
                db.log_action(uid, "buy_single",
                              details=f"{phone} {price}₽ item={item_id}",
                              account_id=acc.id if acc else None)
                return
            await safe_edit(bot, chat_id, msg.message_id,
                "⏹ Остановлено", kbd.back_main())
        except Exception as e:
            logger.exception(f"[buy.single] {e}")
            await safe_edit(bot, chat_id, msg.message_id,
                f"❌ Ошибка: {type(e).__name__}", kbd.back_main())
        finally:
            buy_cancel.pop(uid, None)

    async def _run_bulk_buy(chat_id, uid, count, max_price):
        buy_cancel[uid] = False
        msg = await bot.send_message(
            chat_id,
            "⏳ Оптовая покупка...\n\n"
            f"📦 {count} шт. по {max_price} ₽",
            reply_markup=kbd.buy_stop_kb())
        ok_count = fail_count = attempts = 0
        max_attempts = count * 10
        skip_ids = set(); report = []
        try:
            market = lzt.make_market()
            balance, balance_id = await lzt.get_balance(market)
            if balance is not None and balance < max_price:
                await safe_edit(bot, chat_id, msg.message_id,
                    f"❌ Мало средств: {balance:.2f} ₽", kbd.back_main())
                return
            while (ok_count < count and attempts < max_attempts
                   and not buy_cancel.get(uid)):
                attempts += 1
                await safe_edit(bot, chat_id, msg.message_id,
                    "📦 ОПТОВАЯ ПОКУПКА\n\n"
                    f"✅ Куплено: {ok_count}/{count}\n"
                    f"❌ Ошибок:  {fail_count}\n"
                    f"🔁 Попытка: {attempts}/{max_attempts}",
                    kbd.buy_stop_kb())
                ok, item_id, phone, price, item_country, login_data, err = \
                    await lzt.buy_one(market, max_price, balance_id, balance,
                                      skip_ids=skip_ids)
                if not ok:
                    if err == "no_money":
                        report.append("💸 кончились деньги"); break
                    if err == "no_lots":
                        report.append("📭 лоты закончились"); break
                    fail_count += 1
                    await asyncio.sleep(lzt.BUY_DELAY)
                    continue
                password = None
                for key in ("telegram_password", "password", "twofa", "2fa",
                            "two_fa", "cloud_password"):
                    v = (login_data or {}).get(key)
                    if v:
                        password = str(v)
                        break
                if not phone:
                    skip_ids.add(item_id); fail_count += 1
                    continue
                phone = fmt_phone(phone)
                session_str, status = await lzt.login_to_account(
                    phone, item_id, market, password=password)
                if not session_str:
                    fail_count += 1
                    skip_ids.add(item_id)
                    report.append(f"⚠️ {phone} — {status}")
                    try:
                        db.add_lzt_purchase(
                            item_id=item_id, phone=phone,
                            price=int(price or 0), country=item_country or "",
                            status="failed", error=status)
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
            header = ("📊 ОПТОВАЯ ЗАКУПКА ЗАВЕРШЕНА"
                      if ok_count >= count else "⚠️ ЗАКУПКА ОСТАНОВЛЕНА")
            text = (
                f"{header}\n\n"
                f"✅ Куплено: {ok_count}/{count}\n"
                f"❌ Ошибок:  {fail_count}\n\n"
                "Детали:\n" + "\n".join(report[:15]))
            db.log_action(uid, "buy_bulk",
                          details=f"ok={ok_count} fail={fail_count}")
            await safe_edit(bot, chat_id, msg.message_id, text, kbd.back_main())
        except Exception as e:
            logger.exception(f"[buy.bulk] {e}")
            await safe_edit(bot, chat_id, msg.message_id,
                f"❌ Ошибка: {type(e).__name__}", kbd.back_main())
        finally:
            buy_cancel.pop(uid, None)

    async def _import_purchases(chat_id, msg_id, uid):
        try:
            market = lzt.make_market()
            items = await lzt.list_purchases(market)
            if not items:
                await safe_edit(bot, chat_id, msg_id,
                    "📭 LZT не вернул покупок.", kbd.back_main())
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
                await safe_edit(bot, chat_id, msg_id,
                    "✅ Новых лотов нет.", kbd.back_main())
                return
            ok_count = fail_count = 0
            report = []
            for i, (item_id, it) in enumerate(todo, 1):
                if buy_cancel.get(uid):
                    break
                await safe_edit(bot, chat_id, msg_id,
                    f"📥 ИМПОРТ {i}/{len(todo)}\n\n"
                    f"✅ {ok_count} | ❌ {fail_count}",
                    kbd.buy_stop_kb())
                login_data = await lzt.get_item_login_data(market, item_id)
                phone = None
                for key in ("phone", "phone_number", "number", "telegram_phone"):
                    if login_data.get(key):
                        phone = fmt_phone(str(login_data[key]).strip())
                        break
                if not phone:
                    phone = lzt.extract_phone(str(it.get("title") or ""))
                if phone:
                    phone = fmt_phone(phone)
                if not phone:
                    fail_count += 1
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
                    phone, item_id, market, password=password)
                if not session_str:
                    fail_count += 1
                    report.append(f"⚠️ {phone} — {status}")
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
                + "\n".join(report[:15]))
            db.log_action(uid, "import",
                          details=f"ok={ok_count} fail={fail_count}")
            await safe_edit(bot, chat_id, msg_id, text, kbd.back_main())
        except Exception as e:
            logger.exception(f"[buy.import] {e}")
            await safe_edit(bot, chat_id, msg_id,
                f"❌ Ошибка: {type(e).__name__}", kbd.back_main())
        finally:
            buy_cancel.pop(uid, None)

    # ---------- ОТЧЁТЫ ---------- #

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
            "  • 📜 Экспорт логов — все действия")
        markup = kbd.reports_menu()
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "reports_menu")
    async def cb_reports_menu(call):
        await bot.answer_callback_query(call.id)
        await _render_reports_menu(call.message.chat.id, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "report_total")
    async def cb_report_total(call):
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
            f"  💸 Потрачено:        {totals['spent_rub']} ₽")
        await edit_or_send(bot, call, text,
                           kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))

    @bot.callback_query_handler(func=lambda c: c.data == "report_30d")
    async def cb_report_30d(call):
        await bot.answer_callback_query(call.id)
        rows = db.get_daily_stats(days=30)
        if not rows:
            await edit_or_send(
                bot, call,
                "📅 ОТЧЁТ ЗА 30 ДНЕЙ\n\n😔 Пока нет данных.",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))
            return
        lines = []
        for r in rows[:14]:
            parts = []
            if r.added: parts.append(f"+{r.added}📱")
            if r.cleaned: parts.append(f"{r.cleaned}🧹")
            if r.dead: parts.append(f"{r.dead}❌")
            if r.bought_lzt: parts.append(f"{r.bought_lzt}🛒")
            if r.spent_rub: parts.append(f"{r.spent_rub}₽")
            lines.append(f"{r.day} · " + " ".join(parts or ["—"]))
        text = "📅 ОТЧЁТ ЗА 30 ДНЕЙ\n\n" + "\n".join(lines)
        await edit_or_send(bot, call, text,
                           kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))

    async def _render_report_accounts(chat_id, page, edit_id=None, call=None):
        accounts = db.get_accounts()
        if not accounts:
            text = "📱 ОТЧЁТЫ ПО АККАУНТАМ\n\n😔 Хранилище пусто."
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
            "📱 ОТЧЁТЫ ПО АККАУНТАМ\n\n"
            f"📊 Всего: {total}  |  Страница {page + 1} / {total_pages}\n\n"
            "Выберите аккаунт:")
        markup = kbd.reports_accounts_page(slice_, page, total_pages, 6)
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "report_accounts")
    async def cb_report_accounts(call):
        await bot.answer_callback_query(call.id)
        await _render_report_accounts(call.message.chat.id, 0, call=call)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("report_accounts_page_"))
    async def cb_report_accounts_page(call):
        await bot.answer_callback_query(call.id)
        page = int(call.data.replace("report_accounts_page_", ""))
        await _render_report_accounts(call.message.chat.id, page, call=call)

    async def _render_account_report(call, acc_id, page=0):
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
        status_emoji = {"cleaned": "🧹", "uncleaned": "⚠️", "dead": "❌"}.get(
            acc.status, "•")
        source_line = acc.bought_from or "manual"
        if acc.bought_from == "lzt" and acc.lzt_item_id:
            source_line = f"LZT (лот #{acc.lzt_item_id})"
        actions_block = ""
        if actions:
            actions_block = "\n📜 Последние действия:\n" + "\n".join(
                f"  • {a.created_at.strftime('%d.%m %H:%M')} "
                f"`{a.action}`" + (f" · {a.details[:40]}" if a.details else "")
                for a in actions[:8])
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊 ОТЧЁТ ПО АККАУНТУ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏷 Название:   {name}{star}\n"
            f"📞 Номер:      {acc.phone}\n"
            f"🌍 Страна:     {country_label(acc.phone)}\n"
            f"📆 Добавлен:   {_fmt_dt(acc.registered_at)}\n"
            f"{status_emoji} Статус:  {status_label(acc.status)}\n\n"
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
            f"{actions_block}")
        await edit_or_send(bot, call, text, kbd.account_report_kb(acc, page))

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_report_"))
    async def cb_acc_report(call):
        await bot.answer_callback_query(call.id)
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except Exception as e:
            logger.error(f"[cb_acc_report] parse: {e}")
            return
        await _render_account_report(call, acc_id, page)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_history_export_"))
    async def cb_acc_history_export(call):
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")
        try:
            parts = call.data.split("_")
            acc_id = int(parts[3])
            page = int(parts[4])
        except Exception as e:
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
                    f"📊 Записей: {len(actions)}"))
            db.log_action(call.from_user.id, "export_history",
                          details=acc.phone, account_id=acc.id)
        except Exception as e:
            logger.error(f"[acc_history_export] send: {type(e).__name__}: {e}")
            await edit_or_send(bot, call,
                               f"❌ Не отправлено: {type(e).__name__}",
                               kbd.back_main())

    @bot.callback_query_handler(func=lambda c: c.data.startswith("acc_history_"))
    async def cb_acc_history(call):
        await bot.answer_callback_query(call.id, "📜")
        try:
            _, _, acc_id, page = call.data.split("_")
            acc_id, page = int(acc_id), int(page)
        except Exception as e:
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
                "📜 ИСТОРИЯ ДЕЙСТВИЙ\n\n"
                f"📞 {acc.phone}\n\n"
                "Пока нет записанных действий.")
        else:
            lines = []
            for a in actions:
                ts = a.created_at.strftime("%d.%m %H:%M")
                det = f" · {a.details[:60]}" if a.details else ""
                lines.append(f"{ts} · `{a.action}`{det}")
            text = (
                "📜 ИСТОРИЯ ДЕЙСТВИЙ\n\n"
                f"📞 {acc.phone}\n"
                f"📊 Всего записей: {len(actions)}\n\n"
                + "\n".join(lines))
        await edit_or_send(bot, call, text,
                           kbd.account_history_kb(acc_id, page))

    @bot.callback_query_handler(func=lambda c: c.data == "report_sources")
    async def cb_report_sources(call):
        await bot.answer_callback_query(call.id)
        accounts = db.get_accounts()
        manual = sum(1 for a in accounts if (a.bought_from or "manual") == "manual")
        lzt_cnt = sum(1 for a in accounts if a.bought_from == "lzt")
        imp = sum(1 for a in accounts if a.bought_from == "import")
        other = len(accounts) - manual - lzt_cnt - imp
        total_spent = sum((a.price or 0) for a in accounts if a.bought_from == "lzt")
        avg_lzt = (total_spent / lzt_cnt) if lzt_cnt else 0
        text = (
            "🏆 ТОП ИСТОЧНИКОВ\n\n"
            f"✍️ Вручную:      {manual}\n"
            f"🛒 LZT Market:   {lzt_cnt}\n"
            f"📥 Импорт LZT:   {imp}\n"
            + (f"❔ Прочее:       {other}\n" if other else "")
            + "\n💸 ФИНАНСЫ\n"
            f"  Потрачено на LZT: {total_spent} ₽\n"
            f"  Средняя цена:     {avg_lzt:.2f} ₽")
        await edit_or_send(bot, call, text,
                           kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))

    # ---------- ЭКСПОРТ ВСЕХ ---------- #

    @bot.callback_query_handler(func=lambda c: c.data == "report_export_all")
    async def cb_report_export_all(call):
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")
        accounts = db.get_accounts()
        if not accounts:
            await edit_or_send(bot, call, "📤 ЭКСПОРТ\n\n😔 Хранилище пусто.",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))
            return
        try:
            data = build_accounts_csv(accounts)
        except Exception as e:
            logger.error(f"[export_all] build: {type(e).__name__}: {e}")
            await edit_or_send(bot, call, f"❌ Ошибка: {type(e).__name__}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))
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
                    f"💾 {len(data) // 1024 + 1} КБ"))
            db.log_action(call.from_user.id, "export_csv_all",
                          details=f"count={len(accounts)}")
        except Exception as e:
            logger.error(f"[export_all] send: {type(e).__name__}: {e}")
            await edit_or_send(bot, call,
                               f"❌ Не отправлено: {type(e).__name__}",
                               kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))

    # ---------- ЭКСПОРТ ЛОГОВ ---------- #

    @bot.callback_query_handler(func=lambda c: c.data == "report_export_logs")
    async def cb_report_export_logs(call):
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")
        actions = db.get_recent_actions(limit=5000)
        if not actions:
            await edit_or_send(bot, call,
                "📜 ЭКСПОРТ ЛОГОВ\n\n😔 Логов нет.",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))
            return
        try:
            data = build_logs_csv(actions)
        except Exception as e:
            logger.error(f"[export_logs] build: {type(e).__name__}: {e}")
            await edit_or_send(bot, call, f"❌ Ошибка: {type(e).__name__}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))
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
                    f"📄 {fname}"))
            db.log_action(call.from_user.id, "export_logs",
                          details=f"count={len(actions)}")
        except Exception as e:
            logger.error(f"[export_logs] send: {type(e).__name__}: {e}")
            await edit_or_send(bot, call,
                               f"❌ Не отправлено: {type(e).__name__}",
                               kbd.kb([[kbd.btn("🔙 НАЗАД", "reports_menu")]]))

    # ---------- LZT-ЛОГИ ---------- #

    async def _render_lzt_logs_menu(chat_id, edit_id=None, call=None):
        s = db.get_lzt_stats()
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📥 LZT-ЛОГИ\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"  🎯 Всего:            {s['total']}\n"
            f"  🟢 Вошёл:            {s['logged_in']}\n"
            f"  🟡 Без входа:        {s['bought']}\n"
            f"  🔴 Провалился:       {s['failed']}\n"
            f"  💸 Потрачено:        {s['spent_rub']} ₽")
        markup = kbd.lzt_logs_menu()
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs")
    async def cb_lzt_logs(call):
        await bot.answer_callback_query(call.id)
        await _render_lzt_logs_menu(call.message.chat.id, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs_export")
    async def cb_lzt_logs_export(call):
        await bot.answer_callback_query(call.id, "📤 Готовлю файл...")
        items = db.get_lzt_purchases(limit=5000)
        if not items:
            await edit_or_send(bot, call,
                "📤 ЭКСПОРТ LZT-ЛОГОВ\n\n😔 Записей нет.",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "lzt_logs")]]))
            return
        try:
            data = build_lzt_logs_csv(items)
        except Exception as e:
            logger.error(f"[export_lzt] build: {type(e).__name__}: {e}")
            await edit_or_send(bot, call, f"❌ Ошибка: {type(e).__name__}",
                kbd.kb([[kbd.btn("🔙 НАЗАД", "lzt_logs")]]))
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
                    f"📄 {fname}"))
            db.log_action(call.from_user.id, "export_lzt_logs",
                          details=f"count={len(items)}")
        except Exception as e:
            logger.error(f"[export_lzt] send: {type(e).__name__}: {e}")
            await edit_or_send(bot, call,
                               f"❌ Не отправлено: {type(e).__name__}",
                               kbd.kb([[kbd.btn("🔙 НАЗАД", "lzt_logs")]]))

    async def _render_lzt_list(chat_id, filter_type, page,
                               edit_id=None, call=None):
        status_map = {"all": None, "logged": "logged_in",
                      "failed": "failed", "bought": "bought"}
        status = status_map.get(filter_type)
        items = db.get_lzt_purchases(status=status, limit=500)
        titles = {"all": "📥 ВСЕ ПОКУПКИ", "logged": "🟢 ВОШЁЛ В АККАУНТ",
                  "failed": "🔴 НЕ УДАЛОСЬ ВОЙТИ",
                  "bought": "🟡 КУПЛЕН, БЕЗ ВХОДА"}
        title = titles.get(filter_type, "📥 LZT-ЛОГИ")
        if not items:
            text = f"{title}\n\n😔 Записей нет."
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
            f"{title}\n\n"
            f"📊 Всего: {total}  |  Страница {page + 1} / {total_pages}")
        markup = kbd.lzt_logs_page(slice_, page, total_pages, filter_type, 5)
        if call:
            await edit_or_send(bot, call, text, markup)
        else:
            await safe_edit(bot, chat_id, edit_id, text, markup)

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs_all")
    async def cb_lzt_all(call):
        await bot.answer_callback_query(call.id)
        await _render_lzt_list(call.message.chat.id, "all", 0, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs_logged")
    async def cb_lzt_logged(call):
        await bot.answer_callback_query(call.id)
        await _render_lzt_list(call.message.chat.id, "logged", 0, call=call)

    @bot.callback_query_handler(func=lambda c: c.data == "lzt_logs_failed")
    async def cb_lzt_failed(call):
        await bot.answer_callback_query(call.id)
        await _render_lzt_list(call.message.chat.id, "failed", 0, call=call)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("lzt_logs_"))
    async def cb_lzt_page(call):
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
        await _render_lzt_list(call.message.chat.id, filter_type, page, call=call)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("lzt_log_view_"))
    async def cb_lzt_view(call):
        await bot.answer_callback_query(call.id)
        try:
            parts = call.data.split("_")
            item_id = int(parts[3])
            filter_type = parts[4]
            page = int(parts[5])
        except Exception as e:
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
            "📥 LZT-ЗАПИСЬ\n\n"
            f"🆔 Лот:        #{it.item_id}\n"
            f"📞 Номер:      {it.phone or '—'}\n"
            f"🌍 Страна:     {it.country or '—'}\n"
            f"💰 Цена:       {it.price or 0} ₽\n"
            f"📆 Куплен:     {_fmt_dt(it.bought_at)}\n"
            f"📌 Статус:     {_lzt_status_label(it.status)}\n"
            f"📦 В хранилище: {'✅ да' if in_storage else '❌ нет'}")
        if it.error:
            text += (f"\n\n⚠️ Ошибка: {it.error[:200]}\n\n"
                     f"💡 {_lzt_error_hint(it.error)}")
        can_retry = bool(it.phone) and not in_storage and it.status != "logged_in"
        await edit_or_send(bot, call, text,
                           kbd.lzt_log_detail_kb(item_id, filter_type, page,
                                                 can_retry))

    @bot.callback_query_handler(func=lambda c: c.data.startswith("lzt_retry_"))
    async def cb_lzt_retry(call):
        await bot.answer_callback_query(call.id, "🔁 Пробую войти...")
        try:
            parts = call.data.split("_")
            item_id = int(parts[2])
        except Exception:
            return
        await _retry_login_for_purchase(call, item_id)

    async def _retry_login_for_purchase(call, item_id: int):
        it = db.get_lzt_purchase(item_id)
        if not it:
            await edit_or_send(bot, call, "❌ Запись не найдена",
                               kbd.back_main())
            return
        phone = fmt_phone(it.phone) if it.phone else ""
        await edit_or_send(bot, call,
            "⏳ ПОВТОРНЫЙ ВХОД\n\n"
            f"🆔 Лот: #{item_id}\n"
            f"📞 Номер: {phone or '?'}\n\n"
            "Пробую получить код и войти...")
        err = _lzt_err()
        if err:
            await edit_or_send(bot, call, f"❌ {err}",
                               kbd.kb([[kbd.btn("🔙 НАЗАД", "lzt_logs")]]))
            return
        if not phone:
            await edit_or_send(bot, call,
                "❌ Не знаю номер телефона для этого лота.",
                kbd.back_main())
            return
        existing = db.get_account_by_phone(phone)
        if existing:
            db.update_lzt_purchase(item_id, "logged_in", phone=phone)
            await edit_or_send(bot, call,
                f"✅ Этот аккаунт уже в хранилище.\n\n📞 {phone}",
                kbd.kb([
                    [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{existing.id}_0")],
                    [kbd.btn("📥 LZT-ЛОГИ", "lzt_logs")]]))
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
            phone, item_id, market, password=password)
        if not session_str:
            db.update_lzt_purchase(item_id, "failed",
                                   phone=phone, error=str(status)[:200])
            db.log_action(call.from_user.id, "lzt_retry_fail",
                          details=f"item={item_id} {status}")
            await edit_or_send(bot, call,
                "❌ ПОВТОРНЫЙ ВХОД НЕ УДАЛСЯ\n\n"
                f"🆔 Лот: #{item_id}\n"
                f"📞 Номер: {phone}\n"
                f"❌ Причина: {status}\n\n"
                f"💡 {_lzt_error_hint(status)}",
                kbd.kb([
                    [kbd.btn("🔁 ПОВТОРИТЬ", f"lzt_retry_{item_id}_all_0")],
                    [kbd.btn("🔙 К LZT-ЛОГАМ", "lzt_logs")]]))
            return
        try:
            db.add_account(
                phone, session_str, password or "",
                name=phone,
                country_code=get_country_code(phone),
                price=int(it.price or 0),
                bought_from="lzt",
                lzt_item_id=item_id)
        except Exception as e:
            logger.error(f"[retry.db] {type(e).__name__}: {e}")
            await edit_or_send(bot, call,
                f"❌ Вход удался, но аккаунт не сохранился: {e}",
                kbd.back_main())
            return
        acc = db.get_account_by_phone(phone)
        if acc:
            try:
                schedule_auto_clean(acc.id)
            except Exception:
                pass
        db.update_lzt_purchase(item_id, "logged_in", phone=phone)
        db.stat_added(1)
        db.log_action(call.from_user.id, "lzt_retry_ok",
                      details=f"item={item_id} {phone}",
                      account_id=acc.id if acc else None)
        text = (
            "✅ ПОВТОРНЫЙ ВХОД УДАЛСЯ\n\n"
            f"🏷 Название: {phone}\n"
            f"📞 Номер:    {phone}\n"
            f"🌍 Страна:   {country_label(phone)}\n"
            + (f"🔐 2FA:      {password}\n" if password else "")
            + f"🆔 Лот:      {item_id}\n"
            "📌 Статус:   ⚠️ Ожидает очистки")
        markup = kbd.kb([
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc.id}_0")] if acc
            else kbd.btn("📦 ХРАНИЛИЩЕ", "storage"),
            [kbd.btn("📥 LZT-ЛОГИ", "lzt_logs")],
            [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")]])
        await edit_or_send(bot, call, text, markup)

    # ---------- ДОБАВЛЕНИЕ АККАУНТА ---------- #

    @bot.callback_query_handler(func=lambda c: c.data == "add_account")
    async def cb_add(call):
        await bot.answer_callback_query(call.id)
        if not is_admin(call.from_user.id):
            return
        user_state[call.from_user.id] = {"state": "add_name_pre"}
        pending_names.pop(call.from_user.id, None)
        auto_note = (
            "\n🔐 На аккаунт будет автоматически\n"
            "    установлен 2FA-пароль из конфига.\n"
            if PASSWORD_2FA
            else '\n🔓 Авто-2FA отключён.\n')
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "➕ ДОБАВЛЕНИЕ АККАУНТА\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📝 Шаг 1 из 4\n\n"
            "🏷 Введите название аккаунта (до 64 символов).\n\n"
            "Или отправьте «-», чтобы использовать номер."
            f"{auto_note}"
            f"\n📆 TTL сессий: {SESSION_TTL_DAYS} дней.")
        await edit_or_send(bot, call, text, kbd.cancel())

    async def state_add_name_pre(message, state):
        uid = message.from_user.id
        raw = (message.text or "").strip()
        if len(raw) > 64:
            await bot.send_message(message.chat.id, "❌ Максимум 64 символа")
            return
        if raw == "-" or not raw:
            pending_names[uid] = None
            shown = "по умолчанию = номер"
        else:
            pending_names[uid] = raw
            shown = raw
        user_state[uid] = {"state": "add_phone"}
        text = (
            "➕ Шаг 2 из 4\n\n"
            f"🏷 Название: {shown}\n\n"
            "📞 Введите номер телефона:\n"
            "+79991234567")
        await bot.send_message(message.chat.id, text, reply_markup=kbd.cancel())

    async def state_add_phone(message, state):
        uid = message.from_user.id
        phone = fmt_phone(message.text.strip())
        if not phone or len(phone) < 10 or not phone[1:].isdigit():
            await bot.send_message(message.chat.id, "❌ Формат: +79991234567")
            return
        name_val = pending_names.get(uid)
        name_shown = name_val or "по умолчанию = номер"
        await bot.send_message(
            message.chat.id,
            f"⏳ Отправляю код на {phone}...\n\n🏷 Название: {name_shown}")
        client, phone_hash, msg = await start_login(phone)
        if not client:
            await bot.send_message(message.chat.id, f"❌ {msg}",
                                   reply_markup=kbd.cancel())
            return
        pending_signins[uid] = {"client": client, "phone": phone,
                                "hash": phone_hash}
        user_state[uid] = {"state": "add_code"}
        text = (
            "➕ Шаг 3 из 4\n\n"
            f"🏷 Название: {name_shown}\n"
            f"📞 Номер: {phone}\n\n"
            "Введите код из Telegram:")
        await bot.send_message(message.chat.id, text, reply_markup=kbd.cancel())

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
                        f"🔐 Устанавливаю 2FA на {data['phone']}...")
                    s2, msg2 = await set_2fa_password(
                        data["client"], PASSWORD_2FA)
                    if s2:
                        two_fa_value = PASSWORD_2FA
                    else:
                        await bot.send_message(
                            message.chat.id,
                            f"⚠️ 2FA не установлен: {msg2}")
                elif password_used != PASSWORD_2FA:
                    await bot.send_message(
                        message.chat.id,
                        "🔐 Меняю 2FA на PASSWORD_2FA из конфига...")
                    s2, msg2 = await set_2fa_password(
                        data["client"], PASSWORD_2FA,
                        current_password=password_used)
                    if s2:
                        two_fa_value = PASSWORD_2FA
                    else:
                        await bot.send_message(
                            message.chat.id,
                            f"⚠️ 2FA не заменён: {msg2}")
            except Exception as e:
                logger.error(f"[auto_2fa] {data['phone']}: {type(e).__name__}: {e}")

        if SESSION_TTL_DAYS and SESSION_TTL_DAYS > 0:
            try:
                st, msg_t = await set_session_ttl(
                    data["client"], SESSION_TTL_DAYS)
                if not st:
                    await bot.send_message(
                        message.chat.id,
                        f"⚠️ TTL не установлен: {msg_t}")
            except Exception as e:
                logger.error(f"[set_ttl] {data['phone']}: {type(e).__name__}: {e}")
        try:
            session = data["client"].session.save()
        except Exception as e:
            logger.error(f"[post_login] save #2: {e}")
        return session, two_fa_value

    async def _finalize_add(message, uid, data, session, two_fa_value):
        name_val = pending_names.get(uid)
        final_name = name_val or data["phone"]
        phone = fmt_phone(data["phone"])

        saved = False; db_error = ""
        try:
            saved = db.add_account(
                phone, session, two_fa_value,
                name=final_name,
                country_code=get_country_code(phone),
                bought_from="manual")
            if not saved:
                db_error = "add_account вернул False"
        except TypeError as e:
            db_error = f"TypeError: {e}"
            try:
                saved = db.add_account(phone, session, two_fa_value)
            except Exception as e2:
                logger.error(f"[db] fallback: {e2}")
        except Exception as e:
            db_error = f"{type(e).__name__}: {e}"

        acc = None
        try:
            acc = db.get_account_by_phone(phone)
        except Exception:
            pass

        if not acc:
            await bot.send_message(
                message.chat.id,
                f"❌ НЕ УДАЛОСЬ СОХРАНИТЬ\n\n📞 {phone}\n\nОшибка: {db_error}")
            pending_signins.pop(uid, None)
            pending_names.pop(uid, None)
            user_state.pop(uid, None)
            return
        await close_client(phone)
        try:
            schedule_auto_clean(acc.id)
        except Exception:
            pass
        pending_signins.pop(uid, None)
        pending_names.pop(uid, None)
        user_state.pop(uid, None)
        db.stat_added(1)
        db.log_action(uid, "add", details=phone, account_id=acc.id)
        two_fa_line = (f"🔐 2FA:      {two_fa_value}"
                       if two_fa_value else "🔐 2FA:      не задан")
        text = (
            "✅ АККАУНТ ДОБАВЛЕН\n\n"
            f"🏷 Название: {acc.name or final_name}\n"
            f"📞 Номер:    {phone}\n"
            f"🌍 Страна:   {country_label(phone)}\n"
            f"{two_fa_line}\n"
            f"📆 TTL:      {SESSION_TTL_DAYS} дней\n"
            "📌 Статус:   ⚠️ Ожидает очистки")
        markup = kbd.kb([
            [kbd.btn("🔙 К АККАУНТУ", f"acc_view_{acc.id}_0")],
            [kbd.btn("📦 ХРАНИЛИЩЕ", "storage")],
            [kbd.btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")]])
        try:
            await bot.send_message(message.chat.id, text, reply_markup=markup)
        except Exception as e:
            logger.error(f"[finalize] send: {e}")

    async def state_add_code(message, state):
        uid = message.from_user.id
        code = message.text.strip().replace(" ", "")
        data = pending_signins.get(uid)
        if not data:
            await bot.send_message(message.chat.id, "❌ Сессия потеряна")
            user_state.pop(uid, None); pending_names.pop(uid, None)
            return
        ok, msg, session = await confirm_code(
            data["client"], data["phone"], data["hash"], code)
        if ok:
            session, two_fa_value = await _apply_post_login(
                message, data, password_used="")
            await _finalize_add(message, uid, data, session, two_fa_value)
            return
        if msg == "NEED_2FA":
            user_state[uid] = {"state": "add_2fa"}
            name_shown = pending_names.get(uid) or "по умолчанию = номер"
            text = (
                "➕ Шаг 4 из 4\n\n"
                f"🏷 Название: {name_shown}\n"
                f"📞 Номер: {data['phone']}\n\n"
                "🔐 Введите текущий пароль 2FA:")
            await bot.send_message(message.chat.id, text,
                                   reply_markup=kbd.cancel())
            return
        await bot.send_message(message.chat.id, f"❌ {msg}")

    async def state_add_2fa(message, state):
        uid = message.from_user.id
        password = message.text.strip()
        data = pending_signins.get(uid)
        if not data:
            await bot.send_message(message.chat.id, "❌ Сессия потеряна")
            user_state.pop(uid, None); pending_names.pop(uid, None)
            return
        ok, msg, session = await confirm_2fa(data["client"], password)
        if not ok:
            await bot.send_message(message.chat.id, f"❌ {msg}")
            return
        session, two_fa_value = await _apply_post_login(
            message, data, password_used=password)
        await _finalize_add(message, uid, data, session, two_fa_value)

    # ---------- STATE_HANDLERS + ROUTER ---------- #

    STATE_HANDLERS = {
        "add_name_pre": state_add_name_pre,
        "add_phone": state_add_phone,
        "add_code": state_add_code,
        "add_2fa": state_add_2fa,
        "rename_account": state_rename_account,
        "buy_country": state_buy_country,
        "buy_price": state_buy_price,
        "buy_bulk_count": state_buy_bulk_count,
        "buy_bulk_price": state_buy_bulk_price,
    }

    @bot.message_handler(func=lambda m: True, content_types=["text"])
    async def text_router(message: types.Message):
        uid = message.from_user.id
        if message.text and message.text.startswith("/"):
            return
        state = user_state.get(uid, {})
        handler = STATE_HANDLERS.get(state.get("state"))
        if handler:
            try:
                await handler(message, state)
            except Exception as e:
                logger.error(f"[text_router] {type(e).__name__}: {e}")
                logger.debug(traceback.format_exc())
                user_state.pop(uid, None)
                pending_names.pop(uid, None)
                await bot.send_message(message.chat.id, f"❌ {str(e)[:150]}")
            return
        await bot.send_message(message.chat.id, "Используйте /start")