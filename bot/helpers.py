import csv
import io
import logging
import re
from datetime import datetime

from config import COUNTRIES

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════ #
#                              НОМЕРА / СТРАНЫ                                #
# ═══════════════════════════════════════════════════════════════════════════ #

def fmt_phone(phone: str) -> str:
    """Единый формат номера: +XXXXXXXXXXX (только цифры, ведущий +)."""
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
    """Возвращает код страны (RU, US, …) по номеру или ''."""
    if not phone:
        return ""
    p = fmt_phone(phone)
    for c in sorted(COUNTRIES, key=lambda x: len(x["phone_code"]), reverse=True):
        if p.startswith(c["phone_code"]):
            return c.get("code", "")
    return ""


def country_label(phone: str) -> str:
    """Возвращает '🇷🇺 Россия (+7)' или '🌍 Неизвестно'."""
    if not phone:
        return "🌍 Неизвестно"
    p = fmt_phone(phone)
    for c in sorted(COUNTRIES, key=lambda x: len(x["phone_code"]), reverse=True):
        if p.startswith(c["phone_code"]):
            return f"{c['flag']} {c['name']} ({c['phone_code']})"
    return "🌍 Неизвестно"


# ═══════════════════════════════════════════════════════════════════════════ #
#                              СТАТУСЫ / ВРЕМЯ                                 #
# ═══════════════════════════════════════════════════════════════════════════ #

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


# ═══════════════════════════════════════════════════════════════════════════ #
#                              LZT-СТАТУСЫ                                     #
# ═══════════════════════════════════════════════════════════════════════════ #

def lzt_status_label(s: str) -> str:
    return {
        "bought": "🟡 Куплен (вход не выполнялся)",
        "logged_in": "🟢 Успешный вход в аккаунт",
        "failed": "🔴 Не удалось войти",
    }.get(s, s)


def lzt_error_hint(status: str) -> str:
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


# ═══════════════════════════════════════════════════════════════════════════ #
#                              TELEGRAM UI                                     #
# ═══════════════════════════════════════════════════════════════════════════ #

async def edit_or_send(bot, call, text, markup=None, parse_mode=None):
    """Редактирует сообщение, если возможно. Иначе отправляет новое."""
    try:
        await bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=markup,
        )
    except Exception as e:
        logger.warning(f"[edit_or_send] edit failed: {type(e).__name__}: {e}")
        try:
            await bot.send_message(
                call.message.chat.id,
                text,
                parse_mode=parse_mode,
                reply_markup=markup,
            )
        except Exception as e2:
            logger.error(f"[edit_or_send] send failed: {type(e2).__name__}: {e2}")


async def safe_edit(bot, chat_id, msg_id, text, markup=None, parse_mode=None):
    """Edit без падения на 'message is not modified'."""
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


# ═══════════════════════════════════════════════════════════════════════════ #
#                              CSV ЭКСПОРТ                                     #
# ═══════════════════════════════════════════════════════════════════════════ #

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
    """CSV со всеми аккаунтами. UTF-8 + BOM, разделитель ';'."""
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
    """CSV с одним аккаунтом — все поля по строкам + session."""
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
    """CSV со всеми логами действий."""
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
    """CSV с историей одного аккаунта."""
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
    """CSV со всеми LZT-покупками."""
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