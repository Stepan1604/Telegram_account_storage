# Telegram Account Storage

**Телеграм-бот для централизованного хранения, учёта и автоматизированного обслуживания Telegram-аккаунтов.** Покупка номеров на LZT Market, авто-вход через Telethon, смена 2FA, чистка сессий, экспорт/импорт и резервное копирование — всё в одном боте с удобным интерфейсом.

---

## ✨ Возможности

| Категория | Функции |
|---|---|
| **Хранилище** | Добавление, просмотр, редактирование и удаление аккаунтов. Статусы: очищен / не очищен / мёртв. Избранное для быстрого доступа |
| **LZT Market** | Покупка аккаунтов напрямую через API, автоматическое получение кода входа, контроль баланса и расходов |
| **Авто-обслуживание** | Авто-вход через Telethon, смена облачного пароля 2FA, очистка сессий, выход из чатов и каналов, удаление истории переписок |
| **Проверка** | Массовая проверка аккаунтов на «жизнеспособность» (авторизация, бан, деактивация). Автоматическое удаление мёртвых аккаунтов |
| **Отчёты и статистика** | Ежедневная статистика (добавлено / очищено / куплено / потрачено), логи действий администраторов, история покупок |
| **Экспорт / Импорт** | Выгрузка аккаунтов в CSV и резервное копирование базы (StringSession) в JSON |
| **Уведомления** | Оповещение администраторов при старте бота и критических событиях |

---

## 🧰 Стек технологий

- **Python 3.10+**
- [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) — Telegram-бот
- [Telethon](https://github.com/LonamiWebs/Telethon) — работа с Telegram API от лица аккаунтов
- **SQLAlchemy + asyncpg** — ORM и асинхронный драйвер PostgreSQL
- [LOLZTEAM](https://github.com/asyncee/lolzteam-python) — интеграция с API LZT Market

---

## 🚀 Быстрый старт

### 1. Клонирование и установка

```bash
git clone https://github.com/Stepan1604/Telegram_account_storage.git
cd Telegram_account_storage

python -m venv .venv

# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Настройка

Все секреты хранятся в `config.py`, который **не попадает в репозиторий** (см. `.gitignore`).

Создайте файл конфигурации из шаблона:

```bash
# Windows:
copy config.py
# Linux / macOS:
cp config.py
```

Заполните обязательные параметры:

| Параметр | Где взять |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID`, `ADMINS` | Ваш Telegram user id |
| `API_ID` / `API_HASH` | [my.telegram.org](https://my.telegram.org) |
| `DB_URL` | Строка подключения PostgreSQL |
| `LZT_API_TOKEN` | Личный кабинет LZT Market → API |
| `PASSWORD_2FA` | Пароль облачного 2FA для аккаунтов |

### 3. База данных

Создайте базу и пользователя в PostgreSQL:

```sql
CREATE DATABASE "AccountStorage";
```

Таблицы создаются автоматически при старте (`database.init_db()`).

### 4. Запуск

```bash
python run.py
```

---

## 📁 Структура проекта

```

├── run.py                  # Точка входа, polling, уведомления админам
├── config.py               # Реальные секреты (игнорируется git)
├── database.py             # Модели SQLAlchemy, бэкап/восстановление
├── requirements.txt
└── bot/
    ├── handlers.py         # Команды, колбэки, покупка аккаунтов
    ├── keyboards.py        # Inline / Reply-клавиатуры
    └── lzt.py              # Интеграция LZT Market + Telethon
```

---

## 🔐 Безопасность

> ⚠️ **Важно!**

- Никогда не коммитьте `config.py`, `*.session`, `accounts_backup.json` и `.env`.
- Резервные копии содержат `StringSession` аккаунтов — храните их **вне git** и **в зашифрованном виде**.
- При утечке токенов: отзовите токен у [@BotFather](https://t.me/BotFather) / в кабинете LZT и замените его.
- Рекомендуется использовать отдельного пользователя PostgreSQL с ограниченными правами.


<p align="center">
  <i>Сделано с ❤️ для автоматизации работы с Telegram-аккаунтами</i>
</p>