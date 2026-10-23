import asyncio
import json
import logging
import os
import random
import re
from typing import Optional, Tuple

from telethon import errors
from telethon.tl.functions.photos import UploadProfilePhotoRequest

from config import (
    LZT_API_TOKEN, LZT_PROXY,
    BUY_DELAY, CODE_DELAY, CODE_ATTEMPTS, CODE_ROUNDS,
    MIN_PRICE, SPAM_FILTER,
)
from .telethon_ops import make_client, _safe_disconnect

logger = logging.getLogger(__name__)

try:
    from LOLZTEAM.Client import Market
    LZT_AVAILABLE = True
except ImportError:
    LZT_AVAILABLE = False
    Market = None
    logger.error("[lzt] LOLZTEAM не установлен: pip install LOLZTEAM")

AVATAR_DIR = "avatars"


def fmt_phone(phone: str) -> str:
    """
    Приводит номер телефона к формату +XXXXXXXXXXX.

    :param phone: номер в любом формате.
    :return: нормализованный номер или исходное значение, если пусто.
    """
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


def make_market():
    """
    Создаёт клиент LZT Market для запросов к API.

    :raises RuntimeError: если LOLZTEAM не установлен или токен пуст.
    :return: экземпляр Market.
    """
    if not LZT_AVAILABLE:
        raise RuntimeError("LOLZTEAM не установлен: pip install LOLZTEAM")
    if not LZT_API_TOKEN:
        raise RuntimeError("LZT_API_TOKEN пуст в config.py")
    m = Market(token=LZT_API_TOKEN)
    if LZT_PROXY:
        m.settings.proxy = LZT_PROXY
    return m


async def get_balance(market) -> Tuple[Optional[float], Optional[int]]:
    """
    Получает баланс аккаунта LZT.

    :param market: экземпляр Market.
    :return: (сумма_баланса, balance_id) или (None, None) при ошибке.
    """
    try:
        resp = await asyncio.wait_for(
            market.request("GET", "/balance/exchange"),
            timeout=15,
        )
        data = resp.json()
        total, balance_id, found = 0.0, None, False
        for val in (data.get("to") or {}).values():
            if isinstance(val, dict) and val.get("balance") is not None:
                try:
                    total += float(val["balance"])
                    found = True
                    if val.get("type") == "account":
                        balance_id = val.get("balance_id")
                except (ValueError, TypeError):
                    pass
        return (total if found else None), balance_id
    except asyncio.TimeoutError:
        logger.warning("[lzt.get_balance] timeout")
        return None, None
    except Exception as e:
        logger.warning(f"[lzt.get_balance] {e}")
        return None, None


def get_country(item) -> str:
    """
    Извлекает страну из лота LZT.

    :param item: словарь с данными лота.
    :return: название страны или пустая строка.
    """
    return str(item.get("telegram_country") or item.get("country") or "")


async def fetch_code(market, item_id: int, attempts: int = 1) -> Optional[str]:
    """
    Опрашивает LZT Market на код подтверждения для лота.

    Каждый запрос ограничен таймаутом 10 секунд. При таймауте попытка
    пропускается, а не висит бесконечно.

    :param market: экземпляр Market.
    :param item_id: ID лота LZT.
    :param attempts: сколько раз опросить.
    :return: код в виде строки или None.
    """
    code_keys = {"code", "email_code", "sms_code", "otp",
                 "telegram_code", "login_code"}
    text_keys = {"message", "text", "data", "msg"}

    def walk(d, depth=8):
        if depth <= 0:
            return
        if isinstance(d, dict):
            for k, v in d.items():
                yield k, v
                if isinstance(v, (dict, list)):
                    yield from walk(v, depth - 1)
        elif isinstance(d, list):
            for x in d:
                yield from walk(x, depth - 1)

    for i in range(attempts):
        try:
            resp = await asyncio.wait_for(
                market.request(
                    "GET", f"/{item_id}/telegram-login-code"
                ),
                timeout=10,
            )
            if resp.status_code == 200:
                pairs = list(walk(resp.json()))
                for k, v in pairs:
                    if isinstance(k, str) and k.lower() in code_keys and v is not None:
                        s = str(v).strip()
                        if s.isdigit() and 4 <= len(s) <= 8:
                            return s
                for k, v in pairs:
                    if isinstance(k, str) and k.lower() in text_keys \
                            and isinstance(v, str):
                        m = re.search(r"\b(\d{5})\b", v)
                        if m:
                            return m.group(1)
            elif resp.status_code == 429:
                logger.warning("[lzt.fetch_code] 429 — too many requests")
            elif resp.status_code == 404:
                logger.warning(f"[lzt.fetch_code] 404 — лот {item_id} не найден")
        except asyncio.TimeoutError:
            logger.warning(f"[lzt.fetch_code] timeout for item {item_id} "
                           f"(attempt {i + 1}/{attempts})")
        except Exception as e:
            logger.error(f"[lzt.fetch_code] {type(e).__name__}: {e}")
        if i < attempts - 1:
            await asyncio.sleep(CODE_DELAY)
    return None


def extract_phone(text: str) -> Optional[str]:
    """
    Извлекает номер телефона из произвольного текста.

    :param text: текст для поиска.
    :return: номер в формате +XXXXXXXXXXX или None.
    """
    if not text:
        return None
    m = re.search(r"(\+\d[\d\s\-\(\)\.]{7,20})", text)
    if m:
        s = fmt_phone(m.group(1))
        if s.startswith("+") and len(s) >= 11:
            return s
    m = re.search(r"\b(\d{10,15})\b", text)
    if m:
        return fmt_phone(m.group(1))
    return None


def extract_login_data(data) -> dict:
    """
    Рекурсивно извлекает login-данные из ответа LZT.

    :param data: JSON-ответ от LZT.
    :return: плоский словарь со всеми полями.
    """
    result = {}
    root = data
    for wrapper in ("data", "item", "account", "response"):
        if isinstance(data, dict) and isinstance(data.get(wrapper), dict):
            root = data[wrapper]
            break

    def walk(d, depth=8):
        if depth <= 0 or not isinstance(d, (dict, list)):
            return
        items = d.items() if isinstance(d, dict) else enumerate(d)
        for k, v in items:
            if isinstance(k, str) and k not in result and v is not None:
                result[k] = v
            walk(v, depth - 1)

    walk(root)
    for key in ("loginData", "login_data", "json", "data"):
        v = result.get(key)
        if isinstance(v, str) and len(v) > 5:
            try:
                parsed = json.loads(v)
                if isinstance(parsed, dict):
                    for k2, v2 in parsed.items():
                        if k2 not in result and v2 is not None:
                            result[k2] = v2
            except json.JSONDecodeError:
                pass
    return result


async def buy_one(market, max_price: float, balance_id=None, balance=None,
                  skip_ids: set = None, country: str = None):
    """
    Ищет и покупает один самый дешёвый лот.

    Запрос fast-buy ограничен таймаутом 20 секунд. При таймауте лот
    пропускается и пробуется следующий.

    :param market: экземпляр Market.
    :param max_price: максимальная цена в рублях.
    :param balance_id: ID рублёвого баланса.
    :param balance: текущий баланс.
    :param skip_ids: ID лотов, которые нужно пропустить.
    :param country: фильтр по стране (или None).
    :return: (ok, item_id, phone, price, country, login_data, err).
    """
    if skip_ids is None:
        skip_ids = set()

    for page in range(1, 11):
        query = f"/telegram?pmax={max_price}&order_by=price_to_up&page={page}"
        if MIN_PRICE and MIN_PRICE > 0:
            query += f"&pmin={min(MIN_PRICE, max_price)}"
        if SPAM_FILTER:
            query += f"&spam={SPAM_FILTER}"
        try:
            resp = await asyncio.wait_for(
                market.request("GET", query),
                timeout=15,
            )
            data = resp.json()
        except asyncio.TimeoutError:
            logger.error(f"[lzt.search] page {page}: timeout")
            continue
        except Exception as e:
            logger.error(f"[lzt.search] page {page}: {e}")
            continue

        items = (data.get("items") or data.get("accounts")
                 or (data if isinstance(data, list) else []))
        if not items:
            break

        for item in items:
            item_id = item.get("item_id") or item.get("id")
            if item_id is None:
                continue
            item_id = int(item_id)
            if item_id in skip_ids:
                continue

            item_country = get_country(item)
            if country and country.lower().strip() not in item_country.lower():
                continue

            price = float(item.get("price") or 0)
            price_total = float(item.get("priceWithSellerFee") or price * 1.03)

            if price > max_price:
                continue
            if balance is not None and balance < price_total:
                return False, None, None, None, None, {}, "no_money"

            payload = {"price": price}
            if balance_id is not None:
                payload["balance_id"] = balance_id

            try:
                buy_resp = await asyncio.wait_for(
                    market.request(
                        "POST", f"/{item_id}/fast-buy", json=payload
                    ),
                    timeout=20,
                )
                buy_data = {}
                try:
                    buy_data = buy_resp.json()
                except Exception:
                    pass

                if buy_resp.status_code in (200, 201):
                    logger.info(f"[lzt.buy] ✅ куплен лот {item_id} за {price}₽")
                    login_data = extract_login_data(buy_data)
                    phone = None
                    for key in ("phone", "phone_number", "number", "telegram_phone"):
                        if login_data.get(key):
                            phone = fmt_phone(str(login_data[key]).strip())
                            break
                    if not phone:
                        for field in (str(item.get("title") or ""),
                                      str(item.get("description") or "")):
                            phone = extract_phone(field)
                            if phone:
                                break
                    if not phone and login_data:
                        phone = extract_phone(
                            json.dumps(login_data, ensure_ascii=False))
                    return True, item_id, phone, price, item_country, login_data, None

                logger.warning(f"[lzt.buy] HTTP {buy_resp.status_code} "
                               f"лот {item_id}: {str(buy_data)[:150]}")
                err_txt = str(buy_data.get("errors")
                              if isinstance(buy_data, dict) else buy_data).lower()
                if "средств" in err_txt or "insufficient" in err_txt:
                    return False, None, None, None, None, {}, "no_money"
                if buy_resp.status_code == 429:
                    await asyncio.sleep(3)
                    continue
                skip_ids.add(item_id)
                continue
            except asyncio.TimeoutError:
                logger.warning(f"[lzt.buy] {item_id}: fast-buy timeout")
                skip_ids.add(item_id)
                continue
            except Exception as e:
                logger.error(f"[lzt.buy] {item_id}: {type(e).__name__}: {e}")
                skip_ids.add(item_id)
                continue

    return False, None, None, None, None, {}, "no_lots"


async def get_item_login_data(market, item_id: int) -> dict:
    """
    Получает свежие данные лота (номер, 2FA-пароль).

    :param market: экземпляр Market.
    :param item_id: ID лота.
    :return: плоский словарь с данными или пустой словарь.
    """
    try:
        resp = await asyncio.wait_for(
            market.request("GET", f"/{item_id}"),
            timeout=15,
        )
        return extract_login_data(resp.json())
    except asyncio.TimeoutError:
        logger.warning(f"[lzt.item] {item_id}: timeout")
        return {}
    except Exception as e:
        logger.warning(f"[lzt.item] {item_id}: {e}")
        return {}


async def list_purchases(market) -> list:
    """
    Возвращает список всех купленных лотов (все страницы).

    :param market: экземпляр Market.
    :return: список словарей-лотов.
    """
    result = []
    for page in range(1, 21):
        try:
            resp = await asyncio.wait_for(
                market.request("GET", f"/user/items?page={page}"),
                timeout=15,
            )
            data = resp.json()
        except asyncio.TimeoutError:
            logger.error(f"[lzt.purchases] page {page}: timeout")
            break
        except Exception as e:
            logger.error(f"[lzt.purchases] page {page}: {e}")
            break

        items = None
        if isinstance(data, dict):
            for key in ("items", "data", "purchases", "accounts"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
        elif isinstance(data, list):
            items = data
        if not items:
            break
        result.extend(items)
    return result


def is_telegram_item(item: dict) -> bool:
    """
    Проверяет, относится ли лот к категории Telegram.

    :param item: словарь-лот.
    :return: True, если это Telegram-лот.
    """
    for key in ("category", "category_id", "item_origin", "title", "item_type"):
        v = item.get(key)
        if v is not None and "telegram" in str(v).lower():
            return True
    return bool(item.get("telegram_id") or item.get("telegram_country"))


async def set_random_avatar(client) -> Tuple[bool, str]:
    """
    Устанавливает случайную аватарку из папки avatars/.

    :param client: активный TelegramClient.
    :return: (успех, информация).
    """
    if not os.path.isdir(AVATAR_DIR):
        return False, "no_dir"
    files = [f for f in os.listdir(AVATAR_DIR)
             if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    if not files:
        return False, "no_avatars"
    path = os.path.join(AVATAR_DIR, random.choice(files))
    try:
        await client(UploadProfilePhotoRequest(
            file=await client.upload_file(path)
        ))
        return True, path
    except errors.FloodWaitError as e:
        return False, f"flood_{e.seconds}"
    except Exception as e:
        return False, f"error:{str(e)[:80]}"


async def login_to_account(phone: str, item_id: int, market,
                           password: Optional[str] = None,
                           ctx_cancel=None):
    """
    Заходит на купленный аккаунт по номеру телефона.

    Отправляет код, ждёт появления кода на LZT (fetch_code), вводит
    его в Telegram. Если нужен 2FA — использует password, либо
    повторно берёт свежий из лота. После входа устанавливает
    случайную аватарку.

    Каждый запрос кода ограничен таймаутом 15 секунд — если LZT
    тормозит, попытка пропускается.

    Если передан ctx_cancel (callable без аргументов, возвращающий
    bool) — проверяется перед каждым шагом. Если вернёт True,
    функция сразу выходит со статусом "cancelled".

    Возможные статусы:
      - ("1BVts...", "ok") — успех
      - (None, "cancelled") — отменено пользователем
      - (None, "needs_2fa") — нужен 2FA-пароль
      - (None, "no_code_from_market") — LZT не отдал код
      - (None, "invalid_code") — код не подошёл
      - (None, "send_unavailable") — Telegram не даёт запросить код
      - (None, "phone_banned") — номер забанен
      - (None, "phone_invalid") — неверный номер
      - (None, "phone_flood") — флуд на номер
      - (None, "flood_N") — FloodWait на N секунд
      - (None, "timeout") — таймаут
      - (None, "error:...") — прочие ошибки

    :param phone: номер телефона.
    :param item_id: ID лота LZT.
    :param market: экземпляр Market.
    :param password: пароль 2FA (если известен).
    :param ctx_cancel: callable, возвращающий True для отмены.
    :return: (session_string, status).
    """
    phone = fmt_phone(phone)
    client = make_client()
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
    except Exception as e:
        logger.error(f"[lzt.login] {phone}: connect: {type(e).__name__}: {e}")
        await _safe_disconnect(client)
        return None, f"error:connect_{type(e).__name__}"

    current_hash = None
    tried = set()
    signed = False

    def _is_cancelled():
        try:
            return bool(ctx_cancel and ctx_cancel())
        except Exception:
            return False

    try:
        if _is_cancelled():
            return None, "cancelled"

        try:
            sent = await asyncio.wait_for(
                client.send_code_request(phone), timeout=15)
            current_hash = sent.phone_code_hash
            logger.info(f"[lzt.login] {phone}: код запрошен")
        except errors.SendCodeUnavailableError as e:
            logger.warning(f"[lzt.login] {phone}: SendCodeUnavailable: {e}")
            return None, "send_unavailable"
        except errors.PhoneNumberBannedError:
            logger.warning(f"[lzt.login] {phone}: номер забанен")
            return None, "phone_banned"
        except errors.PhoneNumberInvalidError:
            logger.warning(f"[lzt.login] {phone}: неверный номер")
            return None, "phone_invalid"
        except errors.PhoneNumberFloodError:
            logger.warning(f"[lzt.login] {phone}: PhoneNumberFlood")
            return None, "phone_flood"
        except errors.FloodWaitError as e:
            logger.warning(f"[lzt.login] {phone}: FloodWait {e.seconds}с")
            return None, f"flood_{e.seconds}"
        except asyncio.TimeoutError:
            logger.warning(f"[lzt.login] {phone}: timeout на send_code")
            return None, "timeout"

        for round_no in range(CODE_ROUNDS):
            if _is_cancelled():
                return None, "cancelled"

            if round_no > 0:
                logger.info(f"[lzt.login] {phone}: повторный запрос кода "
                            f"({round_no + 1}/{CODE_ROUNDS})")
                try:
                    sent = await asyncio.wait_for(
                        client.send_code_request(phone), timeout=15)
                    current_hash = sent.phone_code_hash
                except errors.SendCodeUnavailableError:
                    logger.warning(
                        f"[lzt.login] {phone}: SendCodeUnavailable, "
                        f"оставляю прежний phone_code_hash")
                except errors.PhoneNumberFloodError:
                    logger.warning(f"[lzt.login] {phone}: PhoneNumberFlood, "
                                   f"оставляю прежний hash")
                except errors.FloodWaitError as e:
                    logger.warning(f"[lzt.login] {phone}: FloodWait {e.seconds}с")
                    await asyncio.sleep(min(e.seconds, 60))
                except errors.PhoneNumberBannedError:
                    logger.warning(f"[lzt.login] {phone}: номер забанен")
                    return None, "phone_banned"
                except Exception as e:
                    logger.warning(
                        f"[lzt.login] {phone}: повторный запрос не удался "
                        f"({type(e).__name__}), использую прежний hash")

            for attempt in range(CODE_ATTEMPTS):
                if _is_cancelled():
                    return None, "cancelled"

                await asyncio.sleep(CODE_DELAY)

                try:
                    code = await asyncio.wait_for(
                        fetch_code(market, item_id),
                        timeout=15,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[lzt.login] {phone}: fetch_code timeout "
                        f"(попытка {attempt + 1}/{CODE_ATTEMPTS})"
                    )
                    continue

                if not code:
                    continue
                if code in tried:
                    continue
                tried.add(code)
                try:
                    await asyncio.wait_for(
                        client.sign_in(phone, code,
                                       phone_code_hash=current_hash),
                        timeout=15)
                    signed = True
                    logger.info(f"[lzt.login] {phone}: вход выполнен")
                    break
                except errors.PhoneCodeInvalidError:
                    logger.info(f"[lzt.login] {phone}: код {code} не подошёл")
                    continue
                except errors.PhoneCodeExpiredError:
                    logger.warning(f"[lzt.login] {phone}: код истёк")
                    break
                except errors.SessionPasswordNeededError:
                    if not password:
                        logger.info(f"[lzt.login] {phone}: нужен 2FA-пароль")
                        return None, "needs_2fa"
                    try:
                        await asyncio.wait_for(
                            client.sign_in(password=password), timeout=15)
                        signed = True
                        logger.info(f"[lzt.login] {phone}: 2FA пройдена")
                        break
                    except errors.PasswordHashInvalidError:
                        logger.warning(f"[lzt.login] {phone}: неверный 2FA, "
                                       f"пробую получить свежий из лота")
                        fresh = await get_item_login_data(market, item_id)
                        fresh_pw = None
                        for key in ("telegram_password", "password", "twofa",
                                    "2fa", "two_fa", "cloud_password"):
                            if fresh.get(key):
                                fresh_pw = str(fresh[key])
                                break
                        if fresh_pw and fresh_pw != password:
                            try:
                                await asyncio.wait_for(
                                    client.sign_in(password=fresh_pw),
                                    timeout=15)
                                signed = True
                                password = fresh_pw
                                logger.info(f"[lzt.login] {phone}: 2FA "
                                            f"пройдена со свежим паролем")
                                break
                            except errors.PasswordHashInvalidError:
                                return None, "bad_2fa_password"
                        return None, "bad_2fa_password"
                    except errors.FloodWaitError as e:
                        logger.warning(f"[lzt.login] {phone}: FloodWait "
                                       f"на 2FA: {e.seconds}с")
                        await asyncio.sleep(min(e.seconds, 60))
                        return None, f"flood_{e.seconds}"
                except errors.FloodWaitError as e:
                    logger.warning(f"[lzt.login] {phone}: FloodWait "
                                   f"на sign_in: {e.seconds}с")
                    await asyncio.sleep(min(e.seconds, 60))
                    return None, f"flood_{e.seconds}"
                except Exception as e:
                    logger.error(f"[lzt.login] {phone}: sign_in: "
                                 f"{type(e).__name__}: {e}")
                    continue

            if signed:
                break

        if not signed:
            if _is_cancelled():
                return None, "cancelled"
            status = "invalid_code" if tried else "no_code_from_market"
            logger.warning(f"[lzt.login] {phone}: не удалось войти — {status}")
            return None, status

        try:
            session_str = client.session.save()
        except Exception as e:
            logger.error(f"[lzt.login] {phone}: session.save: "
                         f"{type(e).__name__}: {e}")
            return None, "error:session_save"

        if not session_str:
            return None, "error:empty_session"

        try:
            ok_ava, ava_info = await set_random_avatar(client)
            if ok_ava:
                logger.info(f"[lzt.login] {phone}: аватарка установлена")
            else:
                logger.info(f"[lzt.login] {phone}: аватарка не установлена: "
                            f"{ava_info}")
        except Exception as e:
            logger.warning(f"[lzt.login] {phone}: avatar: "
                           f"{type(e).__name__}: {e}")

        return session_str, "ok"

    except asyncio.TimeoutError:
        logger.warning(f"[lzt.login] {phone}: общий таймаут")
        return None, "timeout"
    except errors.PhoneNumberBannedError:
        return None, "phone_banned"
    except errors.PhoneNumberInvalidError:
        return None, "phone_invalid"
    except errors.FloodWaitError as e:
        return None, f"flood_{e.seconds}"
    except Exception as e:
        logger.error(f"[lzt.login] {phone}: {type(e).__name__}: {e}")
        return None, f"error:{type(e).__name__}"
    finally:
        await _safe_disconnect(client)


async def retry_login_to_account(phone: str, item_id: int, market,
                                 password: Optional[str] = None,
                                 ctx_cancel=None,
                                 max_attempts: int = 2):
    """
    Обёртка над login_to_account с автоматическим повтором.

    Если первая попытка вернула "timeout", "no_code_from_market"
    или "invalid_code" — пробует ещё раз (до max_attempts раз).
    Между попытками пауза 5 секунд, чтобы LZT успел прислать код.

    Для невосстановимых статусов (needs_2fa, bad_2fa_password,
    phone_banned и т.п.) повтор не делается — сразу возврат.

    Если все попытки провалились — возвращает последний статус.

    :param phone: номер телефона.
    :param item_id: ID лота LZT.
    :param market: экземпляр Market.
    :param password: пароль 2FA (если известен).
    :param ctx_cancel: callable, возвращающий True для отмены.
    :param max_attempts: сколько всего попыток (включая первую).
    :return: (session_string, status).
    """
    retryable = {"timeout", "no_code_from_market", "invalid_code"}
    last_status = None

    for attempt in range(1, max_attempts + 1):
        try:
            if ctx_cancel and ctx_cancel():
                return None, "cancelled"
        except Exception:
            pass

        if attempt > 1:
            logger.info(
                f"[lzt.retry] {phone}: повторная попытка "
                f"{attempt}/{max_attempts} (item={item_id})"
            )
            await asyncio.sleep(5)

        session_str, status = await login_to_account(
            phone, item_id, market,
            password=password,
            ctx_cancel=ctx_cancel,
        )

        if session_str:
            return session_str, "ok"

        if status == "cancelled":
            return None, "cancelled"

        last_status = status

        if status not in retryable:
            return None, status

    return None, last_status or "timeout"