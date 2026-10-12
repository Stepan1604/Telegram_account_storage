# config.example.py
# Скопируй в config.py и подставь свои значения:
#   Linux/Mac:  cp config.example.py config.py
#   Windows:    copy config.example.py config.py

# ==========================================
# ТЕЛЕГРАМ-БОТ
# ==========================================

BOT_TOKEN = "123456789:AAAA-your-real-token-here"
ADMIN_ID = 0                    # твой Telegram ID
ADMINS = []                     # дополнительные админы (список ID)
HELPER_ID = None                # если None — используется ADMIN_ID

# ==========================================
# TELETHON API
# Получить: https://my.telegram.org/apps
# ==========================================

API_ID = 0
API_HASH = "your_api_hash_here"

# ==========================================
# БАЗА ДАННЫХ
# ==========================================

# SQLite (для теста):
DB_URL = "sqlite:///storage.db"

# PostgreSQL (для прода):
# DB_URL = "postgresql+asyncpg://postgres:password@localhost:5432/NumberShop"

# ==========================================
# 2FA И TTL
# ==========================================

PASSWORD_2FA = ""               # пароль, который ставится на новые аккаунты
SESSION_TTL_DAYS = 365          # 12 месяцев

# ==========================================
# LZT MARKET (опционально)
# ==========================================

LZT_API_TOKEN = ""
LZT_PROXY = None
BUY_DELAY = 3
CODE_DELAY = 5
CODE_ATTEMPTS = 8
CODE_ROUNDS = 3
MIN_PRICE = 0
SPAM_FILTER = None
NO_LOTS_RETRIES = 40
NO_LOTS_DELAY = 10

# ==========================================
# ШИФРОВАНИЕ SESSION
# ==========================================

# Оставь пустым "" — ключ сгенерируется автоматически в secret.key.
# ⚠️ СОХРАНИ secret.key! Без него все session не расшифруются.
SESSION_ENCRYPTION_KEY = ""

# ==========================================
# БАЗА СТРАН
# ==========================================

COUNTRIES = [
    {"code": "RU", "name": "Россия", "flag": "🇷🇺", "phone_code": "+7"},
    {"code": "US", "name": "США", "flag": "🇺🇸", "phone_code": "+1"},
    {"code": "GB", "name": "Великобритания", "flag": "🇬🇧", "phone_code": "+44"},
    {"code": "DE", "name": "Германия", "flag": "🇩🇪", "phone_code": "+49"},
    {"code": "FR", "name": "Франция", "flag": "🇫🇷", "phone_code": "+33"},
    {"code": "UA", "name": "Украина", "flag": "🇺🇦", "phone_code": "+380"},
    {"code": "KZ", "name": "Казахстан", "flag": "🇰🇿", "phone_code": "+7"},
    {"code": "TR", "name": "Турция", "flag": "🇹🇷", "phone_code": "+90"},
    {"code": "IN", "name": "Индия", "flag": "🇮🇳", "phone_code": "+91"},
    {"code": "ID", "name": "Индонезия", "flag": "🇮🇩", "phone_code": "+62"},
    # ... добавь остальные
]