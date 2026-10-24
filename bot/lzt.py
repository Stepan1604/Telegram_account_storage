import asyncio
import json
import logging
import re
from typing import Optional, Tuple

from telethon import errors

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


# Эндпоинт получения кода входа Telegram (из исходника LOLZTEAM):
#   GET https://api.lzt.market/{item_id}/telegram-login-code
TELEGRAM_CODE_PATH = "/{item_id}/telegram-login-code"


# Если на сайте LZT лоты с отрицательным telegram_spam_block помечены
# как спамблок, поставь True.
SPAM_NEGATIVE_IS_BLOCK = False

SPAM_BLOCK_KEYS = ("telegram_spam_block", "spam_block", "has_spam_block")


def _spam_block_value(item: dict):
    """Возвращает (ключ, значение) первого найденного поля спамблока."""
    for key in SPAM_BLOCK_KEYS:
        if key in item and item[key] is not None:
            return key, item[key]
    return None, None


def _is_spam_blocked(item: dict) -> bool:
    """
    Определяет, есть ли у лота спамблок.

    True / "true" / "1" / "yes" / "y"  → спамблок
    число > 0                          → спамблок
    число < 0                          → спамблок только если
                                         SPAM_NEGATIVE_IS_BLOCK = True
    0, None, поля нет                  → спамблока нет
    """
    _, v = _spam_block_value(item)
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if v > 0:
            return True
        if v < 0:
            return SPAM_NEGATIVE_IS_BLOCK
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y")
    return False


async def _fetch_item_card(market, item_id: int) -> Optional[dict]:
    """Загружает карточку лота: GET /{item_id}. None при ошибке."""
    try:
        resp = await asyncio.wait_for(
            market.request("GET", f"/{item_id}"),
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"[lzt.card] {item_id}: HTTP {resp.status_code}")
            return None
        data = resp.json()
    except asyncio.TimeoutError:
        logger.warning(f"[lzt.card] {item_id}: timeout")
        return None
    except Exception as e:
        logger.warning(f"[lzt.card] {item_id}: {type(e).__name__}: {e}")
        return None

    card = data.get("item", data) if isinstance(data, dict) else {}
    key, v = _spam_block_value(card)
    logger.info(f"[lzt.card] {item_id}: {key}={v!r} "
                f"→ спамблок={_is_spam_blocked(card)}")
    return card


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


def make_market():
    if not LZT_AVAILABLE:
        raise RuntimeError("LOLZTEAM не установлен: pip install LOLZTEAM")
    if not LZT_API_TOKEN:
        raise RuntimeError("LZT_API_TOKEN пуст в config.py")
    m = Market(token=LZT_API_TOKEN)
    if LZT_PROXY:
        try:
            m.settings.proxy = LZT_PROXY
        except Exception:
            pass
    return m


async def _raw_get_json(path: str, timeout: int = 45):
    """
    GET к LZT напрямую через curl_cffi, в обход разбора библиотеки.

    Библиотека LOLZTEAM сама разбирает JSON внутри request() и при
    пустом или HTML-ответе бросает исключение, не отдавая тело ответа.
    Здесь тело доступно всегда.

    :return: (data, status, body). data = None, если ответ не JSON или
        статус не 200; body — первые 200 символов ответа для лога.
    """
    from curl_cffi import requests as cr

    kwargs = {
        "headers": {"Authorization": f"Bearer {LZT_API_TOKEN}"},
        "timeout": timeout,
    }
    if LZT_PROXY:
        kwargs["proxy"] = LZT_PROXY

    loop = asyncio.get_running_loop()
    resp = await loop.run_in_executor(
        None, lambda: cr.get(f"https://api.lzt.market{path}", **kwargs))

    body = (resp.text or "")[:200]
    if resp.status_code != 200:
        return None, resp.status_code, body
    try:
        return resp.json(), 200, body
    except Exception:
        return None, 200, body


async def get_balance(market) -> Tuple[Optional[float], Optional[int]]:
    try:
        data, status, body = await asyncio.wait_for(
            _raw_get_json("/balance/exchange"), timeout=60)
        if data is None:
            logger.warning(
                f"[lzt.get_balance] HTTP {status}, нет JSON: {body!r}")
            return None, None

        balances = []
        for val in (data.get("to") or {}).values():
            if isinstance(val, dict) and val.get("balance") is not None:
                try:
                    b = float(val["balance"])
                    balances.append({
                        "balance": b,
                        "balance_id": val.get("balance_id"),
                        "type": val.get("type"),
                    })
                except (ValueError, TypeError):
                    pass

        if not balances:
            logger.warning("[lzt.get_balance] список балансов пуст")
            return None, None

        for b in balances:
            if b["type"] == "account" and b["balance_id"] is not None:
                logger.info(
                    f"[lzt.get_balance] ✅ выбран счёт для покупок: "
                    f"id={b['balance_id']} balance={b['balance']}₽"
                )
                return b["balance"], b["balance_id"]

        balances.sort(key=lambda x: x["balance"], reverse=True)
        best = balances[0]
        logger.warning(
            f"[lzt.get_balance] ⚠️ счёт type='account' не найден, "
            f"использую max balance: id={best['balance_id']} "
            f"type={best['type']} balance={best['balance']}₽"
        )
        return best["balance"], best["balance_id"]

    except asyncio.TimeoutError:
        logger.warning("[lzt.get_balance] timeout")
        return None, None
    except Exception as e:
        logger.warning(f"[lzt.get_balance] {type(e).__name__}: {e}")
        return None, None


async def get_balance_with_retry(market, attempts: int = 3, delay: int = 5):
    """
    Запрашивает счёт для покупок с повторами.

    Возвращает (balance, balance_id). Если счёт так и не определён,
    возвращает (None, None) — покупку нужно останавливать, а не
    платить с основного баланса.
    """
    for i in range(1, attempts + 1):
        balance, balance_id = await get_balance(market)
        if balance_id is not None:
            return balance, balance_id
        logger.warning(
            f"[lzt.get_balance] попытка {i}/{attempts}: счёт не определён")
        if i < attempts:
            await asyncio.sleep(delay)
    return None, None


def get_country(item) -> str:
    return str(item.get("telegram_country") or item.get("country") or "")


def extract_phone(text: str) -> Optional[str]:
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


def _walk_json(d, depth=8):
    if depth <= 0:
        return
    if isinstance(d, dict):
        for k, v in d.items():
            yield k, v
            if isinstance(v, (dict, list)):
                yield from _walk_json(v, depth - 1)
    elif isinstance(d, list):
        for x in d:
            yield from _walk_json(x, depth - 1)


def _normalize_response(raw):
    """Приводит ответ к dict/list, распаковывая строки и bytes."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return raw
    return raw


def _extract_code_from_json(raw) -> Optional[str]:
    """Ищет код подтверждения в произвольном JSON-ответе LZT."""
    raw = _normalize_response(raw)
    if not raw:
        return None

    code_keys = {
        "code", "telegram_code", "email_code", "sms_code", "otp",
        "login_code", "confirmation_code", "verification_code",
        "tg_code", "auth_code",
    }
    text_keys = {"message", "text", "data", "msg", "error",
                 "description", "result"}

    pairs = list(_walk_json(raw))

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

    return None


def _code_from_response_body(resp) -> Optional[str]:
    """
    Достаёт код подтверждения из ответа LZT.
    Сначала JSON через _extract_code_from_json, затем чистая строка из цифр.
    """
    try:
        raw = resp.json()
    except Exception:
        raw = (resp.text or "").strip()

    code = _extract_code_from_json(raw)
    if code:
        return code

    if isinstance(raw, str) and raw.isdigit() and 4 <= len(raw) <= 8:
        return raw
    return None


async def fetch_code(market, item_id: int, attempts: int = 1) -> Optional[str]:
    """
    Получает код входа Telegram для купленного лота одним GET-запросом.
    При 429 делает паузу; при 200 без кода логирует тело ответа.
    """
    path = TELEGRAM_CODE_PATH.format(item_id=item_id)

    for i in range(attempts):
        try:
            resp = await asyncio.wait_for(
                market.request("GET", path),
                timeout=20,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[lzt.fetch_code] {path}: timeout "
                           f"(попытка {i + 1}/{attempts})")
        except Exception as e:
            logger.warning(f"[lzt.fetch_code] {path}: "
                           f"{type(e).__name__}: {e}")
        else:
            status = resp.status_code
            if status in (200, 201):
                code = _code_from_response_body(resp)
                if code:
                    logger.info(f"[lzt.fetch_code] ✅ код получен для {item_id}")
                    return code
                logger.info(f"[lzt.fetch_code] {status} без кода: "
                            f"{(resp.text or '')[:300]}")
            else:
                logger.info(f"[lzt.fetch_code] HTTP {status}: "
                            f"{(resp.text or '')[:200]}")
                if status == 429:
                    await asyncio.sleep(CODE_DELAY * 2)

        if i < attempts - 1:
            await asyncio.sleep(CODE_DELAY)

    logger.warning(f"[lzt.fetch_code] не удалось получить код для item {item_id}")
    return None


def _mail_text(msg) -> str:
    """Собирает текст письма (plain и html) для поиска кода."""
    parts = []
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
    return "\n".join(parts)


def _fetch_login_code_from_mail(length: int, started_at: float,
                                timeout: int = 120) -> Optional[str]:
    """
    Ждёт письмо с кодом от Telegram в ящике LOGIN_EMAIL по IMAP.

    Блокирующая функция: вызывается через asyncio.to_thread.
    Учитываются только письма, пришедшие после started_at.

    :param length: длина кода (из ответа Telegram на запрос кода).
    :param started_at: время запроса кода (time.time()).
    :param timeout: сколько секунд ждать письмо.
    :return: код строкой или None.
    """
    import imaplib
    import email as email_lib
    import time
    from email.utils import parsedate_to_datetime
    import config as cfg

    host = getattr(cfg, "IMAP_HOST", "")
    user = getattr(cfg, "IMAP_USER", "")
    password = getattr(cfg, "IMAP_PASSWORD", "")
    if not (host and user and password):
        logger.error("[lzt.mail] IMAP_HOST / IMAP_USER / IMAP_PASSWORD "
                     "не заданы в config.py")
        return None

    pattern = re.compile(rf"\b(\d{{{length}}})\b")
    deadline = time.time() + timeout

    while time.time() < deadline:
        imap = None
        try:
            imap = imaplib.IMAP4_SSL(host)
            imap.login(user, password)
            imap.select("INBOX")
            _, data = imap.search(None, '(FROM "telegram")')
            ids = (data[0] or b"").split()[-10:]
            for mid in reversed(ids):
                _, msg_data = imap.fetch(mid, "(RFC822)")
                msg = email_lib.message_from_bytes(msg_data[0][1])
                try:
                    msg_time = parsedate_to_datetime(msg["Date"]).timestamp()
                except Exception:
                    continue
                if msg_time < started_at - 5:
                    continue
                m = pattern.search(_mail_text(msg))
                if m:
                    return m.group(1)
        except Exception as e:
            logger.warning(f"[lzt.mail] IMAP: {type(e).__name__}: {e}")
        finally:
            if imap is not None:
                try:
                    imap.logout()
                except Exception:
                    pass
        time.sleep(5)

    logger.warning("[lzt.mail] письмо с кодом не пришло за отведённое время")
    return None


async def bind_login_email(phone: str, session_str: str):
    """
    Привязывает LOGIN_EMAIL к купленному аккаунту.

    Запрашивает код на почту, читает его из ящика LOGIN_EMAIL по IMAP
    и подтверждает. Ошибка НЕ отменяет покупку: возвращается статус.

    :param phone: номер телефона.
    :param session_str: текущая session аккаунта.
    :return: (ok, msg, session_str). session_str может обновиться.
    """
    import time
    import config as cfg
    from .telethon_ops import (
        _get_client, close_client, request_email_code, confirm_email_code,
    )

    login_email = getattr(cfg, "LOGIN_EMAIL", "")
    if not login_email:
        return False, "LOGIN_EMAIL не задан", session_str

    phone = fmt_phone(phone)
    try:
        client = await _get_client(phone, session_str)
    except Exception as e:
        logger.error(f"[lzt.email] {phone}: connect: {type(e).__name__}: {e}")
        return False, f"connect: {type(e).__name__}", session_str

    try:
        started = time.time()
        ok, msg, _, length = await request_email_code(
            client, phone, login_email)
        if not ok:
            logger.warning(f"[lzt.email] {phone}: запрос кода: {msg}")
            return False, msg, session_str

        code = await asyncio.to_thread(
            _fetch_login_code_from_mail, length, started)
        if not code:
            return False, "код из почты не получен", session_str

        ok, msg, _ = await confirm_email_code(client, phone, None, code)
        if not ok:
            logger.warning(f"[lzt.email] {phone}: подтверждение: {msg}")
            return False, msg, session_str

        logger.info(f"[lzt.email] {phone}: почта {login_email} привязана")
        try:
            session_str = client.session.save() or session_str
        except Exception:
            pass
        return True, "OK", session_str
    except Exception as e:
        logger.error(f"[lzt.email] {phone}: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:100]}", session_str
    finally:
        try:
            await close_client(phone)
        except Exception:
            pass


async def request_bind_email(phone: str, session_str: str):
    """
    Шаг 1: запрашивает код для привязки LOGIN_EMAIL.

    Сначала пробует заменить уже привязанную почту. Если у аккаунта
    почты ещё нет, переходит к установке новой — это дополнительно
    отправит служебный код входа в сам аккаунт (см. telethon_ops).

    :return: (ok, msg, ctx, length). ctx нужно передать как есть
        в confirm_bind_email.
    """
    import config as cfg
    from .telethon_ops import _get_client, request_email_code

    login_email = getattr(cfg, "LOGIN_EMAIL", "")
    if not login_email:
        return False, "LOGIN_EMAIL не задан", None, 0

    phone = fmt_phone(phone)
    try:
        client = await _get_client(phone, session_str)
        ok, msg, ctx, length = await request_email_code(
            client, phone, login_email)
        return ok, msg, ctx, length
    except Exception as e:
        logger.error(f"[lzt.email] {phone}: запрос кода: "
                     f"{type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:100]}", None, 0


async def confirm_bind_email(phone: str, session_str: str, ctx, code: str):
    """
    Шаг 2: подтверждает код, который пользователь ввёл в чат.

    :param ctx: то, что вернул request_bind_email.
    :return: (ok, msg, session_str) — session_str обновляется после успеха.
    """
    from .telethon_ops import _get_client, close_client, confirm_email_code

    phone = fmt_phone(phone)
    try:
        client = await _get_client(phone, session_str)
        ok, msg, _ = await confirm_email_code(client, phone, ctx, code)
        new_session = session_str
        if ok:
            try:
                new_session = client.session.save() or session_str
            except Exception:
                pass
        return ok, msg, new_session
    except Exception as e:
        logger.error(f"[lzt.email] {phone}: подтверждение: "
                     f"{type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {str(e)[:100]}", session_str
    finally:
        try:
            await close_client(phone)
        except Exception:
            pass


async def buy_item(market, item_id: int, price: float, balance_id=None):
    try:
        payload = {"price": price}
        if balance_id is not None:
            payload["balance_id"] = balance_id

        resp = await asyncio.wait_for(
            market.request(
                "POST", f"/{item_id}/fast-buy", json=payload
            ),
            timeout=20,
        )
        if resp.status_code == 404:
            return False, None, None, None, {}, "not_found"
        if resp.status_code not in (200, 201):
            text = ""
            try:
                text = resp.text[:150]
            except Exception:
                pass
            return (False, None, None, None, {},
                    f"http_{resp.status_code}: {text}")

        data = resp.json()
        login_data = extract_login_data(data)
        item_data = data.get("item", {}) if isinstance(data, dict) else {}

        real_price = float(item_data.get("price") or price or 0)
        country = get_country(item_data) or get_country(login_data)

        phone = None
        for key in ("phone", "phone_number", "number", "telegram_phone"):
            if login_data.get(key):
                phone = fmt_phone(str(login_data[key]).strip())
                break
        if not phone:
            phone = extract_phone(
                json.dumps(login_data, ensure_ascii=False)
            )

        return True, phone, real_price, country, login_data, None

    except asyncio.TimeoutError:
        return False, None, None, None, {}, "timeout"
    except Exception as e:
        logger.error(f"[lzt.buy_item] {item_id}: {type(e).__name__}: {e}")
        return False, None, None, None, {}, f"{type(e).__name__}: {e}"


async def buy_one(market, max_price: float, balance_id=None,
                  skip_ids: set = None, max_pages: int = 50):
    if skip_ids is None:
        skip_ids = set()

    spam_skipped = 0
    price_skipped = 0
    pages_scanned = 0
    empty_pages = 0
    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 5

    for page in range(1, max_pages + 1):
        pages_scanned = page
        query = f"/telegram?pmax={max_price}&order_by=price_to_up&page={page}"
        if MIN_PRICE and MIN_PRICE > 0:
            query += f"&pmin={min(MIN_PRICE, max_price)}"
        if SPAM_FILTER:
            query += f"&spam={SPAM_FILTER}"

        try:
            resp = await asyncio.wait_for(
                market.request("GET", query),
                timeout=20,
            )
            data = resp.json()
        except asyncio.TimeoutError:
            logger.warning(f"[lzt.search] page {page}: timeout")
            continue
        except Exception as e:
            logger.warning(f"[lzt.search] page {page}: {e}")
            continue

        items = (data.get("items") or data.get("accounts")
                 or (data if isinstance(data, list) else []))
        if not items:
            empty_pages += 1
            if empty_pages >= 3:
                break
            continue
        empty_pages = 0

        for item in items:
            item_id = item.get("item_id") or item.get("id")
            if item_id is None:
                continue
            item_id = int(item_id)
            if item_id in skip_ids:
                continue

            # Быстрый отсев по данным из поиска
            if _is_spam_blocked(item):
                spam_skipped += 1
                skip_ids.add(item_id)
                continue

            # Главная проверка: карточка лота прямо перед покупкой
            card = await _fetch_item_card(market, item_id)
            if card is None:
                # Не смогли проверить — лучше пропустить
                skip_ids.add(item_id)
                await asyncio.sleep(1)
                continue
            if _is_spam_blocked(card):
                spam_skipped += 1
                skip_ids.add(item_id)
                continue

            price = float(item.get("price") or 0)
            if price > max_price:
                price_skipped += 1
                continue

            item_country = get_country(item)

            ok, phone, real_price, country, login_data, err = \
                await buy_item(market, item_id, price,
                               balance_id=balance_id)

            if not ok:
                skip_ids.add(item_id)
                consecutive_errors += 1

                if err == "not_found":
                    logger.debug(f"[lzt.buy] {item_id}: 404 (уже куплен)")
                else:
                    logger.warning(
                        f"[lzt.buy] {item_id}: {err} "
                        f"(подряд ошибок: {consecutive_errors})"
                    )

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        f"[lzt.buy] {MAX_CONSECUTIVE_ERRORS} ошибок подряд — "
                        f"выхожу из покупки. Последняя: {err}"
                    )
                    return False, None, None, None, None, {}, "buy_failed"

                await asyncio.sleep(2)
                continue

            consecutive_errors = 0

            logger.info(
                f"[lzt.buy] ✅ куплен лот {item_id} за {real_price}₽ "
                f"(page={page}, spam_skipped={spam_skipped}, "
                f"balance_id={balance_id})"
            )
            return (True, item_id, phone, real_price,
                    country or item_country, login_data, None)

    logger.warning(
        f"[lzt.buy] после {pages_scanned} страниц: "
        f"spam={spam_skipped}, price_skipped={price_skipped}, "
        f"empty_pages={empty_pages}"
    )

    if spam_skipped > 0 and price_skipped == 0:
        return False, None, None, None, None, {}, "only_spam"

    return False, None, None, None, None, {}, "no_lots"


async def get_item_login_data(market, item_id: int) -> dict:
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
    for key in ("category", "category_id", "item_origin", "title",
                "item_type"):
        v = item.get(key)
        if v is not None and "telegram" in str(v).lower():
            return True
    return bool(item.get("telegram_id") or item.get("telegram_country"))


async def login_to_account(phone: str, item_id: int, market,
                           password: Optional[str] = None,
                           ctx_cancel=None):
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
        except errors.AuthRestartError as e:
            logger.warning(f"[lzt.login] {phone}: AuthRestartError: {e}")
            return None, "auth_restart"
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
                except errors.AuthRestartError:
                    logger.warning(f"[lzt.login] {phone}: AuthRestartError "
                                   f"на повторном запросе")
                    return None, "auth_restart"
                except errors.SendCodeUnavailableError:
                    logger.warning(
                        f"[lzt.login] {phone}: SendCodeUnavailable, "
                        f"оставляю прежний phone_code_hash")
                except errors.PhoneNumberFloodError:
                    logger.warning(f"[lzt.login] {phone}: PhoneNumberFlood, "
                                   f"оставляю прежний hash")
                except errors.FloodWaitError as e:
                    logger.warning(
                        f"[lzt.login] {phone}: FloodWait {e.seconds}с"
                    )
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
                        timeout=20,
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
                    logger.info(
                        f"[lzt.login] {phone}: код {code} не подошёл"
                    )
                    continue
                except errors.PhoneCodeExpiredError:
                    logger.warning(f"[lzt.login] {phone}: код истёк")
                    break
                except errors.SessionPasswordNeededError:
                    from config import PASSWORD_2FA as _pw2fa
                    candidates = []
                    for pw in (password, _pw2fa):
                        if pw and pw not in candidates:
                            candidates.append(pw)
                    if not candidates:
                        logger.info(f"[lzt.login] {phone}: нужен 2FA-пароль")
                        return None, "needs_2fa"

                    found = None
                    for pw in candidates:
                        try:
                            await asyncio.wait_for(
                                client.sign_in(password=pw), timeout=15)
                            found = pw
                            break
                        except errors.PasswordHashInvalidError:
                            logger.warning(
                                f"[lzt.login] {phone}: пароль не подошёл, "
                                f"пробую следующий")
                    if found is None:
                        return None, "bad_2fa_password"

                    signed = True
                    password = found
                    logger.info(f"[lzt.login] {phone}: 2FA пройдена")
                    break
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
            logger.warning(
                f"[lzt.login] {phone}: не удалось войти — {status}"
            )
            return None, status

        try:
            session_str = client.session.save()
        except Exception as e:
            logger.error(f"[lzt.login] {phone}: session.save: "
                         f"{type(e).__name__}: {e}")
            return None, "error:session_save"

        if not session_str:
            return None, "error:empty_session"

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
    retryable = {"timeout", "no_code_from_market", "invalid_code",
                 "auth_restart"}
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


async def apply_password_2fa_after_login(phone: str, session_str: str,
                                          current_password: str = "") -> tuple:
    from config import PASSWORD_2FA
    from .telethon_ops import _get_client, close_client, set_2fa_password

    if not PASSWORD_2FA:
        return session_str, current_password, True, "PASSWORD_2FA не задан"

    if current_password == PASSWORD_2FA:
        return session_str, PASSWORD_2FA, True, "Уже PASSWORD_2FA"

    try:
        client = await _get_client(phone, session_str)
    except Exception as e:
        logger.error(f"[2fa] {phone}: connect: {type(e).__name__}: {e}")
        return session_str, current_password, False, f"connect: {e}"

    try:
        if current_password:
            ok, msg = await set_2fa_password(
                client, PASSWORD_2FA, current_password=current_password,
            )
            if not ok and current_password != PASSWORD_2FA:
                # Вход мог пройти по PASSWORD_2FA, а не по паролю из лота
                ok, msg = await set_2fa_password(
                    client, PASSWORD_2FA, current_password=PASSWORD_2FA,
                )
        else:
            ok, msg = await set_2fa_password(client, PASSWORD_2FA)

        if not ok:
            return session_str, current_password, False, msg

        try:
            new_session = client.session.save()
        except Exception:
            new_session = session_str

        return new_session, PASSWORD_2FA, True, "OK"
    finally:
        try:
            await close_client(phone)
        except Exception:
            pass