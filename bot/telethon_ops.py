import asyncio
import logging
import time
import re
import traceback

from telethon import TelegramClient, errors, functions, types
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

_clients: dict = {}
_all_clients: set = set()
_lock = asyncio.Lock()


def make_client(session_string: str = "") -> TelegramClient:
    """
    Создаёт TelegramClient и регистрирует его в глобальном реестре.
    """
    c = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    _all_clients.add(c)
    return c


async def _safe_disconnect(c: TelegramClient) -> None:
    """
    Безопасно отключает клиент.
    """
    _all_clients.discard(c)
    try:
        await asyncio.shield(c.disconnect())
    except Exception as e:
        logger.warning(f"[disconnect] {type(e).__name__}: {e}")


async def shutdown_all_clients() -> None:
    """
    Закрывает все созданные Telegram-клиенты.
    """
    clients = list(_all_clients)
    if not clients:
        logger.info("🔌 Нет активных Telethon-клиентов")
        return
    logger.info(f"🔌 Закрываю {len(clients)} Telethon-клиентов...")
    await asyncio.gather(
        *(_safe_disconnect(c) for c in clients),
        return_exceptions=True,
    )
    _all_clients.clear()
    logger.info("✅ Все Telethon-клиенты закрыты")


async def _get_client(phone: str, session_string: str) -> TelegramClient:
    """
    Возвращает клиент из кэша или создаёт новый.
    """
    async with _lock:
        c = _clients.get(phone)
        if c and c.is_connected():
            return c
        if c:
            await _safe_disconnect(c)
        c = make_client(session_string)
        try:
            await asyncio.wait_for(c.connect(), timeout=15)
        except Exception:
            await _safe_disconnect(c)
            raise
        _clients[phone] = c
        return c


async def close_client(phone: str) -> None:
    """
    Закрывает и удаляет клиент из кэша по номеру телефона.
    """
    async with _lock:
        c = _clients.pop(phone, None)
    if c:
        await _safe_disconnect(c)


async def close_all_clients() -> None:
    """
    Закрывает все клиенты из кэша.
    """
    for phone in list(_clients.keys()):
        await close_client(phone)


async def _close_on_error(phone: str) -> None:
    """
    Закрывает клиент, чтобы следующий вызов пересоздал его с нуля.
    """
    try:
        await close_client(phone)
    except Exception as e:
        logger.warning(f"[close_client] {phone}: {e}")


async def _classify_call(coro_factory, phone: str, phase: str):
    """
    Выполняет корутину, ловит ошибки Telethon и превращает их в
    (state, info)-пару.
    """
    try:
        return await coro_factory(), None
    except errors.AuthKeyUnregisteredError:
        return None, ("dead", "Ключ аннулирован")
    except errors.UserDeactivatedBanError:
        return None, ("dead", "Аккаунт забанен")
    except errors.UserDeactivatedError:
        return None, ("dead", "Аккаунт деактивирован")
    except errors.FloodWaitError as e:
        return None, ("unknown", f"FloodWait: {e.seconds} сек.")
    except asyncio.TimeoutError:
        return None, ("unknown", f"Таймаут {phase}")
    except Exception as e:
        logger.error(f"[{phase}] {phone}: {type(e).__name__}: {e}")
        logger.debug(traceback.format_exc())
        return None, ("unknown", f"Ошибка {phase}: {str(e)[:120]}")


async def is_alive(session_string: str, phone: str):
    """
    Проверяет, жива ли сессия аккаунта.
    """
    if not session_string:
        return "dead", "Нет session"

    try:
        c = await _get_client(phone, session_string)
    except errors.AuthKeyUnregisteredError:
        return "dead", "Ключ аннулирован"
    except errors.UserDeactivatedBanError:
        return "dead", "Аккаунт забанен"
    except errors.UserDeactivatedError:
        return "dead", "Аккаунт деактивирован"
    except errors.FloodWaitError as e:
        return "unknown", f"FloodWait: {e.seconds} сек."
    except asyncio.TimeoutError:
        return "unknown", "Таймаут подключения"
    except Exception as e:
        logger.error(f"[is_alive.connect] {phone}: "
                     f"{type(e).__name__}: {e}")
        return "unknown", f"Ошибка подключения: {str(e)[:120]}"

    authorized, err = await _classify_call(
        lambda: asyncio.wait_for(c.is_user_authorized(), timeout=10),
        phone, "auth",
    )
    if err:
        await _close_on_error(phone)
        return err
    if not authorized:
        await _close_on_error(phone)
        return "dead", "Сессия не авторизована"

    me, err = await _classify_call(
        lambda: asyncio.wait_for(c.get_me(), timeout=10),
        phone, "get_me",
    )
    if err:
        await _close_on_error(phone)
        return err
    if not me:
        await _close_on_error(phone)
        return "unknown", "Нет данных профиля"

    return "alive", me.first_name or "OK"


async def read_login_code(session_string: str, phone: str):
    """
    Читает код входа из сервисного чата Telegram (777000).
    """
    if not session_string:
        return None, "Нет session"
    c = await _get_client(phone, session_string)
    if not await c.is_user_authorized():
        await close_client(phone)
        return None, "Сессия не авторизована"
    try:
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
    """
    Получает расширенную информацию об аккаунте.
    """
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

    c = await _get_client(phone, session_string)
    try:
        if not await c.is_user_authorized():
            await close_client(phone)
            result["error"] = "Сессия не авторизована"
            return result

        try:
            me = await asyncio.wait_for(c.get_me(), timeout=10)
            if me:
                result["first_name"] = me.first_name or ""
                result["last_name"] = me.last_name or ""
                result["username"] = f"@{me.username}" if me.username else ""
                result["user_id"] = me.id
        except Exception as e:
            logger.warning(f"[acc_info] {phone}: get_me: {e}")

        try:
            from telethon.tl.functions.account import GetAuthorizationsRequest
            auths = await asyncio.wait_for(
                c(GetAuthorizationsRequest()), timeout=15
            )
            result["sessions"] = len(auths.authorizations or [])
        except Exception as e:
            logger.warning(f"[acc_info] {phone}: get_authorizations: {e}")

        try:
            dialogs = await asyncio.wait_for(
                c.get_dialogs(limit=None), timeout=30
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
        logger.debug(traceback.format_exc())
        result["error"] = f"Ошибка: {str(e)[:120]}"
    finally:
        await close_client(phone)
    return result


async def clear_all_dialogs(client: TelegramClient, phone: str):
    """
    Полностью очищает диалоги аккаунта: каналы, группы, личные чаты.
    """
    t0 = time.perf_counter()
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
                f"ошибок={failed}, всего={total}, "
                f"за {time.perf_counter() - t0:.1f} с")
    return deleted, failed, total


async def reset_other_sessions(session_string: str, phone: str):
    """
    Завершает все чужие сессии аккаунта и очищает диалоги.
    """
    if not session_string:
        return False, "Нет session"
    try:
        c = await _get_client(phone, session_string)
        if not await c.is_user_authorized():
            await close_client(phone)
            return False, "Сессия не авторизована"

        t_reset0 = time.perf_counter()
        try:
            await asyncio.wait_for(c(ResetAuthorizationsRequest()), timeout=20)
        except errors.FrozenMethodInvalidError:
            logger.warning(f"[reset_sessions] {phone}: аккаунт ЗАМОРОЖЕН")
            return False, "FROZEN"

        logger.info(f"[reset_sessions] {phone}: сброс сессий "
                    f"{time.perf_counter() - t_reset0:.1f} с")
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
        name = type(e).__name__
        if "FRESHRESET" in name.upper() or "FRESH_RESET" in str(e).upper():
            logger.info(f"[reset_sessions] {phone}: сессия слишком новая")
            return False, ("Сессия слишком новая: Telegram разрешает сброс "
                           "примерно через 24 ч после входа")
        logger.error(f"[reset_sessions] {phone}: {name}: {e}")
        return False, f"{name}: {str(e)[:150]}"


async def clear_dialogs_only(session_string: str, phone: str):
    """
    Очищает диалоги без сброса сессий.
    """
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
    """
    Отправляет код подтверждения на указанный номер.
    """
    phone = fmt_phone(phone)
    client = make_client()
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
        sent = await asyncio.wait_for(
            client.send_code_request(phone), timeout=15
        )
        return client, sent.phone_code_hash, "OK"
    except errors.AuthRestartError as e:
        logger.warning(f"[start_login] {phone}: AuthRestartError: {e}")
        await _safe_disconnect(client)
        return None, None, "Telegram попросил перезапустить авторизацию — попробуйте ещё раз"
    except errors.FloodWaitError as e:
        await _safe_disconnect(client)
        return None, None, f"FloodWait: {e.seconds} сек."
    except errors.PhoneNumberBannedError:
        await _safe_disconnect(client)
        return None, None, "Номер забанен"
    except errors.PhoneNumberInvalidError:
        await _safe_disconnect(client)
        return None, None, "Неверный номер"
    except errors.PhoneNumberFloodError:
        await _safe_disconnect(client)
        return None, None, "Слишком много запросов на номер"
    except Exception as e:
        logger.error(f"[start_login] {phone}: {type(e).__name__}: {e}")
        await _safe_disconnect(client)
        return None, None, f"Ошибка: {str(e)[:200]}"


async def confirm_code(client, phone, phone_code_hash, code):
    """
    Подтверждает вход кодом из Telegram.
    """
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
    """
    Подтверждает вход паролем 2FA.
    """
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
    """
    Устанавливает или меняет пароль 2FA.

    Ошибки определяются по имени класса, потому что некоторых классов
    может не быть в установленной версии Telethon. Ожидаемый отказ
    (неверный пароль) логируется как WARNING, ERROR — только для
    неожиданных ошибок.
    """
    kwargs = {"new_password": new_password, "hint": ""}
    if current_password:
        kwargs["current_password"] = current_password
    try:
        await asyncio.wait_for(client.edit_2fa(**kwargs), timeout=30)
        return True, "OK"
    except asyncio.TimeoutError:
        logger.error("[set_2fa]: таймаут")
        return False, "Таймаут при смене 2FA"
    except Exception as e:
        name = type(e).__name__
        text = str(e)
        if "PasswordHashInvalid" in name or "PASSWORD_HASH_INVALID" in text:
            logger.warning(f"[set_2fa]: текущий пароль не подошёл ({name})")
            return False, "Неверный текущий пароль 2FA"
        if "PasswordMustAttempt" in name:
            logger.warning(f"[set_2fa]: нужен текущий пароль ({name})")
            return False, "Неверный пароль. Повторите ввод текущего пароля 2FA"
        logger.error(f"[set_2fa]: {name}: {text[:300]}")
        return False, f"{name}: {text[:150]}"


async def _request_email_change(client, email: str):
    """
    Пробует ЗАМЕНИТЬ почту для входа (LoginChange).

    Работает только если у аккаунта уже есть привязанная почта.
    Если её нет, Telegram вернёт EMAIL_NOT_SETUP — это не ошибка,
    а сигнал вызывающему коду перейти к установке новой почты.

    :return: (ok, code_or_msg, length).
        code_or_msg при неудаче — "EMAIL_NOT_SETUP" (для разбора вызывающим
        кодом) либо готовое сообщение для пользователя в остальных случаях.
    """
    try:
        result = await asyncio.wait_for(
            client(functions.account.SendVerifyEmailCodeRequest(
                purpose=types.EmailVerifyPurposeLoginChange(),
                email=email,
            )),
            timeout=20,
        )
        return True, "OK", result.length
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек.", 0
    except asyncio.TimeoutError:
        return False, "Таймаут при отправке кода на email", 0
    except ConnectionError as e:
        logger.error(f"[email.change] ConnectionError: {e}")
        return False, "Клиент отключён — повторите попытку", 0
    except Exception as e:
        name = type(e).__name__
        full = (name + " " + str(e)).upper()
        if "EMAIL_NOT_SETUP" in full:
            return False, "EMAIL_NOT_SETUP", 0
        if "EMAIL_NOT_ALLOWED" in full:
            return False, (
                f"Telegram не разрешил email {email}: "
                "занят или домен запрещён"
            ), 0
        if "EMAIL_INVALID" in full:
            return False, f"Некорректный email: {email}", 0
        if "AUTH_RESTART" in full:
            return False, "Telegram попросил перезапустить авторизацию", 0
        if "AUTHKEY_UNREGISTERED" in full:
            return False, "Ключ аннулирован", 0
        if "USER_DEACTIVATED_BAN" in full:
            return False, "Аккаунт забанен", 0
        logger.error(f"[email.change] {name}: {e}")
        return False, f"{name}: {str(e)[:150]}", 0


async def _request_email_setup(client, phone: str, email: str):
    """
    Устанавливает НОВУЮ почту для входа (LoginSetup) — для аккаунтов,
    у которых почты ещё нет.

    Для этого режима Telegram требует phone_code_hash, который
    выдаётся только при запросе кода входа (как при обычном логине).
    Поэтому здесь дополнительно вызывается send_code_request — это
    отправит служебный код входа в сам аккаунт (не email-код, его
    вводить не нужно, Telegram использует только сам факт запроса).

    :return: (ok, msg, length, phone_code_hash).
    """
    try:
        sent = await asyncio.wait_for(
            client.send_code_request(phone), timeout=15)
        phone_code_hash = sent.phone_code_hash
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек.", 0, None
    except asyncio.TimeoutError:
        return False, "Таймаут при запросе кода входа", 0, None
    except Exception as e:
        logger.error(f"[email.setup] send_code_request: "
                     f"{type(e).__name__}: {e}")
        return False, f"не удалось запросить код входа: " \
                       f"{type(e).__name__}", 0, None

    try:
        result = await asyncio.wait_for(
            client(functions.account.SendVerifyEmailCodeRequest(
                purpose=types.EmailVerifyPurposeLoginSetup(
                    phone_number=phone, phone_code_hash=phone_code_hash),
                email=email,
            )),
            timeout=20,
        )
        return True, "OK", result.length, phone_code_hash
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек.", 0, None
    except asyncio.TimeoutError:
        return False, "Таймаут при отправке кода на email", 0, None
    except Exception as e:
        name = type(e).__name__
        full = (name + " " + str(e)).upper()
        if "EMAIL_INVALID" in full:
            return False, f"Некорректный email: {email}", 0, None
        if "EMAIL_NOT_ALLOWED" in full:
            return False, (
                f"Telegram не разрешил email {email}: "
                "занят или домен запрещён"
            ), 0, None
        logger.error(f"[email.setup] {name}: {e}")
        return False, f"{name}: {str(e)[:150]}", 0, None


async def request_email_code(client, phone: str, email: str):
    """
    Запрашивает код для привязки email к авторизованному аккаунту.

    Сначала пробует ЗАМЕНИТЬ уже привязанную почту (LoginChange).
    Если у аккаунта почты ещё нет (EMAIL_NOT_SETUP), переходит к
    её УСТАНОВКЕ (LoginSetup) — это отправит служебный код входа
    в сам аккаунт, см. _request_email_setup.

    :return: (ok, msg, ctx, length).
        ctx — словарь с режимом ("change" или "setup") и, для setup,
        с phone_code_hash. Его нужно передать как есть в
        confirm_email_code. ctx = None при неудаче.
    """
    phone = fmt_phone(phone)
    try:
        if not await asyncio.wait_for(
                client.is_user_authorized(), timeout=10):
            return False, "Сессия не авторизована", None, 0
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:100]}", None, 0

    ok, code_or_msg, length = await _request_email_change(client, email)
    if ok:
        return True, "OK", {"mode": "change"}, length
    if code_or_msg != "EMAIL_NOT_SETUP":
        return False, code_or_msg, None, 0

    logger.info(f"[request_email] {phone}: почты нет, "
                f"устанавливаю новую (LoginSetup)")
    ok, msg, length, phone_code_hash = await _request_email_setup(
        client, phone, email)
    if not ok:
        return False, msg, None, 0
    return True, "OK", {
        "mode": "setup", "phone_code_hash": phone_code_hash,
    }, length


async def confirm_email_code(client, phone: str, ctx, code: str):
    """
    Подтверждает код из письма для привязки email.

    ctx — то, что вернул request_email_code (словарь с режимом).
    Если ctx не передан (None) или в нём нет режима, используется
    замена (LoginChange) — это поведение совместимо со старыми
    вызовами, где третий параметр передавался как hash и игнорировался.

    :return: (ok, msg, email).
    """
    phone = fmt_phone(phone)
    mode = "change"
    phone_code_hash = None
    if isinstance(ctx, dict):
        mode = ctx.get("mode", "change")
        phone_code_hash = ctx.get("phone_code_hash")

    if mode == "setup":
        if not phone_code_hash:
            return False, (
                "Код для установки почты устарел — "
                "запросите привязку заново"
            ), None
        purpose = types.EmailVerifyPurposeLoginSetup(
            phone_number=phone, phone_code_hash=phone_code_hash)
    else:
        purpose = types.EmailVerifyPurposeLoginChange()

    try:
        result = await asyncio.wait_for(
            client(functions.account.VerifyEmailRequest(
                purpose=purpose,
                verification=types.EmailVerificationCode(code=code),
            )),
            timeout=20,
        )
        return True, "OK", getattr(result, "email", None)

    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек.", None
    except asyncio.TimeoutError:
        return False, "Таймаут при подтверждении email", None
    except ConnectionError as e:
        logger.error(f"[confirm_email] {phone}: ConnectionError: {e}")
        return False, "Клиент отключён — повторите попытку", None
    except Exception as e:
        name = type(e).__name__
        full = (name + " " + str(e)).upper()

        if "EMAIL_CODE_INVALID" in full:
            return False, "Неверный код из email", None
        if "EMAIL_CODE_EXPIRED" in full:
            return False, "Код из email истёк", None
        if "EMAIL_NOT_ALLOWED" in full:
            return False, (
                "Telegram не разрешил этот email: "
                "занят или домен запрещён"
            ), None
        if "PHONE_CODE_EXPIRED" in full or "PHONE_CODE_INVALID" in full:
            return False, (
                "Код входа, нужный для установки почты, истёк — "
                "запросите привязку заново"
            ), None

        logger.error(f"[confirm_email] {phone}: {name}: {e}")
        return False, f"{name}: {str(e)[:150]}", None
