"""Конфигурация Telegram Account Manager."""

# --- Telegram ---
BOT_TOKEN = "123456:ABC..."             # токен от @BotFather
ADMIN_ID = 123456789                    # главный админ
ADMINS = [123456789, 987654321]         # список админов
API_ID = 1234567                        # my.telegram.org
API_HASH = "abc123def456..."

# --- База данных ---
DB_URL = "postgresql+asyncpg://user:pass@host/db"  # fallback: data/storage.db
SESSION_ENCRYPTION_KEY = ""             # "" → data/secret.key

# --- 2FA и почта ---
PASSWORD_2FA = "MySecret2FA"            # авто-установка на новые аккаунты
LOGIN_EMAIL = "accounts@example.com"    # email для привязки
IMAP_HOST = "imap.gmail.com"
IMAP_USER = "accounts@example.com"
IMAP_PASSWORD = "app-password"

# --- LZT Market ---
LZT_API_TOKEN = "your_lzt_token"
LZT_PROXY = ""
MIN_PRICE = 0
SPAM_FILTER = "no"                      # "no" | "yes"

# --- Поведение ---
AUTO_CLEAN_DELAY = 24 * 60 * 60 + 10    # сек. после добавления
BUY_DELAY = 3
CODE_DELAY = 5
CODE_ATTEMPTS = 4
CODE_ROUNDS = 2

# --- Справочник стран ---
COUNTRIES = [
    {"code": "RU", "name": "Россия", "flag": "🇷🇺", "phone_code": "+7"},
    {"code": "US", "name": "США", "flag": "🇺🇸", "phone_code": "+1"},
]