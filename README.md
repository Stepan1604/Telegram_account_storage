# Telegram Account Storage

Телеграм-бот для хранения, учёта и обслуживания Telegram-аккаунтов:
покупка аккаунтов на LZT Market, авто-вход через Telethon, смена 2FA,
чистка сессий, экспорт/импорт, резервные копии.

## Стек

- Python 3.10+
- [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) — бот
- [Telethon](https://github.com/LonamiWebs/Telethon) — работа с аккаунтами
- SQLAlchemy + asyncpg — PostgreSQL
- [LOLZTEAM](https://github.com/asyncee/lolzteam-python) — API LZT Market

## Установка

```bash
git clone <repo-url>
cd Telegram_account_storage

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

pip install -r requirements.txt
```

## Настройка

Все секреты хранятся в `config.py`, который **не попадает в репозиторий**
(см. `.gitignore`). Создайте его на основе примера:

```bash
copy config.example.py config.py      # Windows
# cp config.example.py config.py      # Linux / macOS
```

Заполните в `config.py`:

| Параметр | Где взять |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID`, `ADMINS` | свой Telegram user id |
| `API_ID` / `API_HASH` | [my.telegram.org](https://my.telegram.org) |
| `DB_URL` | строка подключения PostgreSQL |
| `LZT_API_TOKEN` | личный кабинет LZT Market → API |
| `PASSWORD_2FA` | пароль облачного 2FA, который выставляется аккаунтам |

## База данных

Создайте БД и пользователя в PostgreSQL:

```sql
CREATE DATABASE "AccountStorage";
```

Таблицы создаются автоматически при старте (`database.init_db()`).

## Запуск

```bash
python run.py
```

## Структура

```
.
├── run.py                 # точка входа, polling, уведомления админам
├── config.example.py      # шаблон конфигурации (коммитится)
├── config.py              # реальные секреты (игнорируется git)
├── database.py            # модели SQLAlchemy, бэкап/восстановление
├── requirements.txt
└── bot/
    ├── handlers.py        # команды, колбэки, покупка аккаунтов
    ├── keyboards.py       # inline/reply-клавиатуры
    └── lzt.py             # интеграция LZT Market + Telethon
```

## Безопасность

- Никогда не коммитьте `config.py`, `*.session`, `accounts_backup.json` и `.env`.
- Бэкапы содержат StringSession аккаунтов — храните их вне git и в зашифрованном виде.
- При утечке токенов: отзовите токен у @BotFather / в кабинете LZT и замените его.