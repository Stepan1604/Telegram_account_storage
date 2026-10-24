# 🏛 Telegram Account Manager

<p align="center">
  <b>Профессиональный Telegram-бот для управления аккаунтами</b><br>
  Покупка · Хранение · Очистка · Проверка · Аналитика
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/SQLAlchemy-2.x-red?style=for-the-badge&logo=sqlalchemy&logoColor=white" alt="SQLAlchemy">
  <img src="https://img.shields.io/badge/Telethon-1.x-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white" alt="Telethon">
  <img src="https://img.shields.io/badge/PostgreSQL-supported-336791?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/SQLite-fallback-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite">
</p>

---

## 📖 О проекте

**Telegram Account Manager** — это полнофункциональный асинхронный Telegram-бот, созданный для администрирования парка Telegram-аккаунтов. Бот автоматизирует весь жизненный цикл аккаунта: от покупки на маркетплейсе **LZT Market** до очистки сессий и контроля «здоровья».

Проект построен на **AsyncTeleBot** + **Telethon** + **SQLAlchemy**, поддерживает **PostgreSQL** с автоматическим **fallback на локальную SQLite** при недоступности основной БД — бот продолжит работу даже при обрыве связи с сервером БД.

> 🔐 Все session и 2FA-пароли шифруются через **Fernet** перед записью в БД. Ключ хранится отдельно в `data/secret.key` или задаётся через `SESSION_ENCRYPTION_KEY`.

---

## ✨ Ключевые возможности

<table>
<tr>
<td width="50%">

### 📦 Управление хранилищем
- 📁 Группировка аккаунтов по **странам** (`RU`, `US`, …)
- ⭐ **Избранное** — отдельный быстрый список
- 🏷 Переименование, смена статуса, привязка email
- 📤 Экспорт в CSV (все аккаунты, один аккаунт, история)
- 🗑️ Мягкое и полное удаление
- 🔍 Просмотр профиля из Telegram (имя, username, ID, сессии, чаты)

### 🛒 Покупка через LZT Market
- 🎲 Покупка **случайной страны** по цене
- 📦 **Оптовая** закупка N штук
- 📥 **Импорт** уже купленных ранее лотов
- 🚫 Автоматический **фильтр спамблока**
- 💳 Работа **только через баланс «Для покупки аккаунтов»**
- 🔁 Повторный вход по любому лоту из LZT-логов

</td>
<td width="50%">

### 🧹 Очистка и проверка
- 🔄 Сброс **всех чужих сессий** (`ResetAuthorizations`)
- 💬 Полная очистка диалогов, групп, каналов
- 🔍 Проверка **жизни** аккаунтов
- ⏰ **Автоочистка** через `AUTO_CLEAN_DELAY` после покупки
- 🧊 Детект **замороженных** Telegram-аккаунтов
- ❔ Разделение «мёртв» vs «не удалось проверить»

### 📊 Аналитика
- 📈 Статистика по дням / за всё время
- 📱 Отчёты по каждому аккаунту
- 🏆 Топ источников (manual / LZT / import)
- 📥 LZT-логи со статусами и ошибками
- 📜 Полная история действий пользователей
- 💾 CSV-экспорт любой статистики

</td>
</tr>
</table>


## 🛠 Технологический стек

| Компонент | Технология |
|---|---|
| **Bot framework** | [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) (async) |
| **Telegram client** | [Telethon](https://github.com/LonamiWebs/Telethon) |
| **ORM** | SQLAlchemy 2.x (declarative, scoped_session) |
| **БД** | PostgreSQL (primary) / SQLite (fallback) |
| **Шифрование** | `cryptography` (Fernet) |
| **Маркетплейс** | [LOLZTEAM](https://github.com/LOLZTEAM/LOLZTEAM) |
| **HTTP-клиент** | `curl_cffi` (обход LZT JSON-разбора) |

---

## 🚀 Установка

### 1. Клонирование и зависимости

```bash
git clone <your-repo-url> tg-account-manager
cd tg-account-manager

python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

pip install -r requirements.txt
```

### 2. Настройка `config.py`

Создайте файл `config.py` в корне проекта:

```python
# ─── Telegram ────────────────────────────────────────────────
BOT_TOKEN   = "123456:ABC..."          # токен бота от @BotFather
ADMIN_ID    = 123456789                # ваш Telegram ID
ADMINS      = [123456789, 987654321]   # список админов бота

API_ID      = 1234567                  # my.telegram.org
API_HASH    = "abc123def456..."

# ─── База данных ─────────────────────────────────────────────
DB_URL = "postgresql+asyncpg://user:pass@host/db"
# При недоступности — автоматический fallback на data/storage.db

SESSION_ENCRYPTION_KEY = ""            # "" = читать/создать data/secret.key

# ─── 2FA и почта ─────────────────────────────────────────────
PASSWORD_2FA = "MySecret2FA"           # авто-установка на новые аккаунты
LOGIN_EMAIL  = "accounts@example.com"  # email для привязки
IMAP_HOST     = "imap.gmail.com"
IMAP_USER     = "accounts@example.com"
IMAP_PASSWORD = "app-password"

# ─── LZT Market ──────────────────────────────────────────────
LZT_API_TOKEN = "your_lzt_token"
LZT_PROXY     = ""                     # "http://user:pass@host:port"
MIN_PRICE     = 0
SPAM_FILTER   = "no"                   # "no" | "yes"

# ─── Поведение ───────────────────────────────────────────────
AUTO_CLEAN_DELAY = 24 * 60 * 60 + 10   # 24ч 10сек после добавления
BUY_DELAY        = 3
CODE_DELAY       = 5
CODE_ATTEMPTS    = 4
CODE_ROUNDS      = 2

# ─── Справочник стран ────────────────────────────────────────
COUNTRIES = [
    {"code": "RU", "name": "Россия", "flag": "🇷🇺", "phone_code": "+7"},
    {"code": "US", "name": "США",    "flag": "🇺🇸", "phone_code": "+1"},
    # ...
]
```

### 3. Запуск

```bash
python run.py
```

При первом запуске бот:
1. ✅ Проверит/создаст ключ шифрования
2. ✅ Создаст таблицы и выполнит миграции
3. ✅ Зашифрует legacy plaintext-session
4. ✅ Восстановит аккаунты из `data/accounts_backup.json`
5. ✅ Отправит всем админам уведомление о запуске

---

## 📂 Структура проекта

```
tg-account-manager/
│
├── run.py                    # 🚀 точка входа
├── config.py                 # ⚙️ конфигурация (не коммитить!)
├── database.py               # 🗄 модели + шифрование + backup
│
├── data/                     # 📁 данные (создаётся автоматически)
│   ├── storage.db            #    SQLite fallback
│   ├── secret.key            #    🔐 ключ Fernet
│   └── accounts_backup.json  #    💾 расшифрованный бэкап
│
├── bot/
│   ├── __init__.py           #    фабрика бота
│   ├── handlers/
│   │   ├── __init__.py       #    регистрация всех хендлеров
│   │   ├── _state.py         #    общий Context
│   │   ├── menu.py           #    /start + главное меню
│   │   ├── storage.py        #    хранилище по странам
│   │   ├── add_account.py    #    FSM добавления
│   │   ├── clean.py          #    очистка сессий/чатов
│   │   ├── lzt_buy.py        #    покупка LZT
│   │   ├── lzt_logs.py       #    LZT-логи
│   │   └── reports.py        #    отчёты и экспорт
│   │
│   ├── middlewares.py        #    AdminMiddleware
│   ├── decorators.py         #    @admin_required
│   ├── keyboards.py          #    инлайн-клавиатуры
│   ├── helpers.py            #    форматирование, CSV
│   ├── lzt.py                #    клиент LZT Market
│   └── telethon_ops.py       #    Telethon-операции
│
└── requirements.txt
```

---

## 🔐 Безопасность

> ⚠️ **Внимание:** проект работает с реальными Telegram-аккаунтами. Относитесь к файлам и токенам с осторожностью.

| Что | Как защищено |
|---|---|
| **Session / 2FA в БД** | 🔒 Шифрование Fernet на уровне ORM (`EncryptedText`) |
| **Ключ шифрования** | 📁 Файл `data/secret.key` (chmod 600) или env-переменная |
| **Резервные копии** | 💾 `data/accounts_backup.json` — **расшифрованный** файл, требует защиты ОС |
| **Доступ к боту** | 🛡 Двойная проверка: `AdminMiddleware` + `@admin_required` |
| **Пароли 2FA** | 🔑 Шифруются в БД, не передаются в логи |
| **LZT-токен** | 🚫 Не выводится в логи (`_safe_url`) |

**Рекомендации:**
- Не коммитьте `config.py`, `data/` в git
- Сделайте `chmod 600 config.py data/secret.key data/accounts_backup.json`
- Держите бота за NAT/прокси или на приватном сервере
- Регулярно бэкапьте `data/secret.key` — **без него session не расшифруются**

---

## 🔄 Как работает автоочистка

```
┌─────────────────────────────────────────────────────────┐
│ 1. Аккаунт добавлен/куплен                              │
│    ↓                                                    │
│ 2. Планировщик ставит задачу через AUTO_CLEAN_DELAY     │
│    ↓                                                    │
│ 3. По таймауту: ResetAuthorizations + clear_all_dialogs │
│    ↓                                                    │
│ 4. Успех → status="cleaned", счётчик +1                 │
│    Заморозка → status="dead"                            │
│    Ошибка → статус не меняется                          │
└─────────────────────────────────────────────────────────┘
```

При рестарте бота все «просроченные» задачи перезапускаются с разносом в 60 сек, чтобы не бить Telegram пачкой.

---

## 🧪 Проверка и статусы

| Статус | Значение |
|---|---|
| `uncleaned` | ⚠️ Только добавлен, ждёт очистки |
| `cleaned` | 🧹 Чужие сессии убиты, чаты удалены |
| `dead` | ❌ Забанен / деактивирован / заморожен |

Проверка (`🔍 ПРОВЕРКА АККАУНТОВ`) различает три состояния и **не считает мёртвым** аккаунт, который не удалось проверить из-за таймаута или FloodWait — статус не изменится, а аккаунт попадёт в отдельный счётчик `❔`.

---

## 🛒 Покупка LZT: как это работает

```
1. Запрос баланса «Для покупки аккаунтов» (balance_id)
   ↓
2. Поиск лотов: /telegram?pmax=X&order_by=price_to_up&spam=no
   ↓
3. Для каждого лота — GET /{item_id} (карточка)
   ↓
4. Проверка telegram_spam_block → пропуск если заблокирован
   ↓
5. POST /{item_id}/fast-buy {price, balance_id}
   ↓
6. Вход по коду из LZT (GET /{item_id}/telegram-login-code)
   ↓
7. Автоустановка PASSWORD_2FA из config.py
   ↓
8. Привязка LOGIN_EMAIL через IMAP (код читается из ящика)
   ↓
9. Сохранение в БД + автоочистка через AUTO_CLEAN_DELAY
```

Все покупки регистрируются в `lzt_purchases` **до** сохранения аккаунта — даже если что-то упадёт, лот появится в «📥 LZT-ЛОГИ» и вход можно будет повторить.

## 🐛 Известные ограничения

- **Telegram FRESHRESET** — сброс сессий недоступен для аккаунтов, авторизованных менее 24 часов назад
- **FloodWait** — при массовых операциях Telegram временно блокирует запросы (обрабатывается автоматически)
- **LZT JSON-разбор** — некоторые ответы маркетплейса требуют `curl_cffi` в обход стандартного парсера `LOLZTEAM`
- **IMAP-код** — привязка email требует настроенного ящика с `FROM "telegram"` фильтром

---

## 🤝 Contributing

PR и issue приветствуются. Перед отправкой PR убедитесь, что:

- [ ] Код не содержит `print` — только `logger`
- [ ] Новые зависимости добавлены в `requirements.txt`
- [ ] Нет хардкода токенов/секретов
- [ ] Соблюдён стиль существующего кода (docstrings, типы)


<p align="center">
  <b>⚡ Сделано для тех, кто управляет аккаунтами профессионально</b><br>
  <sub>Telegram Account Manager · 2026</sub>
</p>

<p align="center">
  <a href="#-telegram-account-manager">⬆ Наверх</a>
</p>