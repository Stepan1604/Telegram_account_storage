import asyncio
import logging
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

    Единая точка создания клиентов. Благодаря регистрации в _all_clients
    все клиенты могут быть гарантированно закрыты при завершении бота
    через shutdown_all_clients().

    :param session_string: строка сессии (StringSession). Пустая — для
        нового входа.
    :return: экземпляр TelegramClient.
    """
    c = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    _all_clients.add(c)
    return c


async def _safe_disconnect(c: TelegramClient) -> None:
    """
    Безопасно отключает клиент, защищая операцию от отмены event loop.

    Использует asyncio.shield, чтобы disconnect не был прерван, если
    loop завершается. Удаляет клиент из реестра _all_clients.

    :param c: экземпляр TelegramClient для отключения.
    """
    _all_clients.discard(c)
    try:
        await asyncio.shield(c.disconnect())
    except Exception as e:
        logger.warning(f"[disconnect] {type(e).__name__}: {e}")


async def shutdown_all_clients() -> None:
    """
    Закрывает все созданные Telegram-клиенты.

    Вызывается при завершении работы бота, пока event loop ещё активен.
    Параллельно отключает все клиенты из реестра _all_clients и очищает
    реестр.
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

    Кэширует клиенты по номеру телефона. Если в кэше есть клиент, но
    он отключён, создаёт новый.

    :param phone: номер телефона (ключ кэша).
    :param session_string: строка сессии.
    :return: подключённый TelegramClient.
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

    :param phone: номер телефона.
    """
    async with _lock:
        c = _clients.pop(phone, None)
    if c:
        await _safe_disconnect(c)


async def close_all_clients() -> None:
    """
    Закрывает все клиенты из кэша.

    Совместимость с предыдущим API. Для полного закрытия используй
    shutdown_all_clients().
    """
    for phone in list(_clients.keys()):
        await close_client(phone)


async def _close_on_error(phone: str) -> None:
    """
    Закрывает клиент, чтобы следующий вызов пересоздал его с нуля.

    :param phone: номер телефона.
    """
    try:
        await close_client(phone)
    except Exception as e:
        logger.warning(f"[close_client] {phone}: {e}")


async def _classify_call(coro_factory, phone: str, phase: str):
    """
    Выполняет корутину, ловит ошибки Telethon и превращает их в
    (state, info)-пару.

    :return: (result, None) или (None, (state, info)),
        где state ∈ {"dead", "unknown"}.
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

    Возвращает три состояния: alive / dead / unknown.

    :param session_string: строка сессии.
    :param phone: номер телефона.
    :return: (state, info) — кортеж строк.
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

    :param session_string: строка сессии.
    :param phone: номер телефона.
    :return: (str, str) — (код, "OK") при успехе; (None, причина) при ошибке.
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

    :param session_string: строка сессии.
    :param phone: номер телефона.
    :return: словарь с ключами ok, error, first_name, last_name, username,
        user_id, sessions, chats, channels, groups, total_dialogs.
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

    :param client: подключённый TelegramClient.
    :param phone: номер телефона (для логов).
    :return: (deleted, failed, total).
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
    """
    Завершает все чужие сессии аккаунта и очищает диалоги.

    :param session_string: строка сессии.
    :param phone: номер телефона.
    :return: (bool, str).
    """
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
    """
    Очищает диалоги без сброса сессий.

    :param session_string: строка сессии.
    :param phone: номер телефона.
    :return: (bool, str, int, int, int).
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

    :param phone: номер телефона.
    :return: (client, phone_code_hash, status).
    """
    phone = fmt_phone(phone)
    client = make_client()
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
        sent = await asyncio.wait_for(
            client.send_code_request(phone), timeout=15
        )
        return client, sent.phone_code_hash, "OK"
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

    :return: (bool, str, str) — (успех, сообщение, session_string).
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

    :return: (bool, str, str).
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

    :return: (bool, str).
    """
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
    except errors.PasswordMustAttemptError:
        return False, "Неверный пароль. Повторите ввод текущего пароля 2FA"
    except errors.PasswordHashInvalidError:
        return False, "Неверный текущий пароль 2FA"
    except Exception as e:
        logger.error(f"[set_2fa]: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:150]}"


async def request_email_code(client, phone: str, email: str):
    """
    Отправляет код подтверждения на email для привязки почты к аккаунту.

    Сначала запрашивает новый phone_code_hash через SendCodeRequest,
    затем вызывает SendVerifyEmailCodeRequest с purpose=LoginSetup.

    :param client: активный TelegramClient.
    :param phone: номер телефона (в формате +XXXXXXXXXXX).
    :param email: email для привязки.
    :return: (ok, msg, phone_code_hash, length):
        - ok=True, msg="OK", phone_code_hash=str, length=int при успехе.
        - ok=False, msg=причина, phone_code_hash=None, length=0 при ошибке.
    """
    phone = fmt_phone(phone)
    try:
        sent_code = await asyncio.wait_for(
            client(functions.auth.SendCodeRequest(
                phone_number=phone,
                api_id=API_ID,
                api_hash=API_HASH,
                settings=types.CodeSettings(),
            )),
            timeout=20,
        )
        phone_code_hash = sent_code.phone_code_hash

        result = await asyncio.wait_for(
            client(functions.account.SendVerifyEmailCodeRequest(
                purpose=types.EmailVerifyPurposeLoginSetup(
                    phone_number=phone,
                    phone_code_hash=phone_code_hash,
                ),
                email=email,
            )),
            timeout=20,
        )
        return True, "OK", phone_code_hash, result.length

    except errors.EmailInvalidError:
        return False, f"Некорректный email: {email}", None, 0
    except errors.EmailNotAllowedError:
        return False, f"Email {email} нельзя использовать (занят)", None, 0
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек.", None, 0
    except errors.AuthKeyUnregisteredError:
        return False, "Ключ аннулирован", None, 0
    except errors.UserDeactivatedBanError:
        return False, "Аккаунт забанен", None, 0
    except asyncio.TimeoutError:
        return False, "Таймаут при отправке кода на email", None, 0
    except Exception as e:
        logger.error(f"[request_email] {phone}: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:150]}", None, 0


async def confirm_email_code(client, phone: str, phone_code_hash: str,
                             code: str):
    """
    Подтверждает код из письма и привязывает email к аккаунту.

    :param client: активный TelegramClient.
    :param phone: номер телефона.
    :param phone_code_hash: хэш из request_email_code.
    :param code: код из письма.
    :return: (ok, msg, email):
        - ok=True, msg="OK", email=привязанный email при успехе.
        - ok=False, msg=причина, email=None при ошибке.
    """
    phone = fmt_phone(phone)
    try:
        result = await asyncio.wait_for(
            client(functions.account.VerifyEmailRequest(
                purpose=types.EmailVerifyPurposeLoginSetup(
                    phone_number=phone,
                    phone_code_hash=phone_code_hash,
                ),
                verification=types.EmailVerificationCode(code=code),
            )),
            timeout=20,
        )
        if isinstance(result, types.account.EmailVerifiedLogin):
            return True, "OK", result.email
        return True, "OK", None

    except errors.EmailCodeInvalidError:
        return False, "Неверный код из email", None
    except errors.EmailCodeExpiredError:
        return False, "Код из email истёк", None
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds} сек.", None
    except asyncio.TimeoutError:
        return False, "Таймаут при подтверждении email", None
    except Exception as e:
        logger.error(f"[confirm_email] {phone}: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:150]}", None