# ==========================================
# Telegram Bot
# ==========================================
BOT_TOKEN = "123456789:AA..."
ADMIN_ID = 123456789
ADMINS = [123456789]

# ==========================================
# Telegram API (my.telegram.org)
# ==========================================
API_ID = 12345678
API_HASH = "0123456789abcdef0123456789abcdef"

# ==========================================
# PostgreSQL
# ==========================================
DB_URL = "postgresql+asyncpg://postgres:password@localhost:5432/AccountStorage"

PASSWORD_2FA = "your-2fa-password"

SESSION_TTL_DAYS = 365

# ==========================================
# LZT MARKET
# ==========================================
LZT_API_TOKEN = "eyJ..."
LZT_PROXY = None

BUY_DELAY = 3            # пауза между попытками покупки, сек
CODE_DELAY = 5           # пауза между опросами кода у LZT, сек
CODE_ATTEMPTS = 8        # сколько раз опрашивать код за один заход
CODE_ROUNDS = 3          # сколько раз перезапрашивать код у Telegram
MIN_PRICE = 0            # 0 = без минимума, ищем самый дешёвый
SPAM_FILTER = None       # None = любые; "no" = только без спам-блока
NO_LOTS_RETRIES = 40     # попытки поиска, если лотов нет
NO_LOTS_DELAY = 10       # пауза между такими поисками, сек

# ---------- WEBAPP ----------
WEBAPP_URL = "https://your-domain.com"
WEBAPP_PORT = 8081

# ==========================================
# СТРАНЫ
# ==========================================

COUNTRIES = [
    {"code": "RU", "name": "Россия", "flag": "🇷🇺", "phone_code": "+7"},
    {"code": "US", "name": "США", "flag": "🇺🇸", "phone_code": "+1"},
]