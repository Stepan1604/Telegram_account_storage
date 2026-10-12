from telebot import types


# ═══════════════════════════════════════════════════════════════════════════ #
#                              ХЕЛПЕРЫ                                         #
# ═══════════════════════════════════════════════════════════════════════════ #

def btn(text: str, cb: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=cb)


def kb(rows) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════════════════════════ #
#                              ГЛАВНОЕ МЕНЮ                                    #
# ═══════════════════════════════════════════════════════════════════════════ #

def main_menu() -> types.InlineKeyboardMarkup:
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
    return kb([[btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")]])


def cancel() -> types.InlineKeyboardMarkup:
    return kb([[btn("🔙 ОТМЕНА", "main_menu")]])


# ═══════════════════════════════════════════════════════════════════════════ #
#                          ХРАНИЛИЩЕ / ИЗБРАННОЕ                               #
# ═══════════════════════════════════════════════════════════════════════════ #

def storage_page(accounts, page: int, total_pages: int,
                 page_size: int = 5) -> types.InlineKeyboardMarkup:
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


# ═══════════════════════════════════════════════════════════════════════════ #
#                          КАРТОЧКА АККАУНТА                                    #
# ═══════════════════════════════════════════════════════════════════════════ #

def account_card(acc, page: int) -> types.InlineKeyboardMarkup:
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
    return kb([
        [btn("✅ ДА, УДАЛИТЬ", f"acc_del_yes_{acc_id}_{page}")],
        [btn("❌ ОТМЕНА", f"acc_view_{acc_id}_{page}")],
    ])


# ═══════════════════════════════════════════════════════════════════════════ #
#                              ОТЧЁТЫ                                          #
# ═══════════════════════════════════════════════════════════════════════════ #

def reports_menu() -> types.InlineKeyboardMarkup:
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
    return kb([
        [btn("📜 История действий", f"acc_history_{acc.id}_{page}")],
        [btn("📤 Экспорт этого аккаунта", f"acc_export_{acc.id}_{page}")],
        [btn("🔙 К АККАУНТУ", f"acc_view_{acc.id}_{page}")],
        [btn("📦 ХРАНИЛИЩЕ", "storage")],
        [btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
    ])


def account_history_kb(acc_id: int, page: int) -> types.InlineKeyboardMarkup:
    return kb([
        [btn("🔄 ОБНОВИТЬ", f"acc_history_{acc_id}_{page}")],
        [btn("📤 ЭКСПОРТ ИСТОРИИ", f"acc_history_export_{acc_id}_{page}")],
        [btn("🔙 К ОТЧЁТУ", f"acc_report_{acc_id}_{page}")],
    ])


# ═══════════════════════════════════════════════════════════════════════════ #
#                              LZT-ЛОГИ                                        #
# ═══════════════════════════════════════════════════════════════════════════ #

def lzt_logs_menu() -> types.InlineKeyboardMarkup:
    return kb([
        [btn("📥 ВСЕ ПОКУПКИ", "lzt_logs_all")],
        [btn("🟢 ВОШЁЛ В АККАУНТ", "lzt_logs_logged")],
        [btn("🔴 НЕ УДАЛОСЬ ВОЙТИ", "lzt_logs_failed")],
        [btn("📤 ЭКСПОРТ LZT-ЛОГОВ (CSV)", "lzt_logs_export")],
        [btn("🔙 НАЗАД", "main_menu")],
    ])


def lzt_logs_page(items, page: int, total_pages: int, filter_type: str,
                  page_size: int = 5) -> types.InlineKeyboardMarkup:
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


# ═══════════════════════════════════════════════════════════════════════════ #
#                          ПРОВЕРКА / ОЧИСТКА                                  #
# ═══════════════════════════════════════════════════════════════════════════ #

def confirm_clean() -> types.InlineKeyboardMarkup:
    return kb([
        [btn("✅ ДА, ОЧИСТИТЬ", "clean_confirm")],
        [btn("❌ ОТМЕНА", "main_menu")],
    ])


def confirm_del_dead(count: int) -> types.InlineKeyboardMarkup:
    return kb([
        [btn(f"✅ УДАЛИТЬ {count} АККАУНТОВ", "del_dead_confirm")],
        [btn("❌ ОТМЕНА", "main_menu")],
    ])


def cleaning_progress() -> types.InlineKeyboardMarkup:
    return kb([[btn("⏹ ОТМЕНИТЬ ОЧИСТКУ", "clean_cancel")]])


def after_check(has_dead: bool) -> types.InlineKeyboardMarkup:
    rows = []
    if has_dead:
        rows.append([btn("🗑️ УДАЛИТЬ МЁРТВЫЕ", "del_dead")])
    rows.append([btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")])
    return kb(rows)


# ═══════════════════════════════════════════════════════════════════════════ #
#                              ПОКУПКА LZT                                     #
# ═══════════════════════════════════════════════════════════════════════════ #

def buy_menu() -> types.InlineKeyboardMarkup:
    return kb([
        [btn("🎲 СЛУЧАЙНАЯ СТРАНА", "buy_single_random")],
        [btn("🌍 КОНКРЕТНАЯ СТРАНА", "buy_single_country")],
        [btn("📦 ОПТОВАЯ ЗАКУПКА", "buy_bulk")],
        [btn("📥 ИМПОРТ ПОКУПОК LZT", "buy_import")],
        [btn("🔙 НАЗАД", "main_menu")],
    ])


def buy_stop_kb() -> types.InlineKeyboardMarkup:
    return kb([[btn("⛔ СТОП", "buy_stop")]])


def buy_back() -> types.InlineKeyboardMarkup:
    return kb([[btn("🔙 ОТМЕНА", "buy_menu")]])


def buy_after() -> types.InlineKeyboardMarkup:
    return kb([
        [btn("📦 ХРАНИЛИЩЕ", "storage")],
        [btn("🛒 ЕЩЁ КУПИТЬ", "buy_menu")],
        [btn("🏠 ГЛАВНОЕ МЕНЮ", "main_menu")],
    ])