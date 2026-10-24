import csv
import io
import logging
import re
from datetime import datetime

from config import COUNTRIES

logger = logging.getLogger(__name__)


COUNTRY_EN_TO_RU = {
    "USA": "США",
    "United States": "США",
    "United Kingdom": "Великобритания",
    "UK": "Великобритания",
    "Canada": "Канада",
    "Germany": "Германия",
    "France": "Франция",
    "Spain": "Испания",
    "Italy": "Италия",
    "Netherlands": "Нидерланды",
    "Poland": "Польша",
    "Sweden": "Швеция",
    "Norway": "Норвегия",
    "Finland": "Финляндия",
    "Denmark": "Дания",
    "Portugal": "Португалия",
    "Greece": "Греция",
    "Czechia": "Чехия",
    "Czech Republic": "Чехия",
    "Romania": "Румыния",
    "Bulgaria": "Болгария",
    "Hungary": "Венгрия",
    "Austria": "Австрия",
    "Switzerland": "Швейцария",
    "Ireland": "Ирландия",
    "Belgium": "Бельгия",
    "Turkey": "Турция",
    "India": "Индия",
    "Indonesia": "Индонезия",
    "Thailand": "Таиланд",
    "Vietnam": "Вьетнам",
    "Malaysia": "Малайзия",
    "Philippines": "Филиппины",
    "Japan": "Япония",
    "South Korea": "Южная Корея",
    "Korea": "Южная Корея",
    "China": "Китай",
    "Hong Kong": "Гонконг",
    "Pakistan": "Пакистан",
    "Bangladesh": "Бангладеш",
    "Myanmar": "Мьянма",
    "Cambodia": "Камбоджа",
    "Sri Lanka": "Шри-Ланка",
    "Nepal": "Непал",
    "Brazil": "Бразилия",
    "Argentina": "Аргентина",
    "Mexico": "Мексика",
    "Colombia": "Колумбия",
    "Chile": "Чили",
    "Peru": "Перу",
    "Venezuela": "Венесуэла",
    "Ecuador": "Эквадор",
    "Nigeria": "Нигерия",
    "Kenya": "Кения",
    "Egypt": "Египет",
    "South Africa": "ЮАР",
    "Morocco": "Марокко",
    "Tunisia": "Тунис",
    "Algeria": "Алжир",
    "Iran": "Иран",
    "Iraq": "Ирак",
    "Saudi Arabia": "Саудовская Аравия",
    "United Arab Emirates": "ОАЭ",
    "UAE": "ОАЭ",
    "Israel": "Израиль",
    "Kazakhstan": "Казахстан",
    "Uzbekistan": "Узбекистан",
    "Kyrgyzstan": "Киргизия",
    "Tajikistan": "Таджикистан",
    "Ukraine": "Украина",
    "Belarus": "Беларусь",
    "Moldova": "Молдова",
    "Georgia": "Грузия",
    "Armenia": "Армения",
    "Azerbaijan": "Азербайджан",
    "Russia": "Россия",
    "Australia": "Австралия",
    "New Zealand": "Новая Зеландия",
}


def country_ru(name: str) -> str:
    """
    Возвращает русское название страны.

    Ищет в словаре COUNTRY_EN_TO_RU по точному совпадению (регистро-
    независимо). Если не находит — возвращает исходное название.

    :param name: английское название (как приходит из LZT).
    :return: русское название или исходное, если не найдено.
    """
    if not name:
        return name
    low = name.strip().lower()
    for en, ru in COUNTRY_EN_TO_RU.items():
        if en.lower() == low:
            return ru
    return name


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


def country_label_by_code(code: str) -> str:
    if not code:
        return "🌍 Без страны"
    for c in COUNTRIES:
        if c.get("code") == code:
            return f"{c['flag']} {c['name']}"
    return f"🌍 {code}"


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


def _fmt_dt_csv(dt) -> str:
    if not dt:
        return ""
    try:
        return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        return str(dt)


def lzt_status_label(s: str) -> str:
    return {
        "bought": "🟡 Куплен (вход не выполнялся)",
        "logged_in": "🟢 Успешный вход в аккаунт",
        "failed": "🔴 Не удалось войти",
    }.get(s, s)


def lzt_error_hint(status: str) -> str:
    hints = {
        "no_code_from_market": (
            "LZT не отдал код за отведённое время.\n"
            "💡 Подожди 5–10 минут и повтори вход через LZT-ЛОГИ."
        ),
        "invalid_code": (
            "Код пришёл, но Telegram его не принял.\n"
            "💡 Повтори вход — LZT пришлёт новый код."
        ),
        "needs_2fa": (
            "Нужен 2FA, но его нет в карточке лота.\n"
            "💡 Проверь данные лота на LZT."
        ),
        "bad_2fa_password": (
            "Пароль 2FA не подошёл.\n"
            "💡 Проверь telegram_password в данных лота."
        ),
        "send_unavailable": (
            "Telegram не даёт запросить код.\n"
            "💡 Подожди 1–2 минуты и повтори."
        ),
        "phone_banned": "Номер забанен Telegram — восстановить нельзя.",
        "phone_invalid": "Неверный формат номера. Обратись в поддержку LZT.",
        "phone_flood": (
            "Слишком частые запросы на номер.\n"
            "💡 Подожди 10–15 минут."
        ),
        "timeout": (
            "Сервер не ответил за 15–20 секунд.\n"
            "💡 Проверь соединение и повтори. "
            "Покупка не отменена — лот уже твой, вход можно повторить."
        ),
        "cancelled": "Операция отменена пользователем.",
        "auth_restart": (
            "Telegram запросил перезапуск авторизации.\n"
            "💡 Повтори вход — обычно проходит со второй попытки."
        ),
        "error:session_save": (
            "Telegram не дал сохранить сессию.\n"
            "💡 Повтори вход — если не помогает, обратись в поддержку."
        ),
        "error:empty_session": (
            "Сессия получилась пустой.\n"
            "💡 Повтори вход."
        ),
        "error:connect_TimeoutError": (
            "Telegram DC не отвечает.\n"
            "💡 Проверь соединение / прокси, затем повтори вход."
        ),
        "error:connect_ConnectionError": (
            "Не удалось установить соединение с Telegram.\n"
            "💡 Проверь сеть / прокси."
        ),
    }

    if status.startswith("flood_"):
        return (
            "FloodWait — Telegram временно заблокировал запросы.\n"
            f"💡 Подожди {status[6:]} секунд и повтори."
        )

    if status.startswith("error:connect_"):
        exc_name = status[len("error:connect_"):]
        return (
            f"Ошибка подключения к Telegram: {exc_name}.\n"
            "💡 Проверь сеть / прокси и повтори."
        )

    if status.startswith("error:"):
        return (
            f"Техническая ошибка: {status[6:]}.\n"
            "💡 Повтори вход через LZT-ЛОГИ."
        )

    return hints.get(status, "Неизвестная причина. Проверь логи бота.")


def md_escape(text: str) -> str:
    if not text:
        return ""
    for ch in ("_", "*", "`", "["):
        text = str(text).replace(ch, f"\\{ch}")
    return text


async def edit_or_send(bot, call, text, markup=None, parse_mode=None):
    try:
        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup,
        )
    except Exception as e:
        if "not modified" in str(e).lower():
            return
        logger.warning(f"[edit_or_send] edit failed: {type(e).__name__}: {e}")
        try:
            await bot.send_message(
                call.message.chat.id,
                text,
                parse_mode=parse_mode,
                reply_markup=markup,
            )
        except Exception as e2:
            logger.error(
                f"[edit_or_send] send failed: {type(e2).__name__}: {e2}"
            )


async def safe_edit(bot, chat_id, msg_id, text, markup=None, parse_mode=None):
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup,
        )
    except Exception as e:
        if "not modified" in str(e).lower():
            return
        logger.warning(f"[safe_edit] {type(e).__name__}: {e}")


_CSV_STATUS = {
    "cleaned": "🧹 Очищен",
    "uncleaned": "⚠️ Ожидает очистки",
    "dead": "❌ Мёртвый",
}


def _csv_writer(buf):
    return csv.writer(
        buf,
        delimiter=";",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )


def build_accounts_csv(accounts) -> bytes:
    buf = io.StringIO()
    w = _csv_writer(buf)
    w.writerow([
        "Название", "Номер", "Страна", "Код страны", "Статус",
        "2FA", "Цена (₽)", "Источник", "Лот LZT", "Избранное",
        "Добавлен", "Проверен", "Очищен", "Ошибок проверки",
    ])
    for acc in accounts:
        w.writerow([
            acc.name or acc.phone,
            acc.phone,
            country_label(acc.phone) or "—",
            acc.country_code or "",
            _CSV_STATUS.get(acc.status, acc.status or ""),
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
    w = _csv_writer(buf)
    w.writerow(["Поле", "Значение"])
    w.writerow(["ID", acc.id])
    w.writerow(["Название", acc.name or acc.phone])
    w.writerow(["Номер", acc.phone])
    w.writerow(["Страна", country_label(acc.phone) or "—"])
    w.writerow(["Код страны", acc.country_code or ""])
    w.writerow(["Статус", _CSV_STATUS.get(acc.status, acc.status or "")])
    w.writerow(["2FA", acc.two_fa or ""])
    w.writerow(["Цена (₽)", acc.price or 0])
    w.writerow(["Источник", acc.bought_from or "manual"])
    w.writerow(["Лот LZT", acc.lzt_item_id or ""])
    w.writerow(["Избранное", "⭐" if (acc.favorite or 0) == 1 else ""])
    w.writerow(["Добавлен", _fmt_dt_csv(acc.registered_at)])
    w.writerow(["Проверен", _fmt_dt_csv(acc.last_checked_at)])
    w.writerow(["Очищен", _fmt_dt_csv(acc.last_cleaned_at)])
    w.writerow(["Ошибок проверки", acc.health_fail_count or 0])
    w.writerow([])
    w.writerow(["Session (StringSession)", acc.session or ""])
    return buf.getvalue().encode("utf-8-sig")


def build_logs_csv(actions) -> bytes:
    buf = io.StringIO()
    w = _csv_writer(buf)
    w.writerow(["Дата", "Пользователь", "Действие",
                "Аккаунт (ID)", "Детали"])
    for a in actions:
        w.writerow([
            _fmt_dt_csv(a.created_at),
            a.user_id or 0,
            a.action or "",
            a.account_id or "",
            a.details or "",
        ])
    return buf.getvalue().encode("utf-8-sig")


def build_account_logs_csv(acc, actions) -> bytes:
    buf = io.StringIO()
    w = _csv_writer(buf)
    w.writerow(["Аккаунт", acc.name or acc.phone])
    w.writerow(["Номер", acc.phone])
    w.writerow(["Страна", country_label(acc.phone) or "—"])
    w.writerow(["Всего записей", len(actions)])
    w.writerow([])
    w.writerow(["Дата", "Пользователь", "Действие", "Детали"])
    for a in actions:
        w.writerow([
            _fmt_dt_csv(a.created_at),
            a.user_id or 0,
            a.action or "",
            a.details or "",
        ])
    return buf.getvalue().encode("utf-8-sig")


def build_lzt_logs_csv(items) -> bytes:
    buf = io.StringIO()
    w = _csv_writer(buf)
    w.writerow(["Лот", "Номер", "Страна", "Цена (₽)", "Статус",
                "Ошибка", "Куплен"])
    status_map = {
        "bought": "🟡 Куплен (без входа)",
        "logged_in": "🟢 Вошёл",
        "failed": "🔴 Провалился",
    }
    for it in items:
        w.writerow([
            it.item_id,
            it.phone or "",
            it.country or "",
            it.price or 0,
            status_map.get(it.status, it.status or ""),
            it.error or "",
            _fmt_dt_csv(it.bought_at),
        ])
    return buf.getvalue().encode("utf-8-sig")