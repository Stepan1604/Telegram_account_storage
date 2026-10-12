import asyncio
import logging
import re
import traceback

from telethon import TelegramClient, errors, functions
from telethon.sessions import StringSession
from telethon.tl.functions.auth import ResetAuthorizationsRequest
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.messages import (
    DeleteChatUserRequest,
    DeleteHistoryRequest,
)
from telethon.tl.types import AccountDaysTTL

from config import API_ID, API_HASH
from .helpers import fmt_phone

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#                          КЭШ КЛИЕНТОВ                                        #
# --------------------------------------------------------------------------- #

_clients: dict = {}
_lock = asyncio.Lock()


# ═══════════════════════════════════════════════════════════════════════════ #
#                          УПРАВЛЕНИЕ КЛИЕНТАМИ                                #
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


async def close_all_clients() -> None:
    for phone in list(_clients.keys()):
        await close_client(phone)


# ═══════════════════════════════════════════════════════════════════════════ #
#                            ПРОВЕРКА / КОД                                    #
# ═══════════════════════════════════════════════════════════════════════════ #

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
    """Расширенная инфа: профиль + сессии + чаты / каналы / группы."""
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
                client(GetAuthorizationsRequest()), timeout=15
            )
            result["sessions"] = len(auths.authorizations or [])
        except Exception as e:
            logger.warning(f"[acc_info] {phone}: get_authorizations: {e}")

        # 3) Диалоги
        try:
            dialogs = await asyncio.wait_for(
                client.get_dialogs(limit=None), timeout=30
            )
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


# ═══════════════════════════════════════════════════════════════════════════ #
#                            ОЧИСТКА ДИАЛОГОВ                                  #
# ═══════════════════════════════════════════════════════════════════════════ #

async def clear_all_dialogs(client: TelegramClient, phone: str):
    """
    Полная очистка: каналы, группы, личные чаты.
    Возвращает (deleted, failed, total).
    """
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
                        timeout=15,
                    )
                    deleted += 1
                    handled = True
                except errors.FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 60))
                    try:
                        await asyncio.wait_for(
                            client(LeaveChannelRequest(channel=entity)),
                            timeout=15,
                        )
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
                            chat_id=entity.id, user_id=my_id,
                        )),
                        timeout=15,
                    )
                    deleted += 1
                    handled = True
                except errors.FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 60))
                    try:
                        await asyncio.wait_for(
                            client(DeleteChatUserRequest(
                                chat_id=entity.id, user_id=my_id,
                            )),
                            timeout=15,
                        )
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
                            just_clear=False, revoke=True,
                        )),
                        timeout=15,
                    )
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


# ═══════════════════════════════════════════════════════════════════════════ #
#                             ВХОД В АККАУНТ                                   #
# ═══════════════════════════════════════════════════════════════════════════ #

async def start_login(phone: str):
    """Отправляет код. Возвращает (client, phone_code_hash, status)."""
    phone = fmt_phone(phone)
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
        sent = await asyncio.wait_for(
            client.send_code_request(phone), timeout=15
        )
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
    """Ввод кода из Telegram. Возвращает (ok, msg, session)."""
    try:
        await asyncio.wait_for(
            client.sign_in(phone, code, phone_code_hash=phone_code_hash),
            timeout=15,
        )
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
    """Ввод пароля 2FA. Возвращает (ok, msg, session)."""
    try:
        await asyncio.wait_for(client.sign_in(password=password), timeout=15)
        return True, "OK", client.session.save()
    except errors.PasswordHashInvalidError:
        return False, "Неверный пароль 2FA", None
    except Exception as e:
        logger.error(f"[confirm_2fa]: {type(e).__name__}: {e}")
        return False, str(e)[:200], None


# ═══════════════════════════════════════════════════════════════════════════ #
#                             НАСТРОЙКИ АККАУНТА                               #
# ═══════════════════════════════════════════════════════════════════════════ #

async def set_2fa_password(client, new_password: str,
                           current_password: str = None):
    """Устанавливает или меняет 2FA-пароль."""
    try:
        if current_password:
            await asyncio.wait_for(
                client.edit_2fa(
                    current_password=current_password,
                    new_password=new_password,
                    hint="",
                ),
                timeout=30,
            )
        else:
            await asyncio.wait_for(
                client.edit_2fa(new_password=new_password, hint=""),
                timeout=30,
            )
        return True, "OK"
    except errors.PasswordHashInvalidError:
        return False, "Неверный текущий пароль 2FA"
    except Exception as e:
        logger.error(f"[set_2fa]: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:150]}"


async def set_session_ttl(client, days: int = 365):
    """Устанавливает TTL — через сколько дней неактивности Telegram удалит сессии."""
    try:
        days = max(1, min(int(days), 366))
        await asyncio.wait_for(
            client(functions.account.SetAccountTTLRequest(
                ttl=AccountDaysTTL(days=days)
            )),
            timeout=15,
        )
        return True, "OK"
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек."
    except Exception as e:
        logger.error(f"[set_ttl]: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:150]}"