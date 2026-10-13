from telebot import types


def btn(text: str, cb: str) -> types.InlineKeyboardButton:
    """
    Создаёт inline-кнопку с текстом и callback-данными.

    :param text: текст на кнопке.
    :param cb: callback_data, которая отправится при нажатии.
    :return: объект InlineKeyboardButton.
    """
    return types.InlineKeyboardButton(text=text, callback_data=cb)


def kb(rows) -> types.InlineKeyboardMarkup:
    """
    Собирает клавиатуру из списка рядов кнопок.

    :param rows: список списков InlineKeyboardButton.
    :return: объект InlineKeyboardMarkup.
    """
    return types.InlineKeyboardMarkup(rows)


def main_menu() -> types.InlineKeyboardMarkup:
    """
    Главное меню бота.

    :return: InlineKeyboardMarkup с кнопками основных разделов.
    """
    return kb([
        [btn("📦 ХРАНИЛИЩЕ", "storage")],
        [btn("⭐ ИЗБРАННОЕ", "favorites")],
        [btn("🛒 КУПИТЬ НОМЕРА", "buy_menu")],
        [btn("📊 ОТЧЁТЫ", "reports_menu"), btn("📥 LZT-ЛОГИ", "lzt_logs")],
        [btn("🔍 ПРОВЕРКА АККАУНТОВ", "check_accounts")],
        [btn("🗑️ УДАЛИТЬ МЁРТВЫЕ", "del_dead")],
        [btn("🧹 ОЧИСТИТЬ СЕССИИ", "clean_sessions")],
        [btn("➕ ДОБАВИТЬ АККАУНТ", "add_account")],
    ])


def back_main() -> types.InlineKeyboardMarkup:
    """
    Клавиатура с одной кнопкой возврата в главное меню.

    :return: InlineKeyboardMarkup.
    """
    return kb([[btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")]])


def cancel() -> types.InlineKeyboardMarkup:
    """
    Клавиатура с кнопкой отмены действия.

    :return: InlineKeyboardMarkup.
    """
    return kb([[btn("🔙 ОТМЕНА", "main_menu")]])


def storage_page(accounts, page: int, total_pages: int,
                 page_size: int = 5) -> types.InlineKeyboardMarkup:
    """
    Клавиатура страницы хранилища.

    Формирует список аккаунтов с эмодзи статуса и звёздочкой избранного,
    навигацию ⬅️/➡️ и кнопку возврата в меню.

    :param accounts: список объектов Account для текущей страницы.
    :param page: номер текущей страницы (0-индексация).
    :param total_pages: всего страниц.
    :param page_size: количество аккаунтов на странице.
    :return: InlineKeyboardMarkup.
    """
    rows = []
    for acc in accounts:
        emoji = {
            "cleaned": "🧹",
            "uncleaned": "⚠️",
            "dead": "❌",
        }.get(acc.status, "•")
        star = "⭐ " if (acc.favorite or 0) == 1 else ""
        label = acc.name or acc.phone
        rows.append([
            btn(f"{star}{emoji} {label}", f"acc_view_{acc.id}_{page}")
        ])

    nav = []
    if page > 0:
        nav.append(btn("⬅️", f"storage_page_{page - 1}"))
    nav.append(btn(f"{page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(btn("➡️", f"storage_page_{page + 1}"))
    rows.append(nav)
    rows.append([btn("🔙 НАЗАД", "main_menu")])
    return kb(rows)


def favorites_page(accounts, page: int, total_pages: int,
                   page_size: int = 5) -> types.InlineKeyboardMarkup:
    """
    Клавиатура страницы избранного.

    Аналогична storage_page, но все кнопки со звёздочкой.

    :param accounts: список объектов Account для текущей страницы.
    :param page: номер текущей страницы.
    :param total_pages: всего страниц.
    :param page_size: количество аккаунтов на странице.
    :return: InlineKeyboardMarkup.
    """
    rows = []
    for acc in accounts:
        emoji = {
            "cleaned": "🧹",
            "uncleaned": "⚠️",
            "dead": "❌",
        }.get(acc.status, "•")
        label = acc.name or acc.phone
        rows.append([
            btn(f"⭐ {emoji} {label}", f"acc_view_{acc.id}_{page}")
        ])

    nav = []
    if page > 0:
        nav.append(btn("⬅️", f"favorites_page_{page - 1}"))
    nav.append(btn(f"{page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(btn("➡️", f"favorites_page_{page + 1}"))
    rows.append(nav)
    rows.append([btn("🔙 НАЗАД", "main_menu")])
    return kb(rows)


def account_card(acc, page: int) -> types.InlineKeyboardMarkup:
    """
    Клавиатура карточки аккаунта.

    Кнопки: получить код, обновить инфо, очистить сессии/чаты,
    отчёт, экспорт, избранное, переименование, смена статуса, удаление.

    :param acc: объект Account.
    :param page: номер страницы хранилища, с которой пришли.
    :return: InlineKeyboardMarkup.
    """
    current = acc.name or acc.phone
    if (acc.favorite or 0) == 1:
        fav_label = "★ УБРАТЬ ИЗ ИЗБРАННОГО"
    else:
        fav_label = "☆ В ИЗБРАННОЕ"

    return kb([
        [btn("🔑 ПОЛУЧИТЬ КОД", f"acc_code_{acc.id}_{page}")],
        [btn("🔄 ОБНОВИТЬ ИНФУ", f"acc_view_{acc.id}_{page}")],
        [btn("🧹 ОЧИСТИТЬ СЕССИИ", f"acc_clean_{acc.id}_{page}")],
        [btn("🗑️ ОЧИСТИТЬ ЧАТЫ", f"acc_clear_chats_{acc.id}_{page}")],
        [btn("📊 ОТЧЁТ ПО АККАУНТУ", f"acc_report_{acc.id}_{page}")],
        [btn("📤 ЭКСПОРТ ЭТОГО АККАУНТА", f"acc_export_{acc.id}_{page}")],
        [btn(fav_label, f"acc_fav_{acc.id}_{page}")],
        [btn(f"🏷 ДАТЬ НАЗВАНИЕ ({current[:20]})", f"acc_rename_{acc.id}_{page}")],
        [btn("✏️ ИЗМЕНИТЬ СТАТУС", f"acc_status_{acc.id}_{page}")],
        [btn("🗑️ УДАЛИТЬ", f"acc_del_{acc.id}_{page}")],
        [btn("🔙 НАЗАД", f"storage_page_{page}")],
    ])


def status_menu(acc_id: int, page: int) -> types.InlineKeyboardMarkup:
    """
    Клавиатура выбора нового статуса аккаунта.

    :param acc_id: ID аккаунта.
    :param page: номер страницы хранилища.
    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn("🧹 Очищен от чужих сессий",
             f"acc_status_set_{acc_id}_cleaned_{page}")],
        [btn("⚠️ Ожидает очистки",
             f"acc_status_set_{acc_id}_uncleaned_{page}")],
        [btn("❌ Мёртвый аккаунт",
             f"acc_status_set_{acc_id}_dead_{page}")],
        [btn("🔙 НАЗАД", f"acc_view_{acc_id}_{page}")],
    ])


def confirm_delete(acc_id: int, page: int) -> types.InlineKeyboardMarkup:
    """
    Клавиатура подтверждения удаления аккаунта.

    :param acc_id: ID аккаунта.
    :param page: номер страницы хранилища.
    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn("✅ ДА, УДАЛИТЬ", f"acc_del_yes_{acc_id}_{page}")],
        [btn("❌ ОТМЕНА", f"acc_view_{acc_id}_{page}")],
    ])


def reports_menu() -> types.InlineKeyboardMarkup:
    """
    Меню отчётов.

    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn("📊 ОБЩИЙ ОТЧЁТ", "report_total")],
        [btn("📅 ЗА 30 ДНЕЙ", "report_30d")],
        [btn("📱 ПО АККАУНТАМ", "report_accounts")],
        [btn("🏆 ТОП ИСТОЧНИКОВ", "report_sources")],
        [btn("📤 ЭКСПОРТ ВСЕХ (CSV)", "report_export_all")],
        [btn("📜 ЭКСПОРТ ЛОГОВ (CSV)", "report_export_logs")],
        [btn("🔙 НАЗАД", "main_menu")],
    ])


def reports_accounts_page(accounts, page: int, total_pages: int,
                          page_size: int = 6) -> types.InlineKeyboardMarkup:
    """
    Клавиатура страницы отчётов по аккаунтам.

    :param accounts: список аккаунтов для текущей страницы.
    :param page: номер страницы.
    :param total_pages: всего страниц.
    :param page_size: количество аккаунтов на странице.
    :return: InlineKeyboardMarkup.
    """
    rows = []
    for acc in accounts:
        label = acc.name or acc.phone
        rows.append([btn(f"📊 {label[:28]}", f"acc_report_{acc.id}_0")])

    nav = []
    if page > 0:
        nav.append(btn("⬅️", f"report_accounts_page_{page - 1}"))
    nav.append(btn(f"{page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(btn("➡️", f"report_accounts_page_{page + 1}"))
    rows.append(nav)
    rows.append([btn("🔙 НАЗАД", "reports_menu")])
    return kb(rows)


def account_report_kb(acc, page: int = 0) -> types.InlineKeyboardMarkup:
    """
    Клавиатура под отчётом по одному аккаунту.

    :param acc: объект Account.
    :param page: номер страницы для возврата.
    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn("📜 История действий", f"acc_history_{acc.id}_{page}")],
        [btn("📤 Экспорт этого аккаунта", f"acc_export_{acc.id}_{page}")],
        [btn("🔙 К АККАУНТУ", f"acc_view_{acc.id}_{page}")],
        [btn("📦 ХРАНИЛИЩЕ", "storage")],
        [btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
    ])


def account_history_kb(acc_id: int, page: int) -> types.InlineKeyboardMarkup:
    """
    Клавиатура под историей действий аккаунта.

    :param acc_id: ID аккаунта.
    :param page: номер страницы для возврата.
    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn("🔄 ОБНОВИТЬ", f"acc_history_{acc_id}_{page}")],
        [btn("📤 ЭКСПОРТ ИСТОРИИ", f"acc_history_export_{acc_id}_{page}")],
        [btn("🔙 К ОТЧЁТУ", f"acc_report_{acc_id}_{page}")],
    ])


def lzt_logs_menu() -> types.InlineKeyboardMarkup:
    """
    Меню LZT-логов.

    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn("📥 ВСЕ ПОКУПКИ", "lzt_logs_all")],
        [btn("🟢 ВОШЁЛ В АККАУНТ", "lzt_logs_logged")],
        [btn("🔴 НЕ УДАЛОСЬ ВОЙТИ", "lzt_logs_failed")],
        [btn("📤 ЭКСПОРТ LZT-ЛОГОВ (CSV)", "lzt_logs_export")],
        [btn("🔙 НАЗАД", "main_menu")],
    ])


def lzt_logs_page(items, page: int, total_pages: int, filter_type: str,
                  page_size: int = 5) -> types.InlineKeyboardMarkup:
    """
    Клавиатура страницы LZT-логов.

    Формирует список записей с эмодзи статуса, навигацию и кнопку назад.

    :param items: список объектов LZTPurchase для текущей страницы.
    :param page: номер текущей страницы.
    :param total_pages: всего страниц.
    :param filter_type: фильтр ("all", "logged", "failed", "bought").
    :param page_size: количество записей на странице.
    :return: InlineKeyboardMarkup.
    """
    rows = []
    for it in items:
        icon = {
            "bought": "🟡",
            "logged_in": "🟢",
            "failed": "🔴",
        }.get(it.status, "•")
        phone = it.phone or "?"
        rows.append([
            btn(f"{icon} #{it.item_id} {phone}",
                f"lzt_log_view_{it.item_id}_{filter_type}_{page}")
        ])

    nav = []
    if page > 0:
        nav.append(btn("⬅️", f"lzt_logs_{filter_type}_{page - 1}"))
    nav.append(btn(f"{page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(btn("➡️", f"lzt_logs_{filter_type}_{page + 1}"))
    rows.append(nav)
    rows.append([btn("🔙 НАЗАД", "lzt_logs")])
    return kb(rows)


def lzt_log_detail_kb(item_id: int, filter_type: str, page: int,
                      can_retry: bool = False) -> types.InlineKeyboardMarkup:
    """
    Клавиатура деталей одной LZT-записи.

    :param item_id: ID лота LZT.
    :param filter_type: фильтр, с которого пришли.
    :param page: номер страницы, с которой пришли.
    :param can_retry: показывать ли кнопку повторного входа.
    :return: InlineKeyboardMarkup.
    """
    rows = []
    if can_retry:
        rows.append([
            btn("🔁 ПОВТОРИТЬ ВХОД",
                f"lzt_retry_{item_id}_{filter_type}_{page}")
        ])
    rows.append([btn("🔙 К СПИСКУ", f"lzt_logs_{filter_type}_{page}")])
    rows.append([btn("📥 LZT-ЛОГИ", "lzt_logs")])
    rows.append([btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")])
    return kb(rows)


def confirm_clean() -> types.InlineKeyboardMarkup:
    """
    Клавиатура подтверждения массовой очистки.

    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn("✅ ДА, ОЧИСТИТЬ", "clean_confirm")],
        [btn("❌ ОТМЕНА", "main_menu")],
    ])


def confirm_del_dead(count: int) -> types.InlineKeyboardMarkup:
    """
    Клавиатура подтверждения удаления мёртвых аккаунтов.

    :param count: сколько мёртвых аккаунтов будет удалено.
    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn(f"✅ УДАЛИТЬ {count} АККАУНТОВ", "del_dead_confirm")],
        [btn("❌ ОТМЕНА", "main_menu")],
    ])


def cleaning_progress() -> types.InlineKeyboardMarkup:
    """
    Клавиатура прогресса очистки с кнопкой отмены.

    :return: InlineKeyboardMarkup.
    """
    return kb([[btn("⏹ ОТМЕНИТЬ ОЧИСТКУ", "clean_cancel")]])


def after_check(has_dead: bool) -> types.InlineKeyboardMarkup:
    """
    Клавиатура после проверки аккаунтов.

    :param has_dead: если True — добавляет кнопку удаления мёртвых.
    :return: InlineKeyboardMarkup.
    """
    rows = []
    if has_dead:
        rows.append([btn("🗑️ УДАЛИТЬ МЁРТВЫЕ", "del_dead")])
    rows.append([btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")])
    return kb(rows)


def buy_menu() -> types.InlineKeyboardMarkup:
    """
    Меню покупки номеров через LZT.

    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn("🎲 СЛУЧАЙНАЯ СТРАНА", "buy_single_random")],
        [btn("🌍 КОНКРЕТНАЯ СТРАНА", "buy_single_country")],
        [btn("📦 ОПТОВАЯ ЗАКУПКА", "buy_bulk")],
        [btn("📥 ИМПОРТ ПОКУПОК LZT", "buy_import")],
        [btn("🔙 НАЗАД", "main_menu")],
    ])


def buy_stop_kb() -> types.InlineKeyboardMarkup:
    """
    Клавиатура с кнопкой остановки процесса покупки.

    :return: InlineKeyboardMarkup.
    """
    return kb([[btn("⛔ СТОП", "buy_stop")]])


def buy_back() -> types.InlineKeyboardMarkup:
    """
    Клавиатура возврата в меню покупки.

    :return: InlineKeyboardMarkup.
    """
    return kb([[btn("🔙 ОТМЕНА", "buy_menu")]])


def buy_after() -> types.InlineKeyboardMarkup:
    """
    Клавиатура после успешной покупки.

    :return: InlineKeyboardMarkup.
    """
    return kb([
        [btn("📦 ХРАНИЛИЩЕ", "storage")],
        [btn("🛒 ЕЩЁ КУПИТЬ", "buy_menu")],
        [btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
    ])