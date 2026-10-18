import json
import logging
import os
import traceback
from contextlib import contextmanager
from datetime import datetime, date
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    BigInteger, Column, DateTime, Integer, String, Text,
    create_engine, text, func,
)
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker
from sqlalchemy.types import TypeDecorator

try:
    from config import DB_URL, SESSION_ENCRYPTION_KEY
except Exception:
    DB_URL = ""
    SESSION_ENCRYPTION_KEY = ""

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
os.makedirs(_DATA_DIR, exist_ok=True)

BACKUP_FILE = os.path.join(_DATA_DIR, "accounts_backup.json")
KEY_FILE = os.path.join(_DATA_DIR, "secret.key")
FALLBACK_DB_PATH = os.path.join(_DATA_DIR, "storage.db")
FALLBACK_SQLITE_URL = f"sqlite:///{FALLBACK_DB_PATH}"


def _to_sync(url: str) -> str:
    """
    Преобразует async-URL БД в синхронный для SQLAlchemy.

    Заменяет "+asyncpg" на "+psycopg2" и добавляет явный драйвер
    для postgresql://.

    :param url: строка подключения.
    :return: синхронный URL.
    """
    if not url:
        return FALLBACK_SQLITE_URL
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg2")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


PRIMARY_DB_URL = _to_sync(DB_URL)

engine = None
Session = None
USING_FALLBACK = False


def _safe_url(url: str) -> str:
    """
    Убирает логин и пароль из строки подключения для логов.

    :param url: строка подключения.
    :return: строка без user:pass.
    """
    if "@" in url:
        return url.split("@", 1)[-1]
    return url


def _try_connect(url: str, timeout: int = 5):
    """
    Пробует создать engine и проверить соединение с БД.

    Для PostgreSQL использует connect_timeout, чтобы не ждать долго
    при недоступном хосте. Выполняет SELECT 1 для проверки.

    :param url: строка подключения.
    :param timeout: таймаут подключения в секундах.
    :return: engine при успехе, иначе None.
    """
    try:
        kwargs = {
            "pool_pre_ping": True,
            "pool_recycle": 280,
            "future": True,
        }
        if url.startswith("postgresql"):
            kwargs["connect_args"] = {"connect_timeout": timeout}
        eng = create_engine(url, **kwargs)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return eng
    except Exception as e:
        logger.warning(
            f"[db] Не удалось подключиться к {_safe_url(url)}: "
            f"{type(e).__name__}: {e}"
        )
        return None


def _init_engine() -> None:
    """
    Инициализирует engine: пробует основную БД (PostgreSQL),
    при неудаче — переключается на локальную SQLite.

    Устанавливает глобальные engine, Session и флаг USING_FALLBACK.
    Логирует, какая БД реально используется.
    """
    global engine, Session, USING_FALLBACK

    if DB_URL and not DB_URL.startswith("sqlite"):
        logger.info("[db] Проверяю подключение к основной БД (PostgreSQL)...")
        eng = _try_connect(PRIMARY_DB_URL)
    else:
        eng = None

    if eng is not None:
        engine = eng
        USING_FALLBACK = False
        logger.info(f"✅ Основная БД доступна: {_safe_url(PRIMARY_DB_URL)}")
    else:
        if DB_URL and not DB_URL.startswith("sqlite"):
            logger.warning(
                "⚠️ PostgreSQL недоступен — переключаюсь на локальную SQLite"
            )
        else:
            logger.info("[db] DB_URL не задан или SQLite — использую локальную БД")
        os.makedirs(os.path.dirname(FALLBACK_DB_PATH), exist_ok=True)
        engine = create_engine(
            FALLBACK_SQLITE_URL,
            pool_pre_ping=True,
            pool_recycle=280,
            future=True,
        )
        USING_FALLBACK = True
        logger.info(f"✅ Локальная БД: {FALLBACK_SQLITE_URL}")

    Session = scoped_session(
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    )


_init_engine()

Base = declarative_base()


@contextmanager
def scope():
    """
    Контекстный менеджер для работы с сессией SQLAlchemy.

    Открывает сессию, коммитит при успехе, откатывает при исключении
    и всегда удаляет сессию из scoped-сессии.

    :yield: объект Session.
    """
    s = Session()
    try:
        yield s
        s.commit()
    except Exception as e:
        logger.error(f"[db.scope] {type(e).__name__}: {e}")
        logger.debug(traceback.format_exc())
        s.rollback()
        raise
    finally:
        Session.remove()


_fernet = None
FERNET_MARKER = "gAAAAA"


def _get_fernet() -> Fernet:
    """
    Возвращает Fernet-инстанс для шифрования.

    Ключ ищется в порядке: SESSION_ENCRYPTION_KEY из config.py,
    файл secret.key, генерация нового. Кэширует инстанс.

    :return: объект Fernet.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    key = None

    if SESSION_ENCRYPTION_KEY:
        try:
            key = SESSION_ENCRYPTION_KEY.encode() \
                if isinstance(SESSION_ENCRYPTION_KEY, str) \
                else SESSION_ENCRYPTION_KEY
        except Exception:
            key = None

    if not key and os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "rb") as f:
                key = f.read().strip()
        except Exception as e:
            logger.error(f"❌ Не удалось прочитать {KEY_FILE}: {e}")

    if not key:
        key = Fernet.generate_key()
        try:
            os.makedirs(os.path.dirname(KEY_FILE) or ".", exist_ok=True)
            with open(KEY_FILE, "wb") as f:
                f.write(key)
            logger.warning(
                "🔐 Сгенерирован новый ключ шифрования.\n"
                f"   Файл: {KEY_FILE}\n"
                "   ⚠️ СОХРАНИ ЕГО! Без ключа session не расшифруются."
            )
        except Exception as e:
            logger.error(f"❌ Не удалось сохранить ключ: {e}")

    try:
        _fernet = Fernet(key)
    except Exception as e:
        logger.error(f"❌ Неверный ключ шифрования: {e}")
        raise
    return _fernet


class EncryptedText(TypeDecorator):
    """
    SQLAlchemy-тип для прозрачного шифрования текстовых колонок.

    При записи — шифрует через Fernet. При чтении — расшифровывает.
    Legacy plaintext (без префикса FERNET_MARKER) возвращается как есть.
    Если токен повреждён — возвращает пустую строку, чтобы Telethon
    не падал с ValueError.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        s = str(value)
        if s.startswith(FERNET_MARKER):
            return s
        return _get_fernet().encrypt(s.encode("utf-8")).decode("ascii")

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return value
        s = str(value)

        if not s.startswith(FERNET_MARKER):
            return s

        try:
            return _get_fernet().decrypt(s.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.error(
                "[decrypt] ❌ InvalidToken — ключ шифрования НЕ подходит к "
                "этой session.\n"
                "   Скорее всего secret.key был пересоздан.\n"
                "   Восстанови session из accounts_backup.json."
            )
            return ""
        except Exception as e:
            logger.error(f"[decrypt] {type(e).__name__}: {e}")
            return ""


class Account(Base):
    """
    Модель аккаунта Telegram.

    Хранит номер, название, зашифрованную session, 2FA-пароль,
    статус (uncleaned / cleaned / dead), метаданные (цена, источник,
    LZT-лот, флаг избранного, счётчик фейлов).
    """
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(32), unique=True, index=True, nullable=False)
    name = Column(String(64), default="")
    session = Column(EncryptedText, nullable=False)
    # EncryptedText: расшифрует legacy Fernet-значения при чтении и
    # шифрует plaintext при записи. Миграция ALTER TABLE не нужна —
    # в БД тип колонки остаётся TEXT.
    two_fa = Column(EncryptedText, default="")
    registered_at = Column(DateTime, default=datetime.now)
    status = Column(String(16), default="uncleaned")

    last_checked_at = Column(DateTime, nullable=True)
    last_cleaned_at = Column(DateTime, nullable=True)
    country_code = Column(String(8), default="")
    price = Column(Integer, default=0)
    bought_from = Column(String(16), default="manual")
    lzt_item_id = Column(Integer, nullable=True)
    health_fail_count = Column(Integer, default=0)
    deleted_at = Column(DateTime, nullable=True)
    favorite = Column(Integer, default=0)

    def __repr__(self) -> str:
        return f"<Account(id={self.id}, phone={self.phone}, status={self.status})>"


class ActionLog(Base):
    """
    Модель лога действий пользователей.

    Хранит user_id, account_id (если применимо), тип действия,
    детали и время.
    """
    __tablename__ = "actions_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    account_id = Column(Integer, nullable=True, index=True)
    action = Column(String(32), index=True)
    details = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class DailyStat(Base):
    """
    Модель ежедневной статистики.

    Первичный ключ — дата (ISO-строка). Счётчики: added, cleaned,
    dead, bought_lzt, spent_rub.
    """
    __tablename__ = "daily_stats"

    day = Column(String(16), primary_key=True)
    added = Column(Integer, default=0)
    cleaned = Column(Integer, default=0)
    dead = Column(Integer, default=0)
    bought_lzt = Column(Integer, default=0)
    spent_rub = Column(Integer, default=0)


class LZTPurchase(Base):
    """
    Модель покупки с LZT Market.

    Хранит ID лота, номер, цену, страну, статус
    (bought / logged_in / failed), текст ошибки и дату покупки.
    """
    __tablename__ = "lzt_purchases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, index=True, unique=True)
    phone = Column(String(32), index=True, nullable=True)
    price = Column(Integer, default=0)
    country = Column(String(64), default="")
    status = Column(String(16), default="bought")
    error = Column(Text, default="")
    bought_at = Column(DateTime, default=datetime.now, index=True)


def normalize_phone(phone: str) -> str:
    """
    Приводит номер к виду с ведущим плюсом.

    :param phone: номер телефона.
    :return: номер с "+" в начале.
    """
    if not phone:
        return phone
    phone = phone.strip()
    return phone if phone.startswith("+") else "+" + phone


def _today() -> str:
    """
    Возвращает сегодняшнюю дату в ISO-формате.

    :return: строка вида "2026-10-13".
    """
    return date.today().isoformat()


def _account_to_dict(acc: Account) -> dict:
    """
    Преобразует объект Account в словарь для бэкапа.

    Session и two_fa сохраняются в расшифрованном виде (ORM уже
    расшифровал при чтении).

    :param acc: объект Account.
    :return: словарь с полями аккаунта.
    """
    return {
        "phone": acc.phone,
        "name": acc.name or acc.phone,
        "session": acc.session,
        "two_fa": acc.two_fa or "",
        "registered_at": acc.registered_at.isoformat() if acc.registered_at else None,
        "status": acc.status,
        "country_code": acc.country_code or "",
        "price": acc.price or 0,
        "bought_from": acc.bought_from or "manual",
        "lzt_item_id": acc.lzt_item_id,
        "last_checked_at": acc.last_checked_at.isoformat() if acc.last_checked_at else None,
        "last_cleaned_at": acc.last_cleaned_at.isoformat() if acc.last_cleaned_at else None,
        "favorite": acc.favorite or 0,
    }


def save_backup() -> None:
    """
    Сохраняет JSON-бэкап всех активных аккаунтов.

    Записывает расшифрованные session и two_fa в файл
    BACKUP_FILE. Использует атомарную замену через .tmp.
    """
    try:
        with scope() as s:
            accounts = s.query(Account).filter(Account.deleted_at.is_(None)).all()
            data = [_account_to_dict(a) for a in accounts]
        os.makedirs(os.path.dirname(BACKUP_FILE) or ".", exist_ok=True)
        tmp = BACKUP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BACKUP_FILE)
        logger.info(f"💾 Бэкап сохранён ({len(data)} аккаунтов)")
    except Exception as e:
        logger.error(f"❌ Ошибка бэкапа: {type(e).__name__}: {e}")
        logger.debug(traceback.format_exc())


def load_backup() -> list:
    """
    Читает бэкап из файла.

    Пробует сначала как обычный JSON, потом как Fernet-шифр
    (для совместимости со старыми зашифрованными бэкапами).

    :return: список словарей-аккаунтов или пустой список.
    """
    if not os.path.exists(BACKUP_FILE):
        return []
    try:
        with open(BACKUP_FILE, "rb") as f:
            raw = f.read()

        if not raw.strip():
            return []

        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

        try:
            decrypted = _get_fernet().decrypt(raw).decode("utf-8")
            data = json.loads(decrypted)
            logger.warning(
                "[backup] Обнаружен зашифрованный бэкап — читаю через Fernet."
            )
            return data
        except (InvalidToken, Exception):
            pass

        logger.error("❌ Бэкап не читается ни как JSON, ни как Fernet")
        return []
    except Exception as e:
        logger.error(f"❌ Ошибка чтения бэкапа: {e}")
        return []


def restore_from_backup() -> int:
    """
    Дополняет БД аккаунтами из бэкапа.

    Восстанавливает только те номера, которых нет в БД (включая
    архивные). Существующие аккаунты не трогает.

    :return: количество восстановленных аккаунтов.
    """
    data = load_backup()
    if not data:
        return 0

    restored = 0
    try:
        with scope() as s:
            existing_phones = {row[0] for row in s.query(Account.phone).all()}
            logger.info(f"[restore] В БД: {len(existing_phones)}, "
                        f"в бэкапе: {len(data)}")

            for item in data:
                try:
                    phone = normalize_phone(item.get("phone", ""))
                    session = item.get("session", "")
                    if not phone or not session:
                        continue

                    if phone in existing_phones:
                        continue

                    reg = item.get("registered_at")
                    try:
                        reg_dt = (datetime.fromisoformat(reg)
                                  if reg else datetime.now())
                    except Exception:
                        reg_dt = datetime.now()

                    s.add(Account(
                        phone=phone,
                        name=item.get("name") or phone,
                        session=session,
                        two_fa=item.get("two_fa", "") or "",
                        registered_at=reg_dt,
                        status=item.get("status", "uncleaned") or "uncleaned",
                        country_code=item.get("country_code", "") or "",
                        price=item.get("price", 0) or 0,
                        bought_from=item.get("bought_from", "manual") or "manual",
                        lzt_item_id=item.get("lzt_item_id"),
                        favorite=item.get("favorite", 0) or 0,
                    ))
                    existing_phones.add(phone)
                    restored += 1
                    logger.info(f"[restore] + {phone}")
                except Exception as e:
                    logger.warning(f"⚠️ Запись {item.get('phone')} "
                                   f"пропущена: {e}")
    except Exception as e:
        logger.error(f"❌ restore_from_backup: {e}")
        return 0

    if restored:
        logger.info(f"♻️ Восстановлено из бэкапа: {restored}")
    else:
        logger.info("[restore] Нечего восстанавливать")
    return restored


_ACCOUNT_COLUMNS = [
    ("name", "VARCHAR(64) DEFAULT ''"),
    ("last_checked_at", "TIMESTAMP NULL"),
    ("last_cleaned_at", "TIMESTAMP NULL"),
    ("country_code", "VARCHAR(8) DEFAULT ''"),
    ("price", "INTEGER DEFAULT 0"),
    ("bought_from", "VARCHAR(16) DEFAULT 'manual'"),
    ("lzt_item_id", "INTEGER NULL"),
    ("health_fail_count", "INTEGER DEFAULT 0"),
    ("deleted_at", "TIMESTAMP NULL"),
    ("favorite", "INTEGER DEFAULT 0"),
]


def _migrate() -> None:
    """
    Добавляет недостающие колонки в таблицу accounts.

    Для каждой колонки из _ACCOUNT_COLUMNS пытается выполнить
    ALTER TABLE ADD COLUMN. Ошибки (колонка уже есть) игнорирует.
    Также заполняет дефолтные значения у существующих записей.
    """
    for col, ddl in _ACCOUNT_COLUMNS:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {col} {ddl}"))
                logger.info(f"🔧 Добавлена колонка accounts.{col}")
        except Exception:
            pass

    try:
        with scope() as s:
            for acc in s.query(Account).all():
                if not acc.name:
                    acc.name = acc.phone
                if acc.bought_from is None:
                    acc.bought_from = "manual"
                if acc.price is None:
                    acc.price = 0
                if acc.health_fail_count is None:
                    acc.health_fail_count = 0
                if acc.favorite is None:
                    acc.favorite = 0
    except Exception as e:
        logger.warning(f"[_migrate] fill: {e}")


def _migrate_encrypt_sessions() -> None:
    """
    Шифрует plaintext session_string у существующих записей.

    Проходит по всем строкам accounts, находит те, где session
    не начинается с FERNET_MARKER, и шифрует их на месте.
    """
    try:
        f = _get_fernet()

        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT id, session FROM accounts WHERE session IS NOT NULL")
            ).fetchall()

        to_encrypt = []
        for row_id, raw in rows:
            if raw and isinstance(raw, str) and not raw.startswith(FERNET_MARKER):
                to_encrypt.append((row_id, raw))

        if not to_encrypt:
            return

        with engine.begin() as conn:
            for row_id, raw in to_encrypt:
                enc = f.encrypt(raw.encode("utf-8")).decode("ascii")
                conn.execute(
                    text("UPDATE accounts SET session = :s WHERE id = :id"),
                    {"s": enc, "id": row_id}
                )

        logger.info(f"🔐 Зашифровано session_string: {len(to_encrypt)}")
    except Exception as e:
        logger.error(f"❌ _migrate_encrypt_sessions: {type(e).__name__}: {e}")
        logger.debug(traceback.format_exc())


def init_db() -> None:
    """
    Инициализирует БД: создаёт таблицы, прогоняет миграции,
    шифрует legacy session, восстанавливает из бэкапа.

    Вызывается один раз при старте бота.
    """
    try:
        _get_fernet()
    except Exception as e:
        logger.exception(f"❌ Не удалось инициализировать шифрование: {e}")
        raise

    try:
        Base.metadata.create_all(engine)
        logger.info("✅ Таблицы проверены/созданы")
    except Exception as e:
        logger.exception(f"❌ create_all: {e}")
        raise

    _migrate()
    _migrate_encrypt_sessions()

    restored = restore_from_backup()
    if restored == 0:
        save_backup()


def add_account(phone: str, session_str: str, two_fa: str = "",
                name: str = "", country_code: str = "", price: int = 0,
                bought_from: str = "manual",
                lzt_item_id: Optional[int] = None) -> bool:
    """
    Добавляет или обновляет аккаунт в БД.

    Если аккаунт с таким номером уже есть — обновляет session,
    2FA и метаданные, сбрасывает статус на uncleaned. Иначе создаёт
    новый. В конце сохраняет бэкап.

    :param phone: номер телефона.
    :param session_str: строка сессии.
    :param two_fa: пароль 2FA.
    :param name: название аккаунта.
    :param country_code: код страны.
    :param price: цена.
    :param bought_from: источник (manual / lzt / import).
    :param lzt_item_id: ID лота LZT.
    :return: True при успехе.
    """
    phone = normalize_phone(phone)
    logger.info(f"[db.add_account] {phone} name={name!r} from={bought_from}")
    try:
        with scope() as s:
            acc = s.query(Account).filter(Account.phone == phone).first()
            if acc:
                acc.session = session_str
                acc.two_fa = two_fa
                acc.status = "uncleaned"
                acc.registered_at = datetime.now()
                acc.deleted_at = None
                acc.health_fail_count = 0
                if not acc.name:
                    acc.name = name or phone
                if country_code:
                    acc.country_code = country_code
                if price:
                    acc.price = price
                if bought_from:
                    acc.bought_from = bought_from
                if lzt_item_id is not None:
                    acc.lzt_item_id = lzt_item_id
            else:
                s.add(Account(
                    phone=phone,
                    name=name or phone,
                    session=session_str,
                    two_fa=two_fa,
                    status="uncleaned",
                    registered_at=datetime.now(),
                    country_code=country_code or "",
                    price=price or 0,
                    bought_from=bought_from or "manual",
                    lzt_item_id=lzt_item_id,
                    health_fail_count=0,
                    favorite=0,
                ))
    except Exception as e:
        logger.error(f"[db.add_account] {type(e).__name__}: {e}")
        logger.debug(traceback.format_exc())
        raise
    save_backup()
    return True


def get_accounts(include_archived: bool = False):
    """
    Возвращает все аккаунты, отсортированные по избранному и id.

    :param include_archived: включать ли архивные (deleted_at != None).
    :return: список объектов Account.
    """
    with scope() as s:
        q = s.query(Account)
        if not include_archived:
            q = q.filter(Account.deleted_at.is_(None))
        return q.order_by(
            Account.favorite.desc(),
            Account.id.desc(),
        ).all()


def get_favorites():
    """
    Возвращает только избранные аккаунты.

    :return: список объектов Account.
    """
    with scope() as s:
        return (s.query(Account)
                .filter(Account.deleted_at.is_(None))
                .filter(Account.favorite == 1)
                .order_by(Account.id.desc())
                .all())


def get_archived_accounts():
    """
    Возвращает архивные (удалённые мягко) аккаунты.

    :return: список объектов Account.
    """
    with scope() as s:
        return (s.query(Account)
                .filter(Account.deleted_at.isnot(None))
                .order_by(Account.deleted_at.desc())
                .all())


def get_account(account_id: int):
    """
    Возвращает аккаунт по ID.

    :param account_id: ID аккаунта.
    :return: объект Account или None.
    """
    with scope() as s:
        return s.get(Account, account_id)


def get_account_by_phone(phone: str):
    """
    Возвращает активный аккаунт по номеру телефона.

    :param phone: номер телефона.
    :return: объект Account или None.
    """
    phone = normalize_phone(phone)
    with scope() as s:
        return (s.query(Account)
                .filter(Account.phone == phone)
                .filter(Account.deleted_at.is_(None))
                .first())


def delete_account(account_id: int) -> bool:
    """
    Полностью удаляет аккаунт из БД и из бэкапа.

    Используется, когда аккаунт удаляет сам бот (кнопка "Удалить").
    При следующем старте бота аккаунт НЕ вернётся.

    :param account_id: ID аккаунта.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            s.delete(acc)
    save_backup()
    return True


def delete_account_soft(account_id: int) -> bool:
    """
    Мягко удаляет аккаунт: только из БД, бэкап не трогает.

    При следующем старте бота аккаунт восстановится из бэкапа.
    Используется для ручного удаления через SQL/скрипты, когда
    нужно временно скрыть аккаунт.

    :param account_id: ID аккаунта.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            s.delete(acc)
    return True


def archive_account(account_id: int) -> bool:
    """
    Мягко удаляет аккаунт: ставит deleted_at = now.

    Бэкап не трогает — аккаунт остаётся в файле.

    :param account_id: ID аккаунта.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.deleted_at = datetime.now()
    return True


def restore_account(account_id: int) -> bool:
    """
    Восстанавливает аккаунт из архива.

    :param account_id: ID аккаунта.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.deleted_at = None
    save_backup()
    return True


def update_account_status(account_id: int, status: str) -> bool:
    """
    Обновляет статус аккаунта.

    При "cleaned" — ставит last_cleaned_at и сбрасывает счётчик
    фейлов. При "dead" — увеличивает счётчик фейлов.

    :param account_id: ID аккаунта.
    :param status: новый статус.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.status = status
            if status == "cleaned":
                acc.last_cleaned_at = datetime.now()
                acc.health_fail_count = 0
            elif status == "dead":
                acc.health_fail_count = (acc.health_fail_count or 0) + 1
    save_backup()
    return True


def update_account_two_fa(account_id: int, two_fa: str) -> bool:
    """
    Обновляет 2FA-пароль аккаунта.

    :param account_id: ID аккаунта.
    :param two_fa: новый пароль.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.two_fa = two_fa
    save_backup()
    return True


def update_account_name(account_id: int, name: str) -> bool:
    """
    Обновляет название аккаунта.

    Если пустая строка — ставит номер телефона.

    :param account_id: ID аккаунта.
    :param name: новое название.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.name = (name or "").strip() or acc.phone
    save_backup()
    return True


def toggle_favorite(account_id: int) -> bool:
    """
    Переключает флаг избранного.

    :param account_id: ID аккаунта.
    :return: True, если теперь в избранном; False, если убран.
    """
    result = False
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            new_val = 0 if (acc.favorite or 0) == 1 else 1
            acc.favorite = new_val
            result = new_val == 1
    save_backup()
    return result


def set_favorite(account_id: int, value: bool) -> bool:
    """
    Устанавливает флаг избранного.

    :param account_id: ID аккаунта.
    :param value: True — в избранное, False — убрать.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.favorite = 1 if value else 0
    save_backup()
    return True


def is_favorite(account_id: int) -> bool:
    """
    Проверяет, в избранном ли аккаунт.

    :param account_id: ID аккаунта.
    :return: True, если в избранном.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        return bool(acc and (acc.favorite or 0) == 1)


def update_last_checked(account_id: int, alive: bool) -> bool:
    """
    Обновляет дату последней проверки аккаунта.

    При alive=True сбрасывает счётчик фейлов, при False — увеличивает.

    :param account_id: ID аккаунта.
    :param alive: жив ли аккаунт.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.last_checked_at = datetime.now()
            if alive:
                acc.health_fail_count = 0
            else:
                acc.health_fail_count = (acc.health_fail_count or 0) + 1
    return True


def mark_cleaned(account_id: int) -> bool:
    """
    Помечает аккаунт как очищенный.

    :param account_id: ID аккаунта.
    :return: True.
    """
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.last_cleaned_at = datetime.now()
            acc.status = "cleaned"
            acc.health_fail_count = 0
    save_backup()
    return True


def get_accounts_stats() -> dict:
    """
    Собирает статистику по аккаунтам.

    :return: словарь с ключами total, cleaned, uncleaned, dead,
        favorites, archived.
    """
    with scope() as s:
        base = s.query(Account).filter(Account.deleted_at.is_(None))
        total = base.count()
        cleaned = base.filter(Account.status == "cleaned").count()
        uncleaned = base.filter(Account.status == "uncleaned").count()
        dead = base.filter(Account.status == "dead").count()
        favorites = base.filter(Account.favorite == 1).count()
        archived = (s.query(Account)
                    .filter(Account.deleted_at.isnot(None)).count())
    return {
        "total": total,
        "cleaned": cleaned,
        "uncleaned": uncleaned,
        "dead": dead,
        "favorites": favorites,
        "archived": archived,
    }


def get_problem_accounts(threshold: int = 3):
    """
    Возвращает аккаунты с health_fail_count >= threshold.

    :param threshold: порог фейлов.
    :return: список объектов Account.
    """
    with scope() as s:
        return (s.query(Account)
                .filter(Account.deleted_at.is_(None))
                .filter(Account.health_fail_count >= threshold)
                .all())


def log_action(user_id: int, action: str, details: str = "",
               account_id: Optional[int] = None) -> bool:
    """
    Записывает действие пользователя в actions_log.

    :param user_id: Telegram ID пользователя.
    :param action: тип действия.
    :param details: детали (обрезаются до 500 символов).
    :param account_id: ID аккаунта (если применимо).
    :return: True при успехе.
    """
    try:
        with scope() as s:
            s.add(ActionLog(
                user_id=user_id, action=action,
                details=(details or "")[:500],
                account_id=account_id,
                created_at=datetime.now(),
            ))
        return True
    except Exception as e:
        logger.error(f"[log_action] {type(e).__name__}: {e}")
        return False


def get_recent_actions(limit: int = 50):
    """
    Возвращает последние действия из лога.

    :param limit: сколько записей вернуть.
    :return: список объектов ActionLog.
    """
    with scope() as s:
        return (s.query(ActionLog)
                .order_by(ActionLog.created_at.desc())
                .limit(limit).all())


def get_account_actions(account_id: int, limit: int = 30):
    """
    Возвращает историю действий по одному аккаунту.

    :param account_id: ID аккаунта.
    :param limit: сколько записей вернуть.
    :return: список объектов ActionLog.
    """
    with scope() as s:
        return (s.query(ActionLog)
                .filter(ActionLog.account_id == account_id)
                .order_by(ActionLog.created_at.desc())
                .limit(limit).all())


def get_action_counts(account_id: int) -> dict:
    """
    Считает количество действий каждого типа для аккаунта.

    :param account_id: ID аккаунта.
    :return: словарь {action: count}.
    """
    with scope() as s:
        rows = (s.query(ActionLog.action, func.count(ActionLog.id))
                .filter(ActionLog.account_id == account_id)
                .group_by(ActionLog.action)
                .all())
    return {r[0]: r[1] for r in rows}


def _bump_today(field: str, amount: int = 1) -> None:
    """
    Увеличивает счётчик в daily_stats за сегодня.

    Создаёт запись, если её нет.

    :param field: имя поля (added, cleaned, dead, bought_lzt, spent_rub).
    :param amount: на сколько увеличить.
    """
    day = _today()
    with scope() as s:
        row = s.get(DailyStat, day)
        if row is None:
            row = DailyStat(day=day)
            s.add(row)
            s.flush()
        setattr(row, field, (getattr(row, field) or 0) + amount)


def stat_added(amount: int = 1) -> None:
    """
    Увеличивает счётчик добавленных аккаунтов за сегодня.

    :param amount: на сколько увеличить.
    """
    _bump_today("added", amount)


def stat_cleaned(amount: int = 1) -> None:
    """
    Увеличивает счётчик очищенных аккаунтов за сегодня.

    :param amount: на сколько увеличить.
    """
    _bump_today("cleaned", amount)


def stat_dead(amount: int = 1) -> None:
    """
    Увеличивает счётчик мёртвых аккаунтов за сегодня.

    :param amount: на сколько увеличить.
    """
    _bump_today("dead", amount)


def stat_bought_lzt(amount: int = 1, spent_rub: int = 0) -> None:
    """
    Увеличивает счётчики покупок LZT за сегодня.

    :param amount: сколько покупок.
    :param spent_rub: сколько потрачено рублей.
    """
    _bump_today("bought_lzt", amount)
    if spent_rub:
        _bump_today("spent_rub", spent_rub)


def get_daily_stats(days: int = 30):
    """
    Возвращает статистику за последние N дней.

    :param days: сколько дней вернуть.
    :return: список объектов DailyStat.
    """
    with scope() as s:
        return (s.query(DailyStat)
                .order_by(DailyStat.day.desc())
                .limit(days).all())


def get_totals_stats() -> dict:
    """
    Суммарная статистика за всё время.

    :return: словарь added, cleaned, dead, bought_lzt, spent_rub.
    """
    with scope() as s:
        added = s.query(func.coalesce(func.sum(DailyStat.added), 0)).scalar()
        cleaned = s.query(func.coalesce(func.sum(DailyStat.cleaned), 0)).scalar()
        dead = s.query(func.coalesce(func.sum(DailyStat.dead), 0)).scalar()
        bought = s.query(func.coalesce(func.sum(DailyStat.bought_lzt), 0)).scalar()
        spent = s.query(func.coalesce(func.sum(DailyStat.spent_rub), 0)).scalar()
    return {
        "added": int(added or 0),
        "cleaned": int(cleaned or 0),
        "dead": int(dead or 0),
        "bought_lzt": int(bought or 0),
        "spent_rub": int(spent or 0),
    }


def add_lzt_purchase(item_id: int, phone: Optional[str], price: int,
                     country: str = "", status: str = "bought",
                     error: str = "") -> int:
    """
    Добавляет или обновляет запись о покупке LZT.

    Если запись с таким item_id есть — обновляет, иначе создаёт.

    :param item_id: ID лота LZT.
    :param phone: номер телефона.
    :param price: цена.
    :param country: страна.
    :param status: статус (bought / logged_in / failed).
    :param error: текст ошибки.
    :return: ID записи.
    """
    with scope() as s:
        row = s.query(LZTPurchase).filter(LZTPurchase.item_id == item_id).first()
        if row:
            row.phone = phone
            row.price = price
            row.country = country
            row.status = status
            row.error = error
            s.flush()
            return row.id
        p = LZTPurchase(item_id=item_id, phone=phone, price=price,
                        country=country, status=status, error=error,
                        bought_at=datetime.now())
        s.add(p)
        s.flush()
        return p.id


def update_lzt_purchase(item_id: int, status: str,
                        phone: Optional[str] = None,
                        error: str = "") -> bool:
    """
    Обновляет запись о покупке LZT.

    :param item_id: ID лота.
    :param status: новый статус.
    :param phone: номер (если нужно обновить).
    :param error: текст ошибки.
    :return: True.
    """
    with scope() as s:
        row = s.query(LZTPurchase).filter(LZTPurchase.item_id == item_id).first()
        if row:
            row.status = status
            if phone:
                row.phone = phone
            row.error = error or ""
    return True


def get_lzt_purchase(item_id: int):
    """
    Возвращает запись о покупке LZT по item_id.

    :param item_id: ID лота.
    :return: объект LZTPurchase или None.
    """
    with scope() as s:
        return s.query(LZTPurchase).filter(LZTPurchase.item_id == item_id).first()


def get_lzt_purchases(status: Optional[str] = None, limit: int = 200):
    """
    Возвращает список покупок LZT с фильтром по статусу.

    :param status: статус для фильтра (или None — все).
    :param limit: максимум записей.
    :return: список объектов LZTPurchase.
    """
    with scope() as s:
        q = s.query(LZTPurchase)
        if status:
            q = q.filter(LZTPurchase.status == status)
        return q.order_by(LZTPurchase.bought_at.desc()).limit(limit).all()


def get_known_lzt_item_ids() -> set:
    """
    Возвращает множество всех известных item_id из lzt_purchases.

    :return: set[int].
    """
    with scope() as s:
        rows = s.query(LZTPurchase.item_id).all()
    return {r[0] for r in rows}


def get_lzt_stats() -> dict:
    """
    Статистика по LZT-покупкам.

    :return: словарь total, logged_in, failed, bought, spent_rub.
    """
    with scope() as s:
        total = s.query(LZTPurchase).count()
        logged_in = s.query(LZTPurchase).filter(
            LZTPurchase.status == "logged_in").count()
        failed = s.query(LZTPurchase).filter(
            LZTPurchase.status == "failed").count()
        bought = s.query(LZTPurchase).filter(
            LZTPurchase.status == "bought").count()
        spent = s.query(func.coalesce(func.sum(LZTPurchase.price), 0)).scalar()
    return {
        "total": total,
        "logged_in": logged_in,
        "failed": failed,
        "bought": bought,
        "spent_rub": int(spent or 0),
    }


def get_db_info() -> dict:
    """
    Возвращает информацию о текущем подключении к БД.

    Используется для отображения в стартовом уведомлении и
    для отладки на разных машинах (ПК / телефон).

    :return: словарь с ключами url, fallback, engine_name.
    """
    url = _safe_url(str(engine.url)) if engine else "—"
    return {
        "url": url,
        "fallback": USING_FALLBACK,
        "engine_name": engine.name if engine else "—",
    }