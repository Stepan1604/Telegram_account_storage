import csv
import io
import logging
import re
from datetime import datetime

from config import COUNTRIES

logger = logging.getLogger(__name__)


def fmt_phone(phone: str) -> str:
    """
    Приводит номер телефона к единому формату +XXXXXXXXXXX.

    Удаляет пробелы, скобки, дефисы, точки. Оставляет только цифры
    и ведущий плюс.

    :param phone: номер в любом формате.
    :return: нормализованный номер с ведущим плюсом.
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


def get_country_code(phone: str) -> str:
    """
    Определяет код страны (RU, US, ...) по номеру телефона.

    Ищет совпадение по phone_code из config.COUNTRIES. Сортирует коды
    по длине в убывающем порядке, чтобы сначала проверялись длинные.

    :param phone: номер телефона.
    :return: код страны (например, "US") или пустая строка.
    """
    if not phone:
        return ""
    p = fmt_phone(phone)
    for c in sorted(COUNTRIES, key=lambda x: len(x["phone_code"]), reverse=True):
        if p.startswith(c["phone_code"]):
            return c.get("code", "")
    return ""


def country_label(phone: str) -> str:
    """
    Возвращает красивое название страны с флагом по номеру телефона.

    :param phone: номер телефона.
    :return: строка вида "🇷🇺 Россия (+7)" или "🌍 Неизвестно".
    """
    if not phone:
        return "🌍 Неизвестно"
    p = fmt_phone(phone)
    for c in sorted(COUNTRIES, key=lambda x: len(x["phone_code"]), reverse=True):
        if p.startswith(c["phone_code"]):
            return f"{c['flag']} {c['name']} ({c['phone_code']})"
    return "🌍 Неизвестно"


def country_label_by_code(code: str) -> str:
    """
    Возвращает название страны с флагом по её коду (RU, US, ...), а не
    по номеру телефона. Используется для подписи "папок" стран в
    хранилище, где известен сохранённый country_code аккаунта.

    :param code: код страны из config.COUNTRIES, или "" для группы
        аккаунтов без определённой страны.
    :return: строка вида "🇷🇺 Россия" или "🌍 Без страны" (если код
        пуст), либо "🌍 {code}" если код не найден в справочнике.
    """
    if not code:
        return "🌍 Без страны"
    for c in COUNTRIES:
        if c.get("code") == code:
            return f"{c['flag']} {c['name']}"
    return f"🌍 {code}"


def status_label(s: str) -> str:
    """
    Возвращает человекочитаемую метку статуса аккаунта.

    :param s: статус ("cleaned", "uncleaned", "dead").
    :return: строка с эмодзи.
    """
    return {
        "cleaned": "🧹 Очищен от чужих сессий",
        "uncleaned": "⚠️ Ожидает очистки",
        "dead": "❌ Мёртвый аккаунт",
    }.get(s, s)


def format_remaining(seconds: float) -> str:
    """
    Форматирует количество секунд в строку вида "Xч Yм".

    :param seconds: количество секунд (может быть отрицательным).
    :return: строка "Xч Yм" или "ожидается", если время истекло.
    """
    if seconds <= 0:
        return "ожидается"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}ч {m}м"


def _fmt_dt(dt) -> str:
    """
    Форматирует datetime в строку "ДД.ММ.ГГГГ в ЧЧ:ММ".

    :param dt: объект datetime или None.
    :return: отформатированная строка или "—".
    """
    if not dt:
        return "—"
    try:
        return dt.strftime("%d.%m.%Y в %H:%M")
    except Exception:
        return str(dt)


def _fmt_dt_csv(dt) -> str:
    """
    Форматирует datetime для CSV-экспорта.

    :param dt: объект datetime или None.
    :return: строка "ДД.ММ.ГГГГ ЧЧ:ММ:СС" или пустая строка.
    """
    if not dt:
        return ""
    try:
        return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        return str(dt)


def lzt_status_label(s: str) -> str:
    """
    Возвращает человекочитаемую метку статуса LZT-покупки.

    :param s: статус ("bought", "logged_in", "failed").
    :return: строка с эмодзи.
    """
    return {
        "bought": "🟡 Куплен (вход не выполнялся)",
        "logged_in": "🟢 Успешный вход в аккаунт",
        "failed": "🔴 Не удалось войти",
    }.get(s, s)


def lzt_error_hint(status: str) -> str:
    """
    Возвращает подсказку по ошибке LZT-входа.

    Расшифровывает технический статус в человеческое объяснение и
    совет, что делать.

    :param status: технический код ошибки.
    :return: строка-подсказка.
    """
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


def md_escape(text: str) -> str:
    """
    Экранирует спецсимволы Markdown V1 для Telegram.

    В Markdown V1 символы _, *, `, [ нужно экранировать, иначе
    Telegram вернёт ошибку парсинга.

    :param text: текст для экранирования.
    :return: безопасный текст.
    """
    if not text:
        return ""
    for ch in ("_", "*", "`", "["):
        text = str(text).replace(ch, f"\\{ch}")
    return text


async def edit_or_send(bot, call, text, markup=None, parse_mode=None):
    """
    Редактирует сообщение или отправляет новое, если редактирование
    невозможно.

    Полезно для callback-хендлеров, когда нужно обновить текущее
    сообщение, но оно может быть уже удалено или недоступно.

    :param bot: экземпляр AsyncTeleBot.
    :param call: callback_query от пользователя.
    :param text: текст сообщения.
    :param markup: InlineKeyboardMarkup или None.
    :param parse_mode: "Markdown", "HTML" или None.
    """
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
    """
    Редактирует сообщение, игнорируя ошибку "message is not modified".

    :param bot: экземпляр AsyncTeleBot.
    :param chat_id: ID чата.
    :param msg_id: ID сообщения.
    :param text: новый текст.
    :param markup: InlineKeyboardMarkup или None.
    :param parse_mode: "Markdown", "HTML" или None.
    """
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
    """
    Создаёт CSV-writer с настройками под Excel/LibreOffice.

    :param buf: StringIO-буфер.
    :return: csv.writer.
    """
    return csv.writer(
        buf,
        delimiter=";",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )


def build_accounts_csv(accounts) -> bytes:
    """
    Собирает CSV со всеми аккаунтами.

    Формат: UTF-8 с BOM, разделитель ";". Открывается в Excel,
    LibreOffice и на телефонах без настроек.

    :param accounts: список объектов Account.
    :return: bytes готового CSV-файла.
    """
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
    """
    Собирает CSV с одним аккаунтом.

    Все поля идут по строкам (поле;значение). В конце — session_string
    в открытом виде, чтобы можно было восстановить аккаунт вручную.

    :param acc: объект Account.
    :return: bytes готового CSV-файла.
    """
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
    """
    Собирает CSV со всеми логами действий.

    :param actions: список объектов ActionLog.
    :return: bytes готового CSV-файла.
    """
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
    """
    Собирает CSV с историей действий одного аккаунта.

    :param acc: объект Account.
    :param actions: список ActionLog по этому аккаунту.
    :return: bytes готового CSV-файла.
    """
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
    """
    Собирает CSV со всеми LZT-покупками.

    :param items: список объектов LZTPurchase.
    :return: bytes готового CSV-файла.
    """
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
