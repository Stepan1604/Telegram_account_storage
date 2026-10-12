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
    DB_URL = "sqlite:///storage.db"
    SESSION_ENCRYPTION_KEY = ""

logger = logging.getLogger(__name__)

BACKUP_FILE = "data/accounts_backup.json"
KEY_FILE = "data/secret.key"


def _to_sync(url: str) -> str:
    if not url:
        return "sqlite:///storage.db"
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg2")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


engine = create_engine(
    _to_sync(DB_URL),
    pool_pre_ping=True,
    pool_recycle=280,
    future=True,
)

Session = scoped_session(
    sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
)

Base = declarative_base()


@contextmanager
def scope():
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


# ═══════════════════════════════════════════════════════════════════════════ #
#                          ШИФРОВАНИЕ SESSION                                 #
# ═══════════════════════════════════════════════════════════════════════════ #

_fernet = None
FERNET_MARKER = "gAAAAA"  # префикс всех Fernet-токенов


def _get_fernet() -> Fernet:
    """Возвращает Fernet-инстанс (кэширует)."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = None

    # 1) Из config.py
    if SESSION_ENCRYPTION_KEY:
        try:
            key = SESSION_ENCRYPTION_KEY.encode() \
                if isinstance(SESSION_ENCRYPTION_KEY, str) \
                else SESSION_ENCRYPTION_KEY
        except Exception:
            key = None

    # 2) Из файла secret.key
    if not key and os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "rb") as f:
                key = f.read().strip()
        except Exception as e:
            logger.error(f"❌ Не удалось прочитать {KEY_FILE}: {e}")

    # 3) Генерируем новый
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
    Колонка шифрует при записи и расшифровывает при чтении.
    Если значение НЕ начинается с FERNET_MARKER — это legacy plaintext,
    возвращаем как есть. Если начинается, но не расшифровывается —
    вернём "" (пусто), чтобы Telethon не падал с ValueError.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        s = str(value)
        # Защита от двойного шифрования
        if s.startswith(FERNET_MARKER):
            return s
        return _get_fernet().encrypt(s.encode("utf-8")).decode("ascii")

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return value
        s = str(value)

        # Не Fernet-токен → это legacy plaintext, отдаём как есть
        if not s.startswith(FERNET_MARKER):
            return s

        # Fernet-токен → расшифровываем
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


# ═══════════════════════════════════════════════════════════════════════════ #
#                                  МОДЕЛИ                                     #
# ═══════════════════════════════════════════════════════════════════════════ #

class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(32), unique=True, index=True, nullable=False)
    name = Column(String(64), default="")
    session = Column(EncryptedText, nullable=False)
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
    __tablename__ = "actions_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    account_id = Column(Integer, nullable=True, index=True)
    action = Column(String(32), index=True)
    details = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class DailyStat(Base):
    __tablename__ = "daily_stats"

    day = Column(String(16), primary_key=True)
    added = Column(Integer, default=0)
    cleaned = Column(Integer, default=0)
    dead = Column(Integer, default=0)
    bought_lzt = Column(Integer, default=0)
    spent_rub = Column(Integer, default=0)


class LZTPurchase(Base):
    __tablename__ = "lzt_purchases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, index=True, unique=True)
    phone = Column(String(32), index=True, nullable=True)
    price = Column(Integer, default=0)
    country = Column(String(64), default="")
    status = Column(String(16), default="bought")
    error = Column(Text, default="")
    bought_at = Column(DateTime, default=datetime.now, index=True)


# ═══════════════════════════════════════════════════════════════════════════ #
#                                  УТИЛИТЫ                                    #
# ═══════════════════════════════════════════════════════════════════════════ #

def normalize_phone(phone: str) -> str:
    if not phone:
        return phone
    phone = phone.strip()
    return phone if phone.startswith("+") else "+" + phone


def _today() -> str:
    return date.today().isoformat()


# ═══════════════════════════════════════════════════════════════════════════ #
#                                 BACKUP                                      #
# ═══════════════════════════════════════════════════════════════════════════ #

def _account_to_dict(acc: Account) -> dict:
    return {
        "phone": acc.phone,
        "name": acc.name or acc.phone,
        "session": acc.session,   # plaintext (расшифрован ORM)
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
    try:
        with scope() as s:
            accounts = s.query(Account).filter(Account.deleted_at.is_(None)).all()
            data = [_account_to_dict(a) for a in accounts]

        os.makedirs(os.path.dirname(BACKUP_FILE) or ".", exist_ok=True)
        tmp = BACKUP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BACKUP_FILE)
        logger.info(f"💾 Бэкап сохранён ({len(data)} аккаунтов, plaintext)")
    except Exception as e:
        logger.error(f"❌ Ошибка бэкапа: {type(e).__name__}: {e}")
        logger.debug(traceback.format_exc())


def load_backup() -> list:
    if not os.path.exists(BACKUP_FILE):
        return []
    try:
        with open(BACKUP_FILE, "rb") as f:
            raw = f.read()

        if not raw.strip():
            return []

        # 1) Пробуем как обычный JSON (plaintext)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

        # 2) Fallback — старый зашифрованный бэкап
        try:
            decrypted = _get_fernet().decrypt(raw).decode("utf-8")
            data = json.loads(decrypted)
            logger.warning(
                "[backup] Обнаружен зашифрованный бэкап — читаю через Fernet. "
                "При следующем сохранении он станет plaintext."
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
    data = load_backup()
    if not data:
        return 0
    try:
        with scope() as s:
            if s.query(Account).filter(Account.deleted_at.is_(None)).count() > 0:
                return 0
            restored = 0
            for item in data:
                try:
                    phone = normalize_phone(item.get("phone", ""))
                    session = item.get("session", "")
                    if not phone or not session:
                        continue
                    reg = item.get("registered_at")
                    try:
                        reg_dt = datetime.fromisoformat(reg) if reg else datetime.now()
                    except Exception:
                        reg_dt = datetime.now()
                    s.add(Account(
                        phone=phone,
                        name=item.get("name") or phone,
                        session=session,   # ORM зашифрует
                        two_fa=item.get("two_fa", "") or "",
                        registered_at=reg_dt,
                        status=item.get("status", "uncleaned") or "uncleaned",
                        country_code=item.get("country_code", "") or "",
                        price=item.get("price", 0) or 0,
                        bought_from=item.get("bought_from", "manual") or "manual",
                        lzt_item_id=item.get("lzt_item_id"),
                        favorite=item.get("favorite", 0) or 0,
                    ))
                    restored += 1
                except Exception as e:
                    logger.warning(f"⚠️ Запись пропущена: {e}")
    except Exception as e:
        logger.error(f"❌ restore_from_backup: {e}")
        return 0
    if restored:
        logger.info(f"♻️ Восстановлено из бэкапа: {restored}")
    return restored


# ═══════════════════════════════════════════════════════════════════════════ #
#                          МИГРАЦИИ / INIT                                    #
# ═══════════════════════════════════════════════════════════════════════════ #

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
    """Шифрует plaintext session_string у существующих записей."""
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

def _migrate_encrypt_two_fa() -> None:
    """Шифрует plaintext two_fa у существующих записей."""
    try:
        f = _get_fernet()
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT id, two_fa FROM accounts WHERE two_fa IS NOT NULL AND two_fa != ''")
            ).fetchall()

        to_encrypt = [
            (r[0], r[1]) for r in rows
            if isinstance(r[1], str) and not r[1].startswith(FERNET_MARKER)
        ]
        if not to_encrypt:
            return

        with engine.begin() as conn:
            for row_id, raw in to_encrypt:
                enc = f.encrypt(raw.encode("utf-8")).decode("ascii")
                conn.execute(
                    text("UPDATE accounts SET two_fa = :s WHERE id = :id"),
                    {"s": enc, "id": row_id},
                )
        logger.info(f"🔐 Зашифровано two_fa: {len(to_encrypt)}")
    except Exception as e:
        logger.error(f"❌ _migrate_encrypt_two_fa: {type(e).__name__}: {e}")

def init_db() -> None:
    # Инициализируем ключ шифрования ДО первого чтения/записи
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
    _migrate_encrypt_two_fa()

    restored = restore_from_backup()
    if restored == 0:
        save_backup()


# ═══════════════════════════════════════════════════════════════════════════ #
#                            АККАУНТЫ — CRUD                                  #
# ═══════════════════════════════════════════════════════════════════════════ #

def add_account(phone: str, session_str: str, two_fa: str = "",
                name: str = "", country_code: str = "", price: int = 0,
                bought_from: str = "manual",
                lzt_item_id: Optional[int] = None) -> bool:
    phone = normalize_phone(phone)
    logger.info(f"[db.add_account] {phone} name={name!r} from={bought_from}")
    try:
        with scope() as s:
            acc = s.query(Account).filter(Account.phone == phone).first()
            if acc:
                acc.session = session_str      # ORM зашифрует
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
                    session=session_str,       # ORM зашифрует
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
    with scope() as s:
        q = s.query(Account)
        if not include_archived:
            q = q.filter(Account.deleted_at.is_(None))
        return q.order_by(
            Account.favorite.desc(),
            Account.id.desc(),
        ).all()


def get_favorites():
    with scope() as s:
        return (s.query(Account)
                .filter(Account.deleted_at.is_(None))
                .filter(Account.favorite == 1)
                .order_by(Account.id.desc())
                .all())


def get_archived_accounts():
    with scope() as s:
        return (s.query(Account)
                .filter(Account.deleted_at.isnot(None))
                .order_by(Account.deleted_at.desc())
                .all())


def get_account(account_id: int):
    with scope() as s:
        return s.get(Account, account_id)


def get_account_by_phone(phone: str):
    phone = normalize_phone(phone)
    with scope() as s:
        return (s.query(Account)
                .filter(Account.phone == phone)
                .filter(Account.deleted_at.is_(None))
                .first())


def delete_account(account_id: int) -> bool:
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            s.delete(acc)
    save_backup()
    return True


def archive_account(account_id: int) -> bool:
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.deleted_at = datetime.now()
    save_backup()
    return True


def restore_account(account_id: int) -> bool:
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.deleted_at = None
    save_backup()
    return True


def update_account_status(account_id: int, status: str) -> bool:
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
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.two_fa = two_fa
    save_backup()
    return True


def update_account_name(account_id: int, name: str) -> bool:
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.name = (name or "").strip() or acc.phone
    save_backup()
    return True


def toggle_favorite(account_id: int) -> bool:
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
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.favorite = 1 if value else 0
    save_backup()
    return True


def is_favorite(account_id: int) -> bool:
    with scope() as s:
        acc = s.get(Account, account_id)
        return bool(acc and (acc.favorite or 0) == 1)


def update_last_checked(account_id: int, alive: bool) -> bool:
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
    with scope() as s:
        acc = s.get(Account, account_id)
        if acc:
            acc.last_cleaned_at = datetime.now()
            acc.status = "cleaned"
            acc.health_fail_count = 0
    save_backup()
    return True


def get_accounts_stats() -> dict:
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
    with scope() as s:
        return (s.query(Account)
                .filter(Account.deleted_at.is_(None))
                .filter(Account.health_fail_count >= threshold)
                .all())


# ═══════════════════════════════════════════════════════════════════════════ #
#                              ACTIONS LOG                                    #
# ═══════════════════════════════════════════════════════════════════════════ #

def log_action(user_id: int, action: str, details: str = "",
               account_id: Optional[int] = None) -> bool:
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
    with scope() as s:
        return (s.query(ActionLog)
                .order_by(ActionLog.created_at.desc())
                .limit(limit).all())


def get_account_actions(account_id: int, limit: int = 30):
    with scope() as s:
        return (s.query(ActionLog)
                .filter(ActionLog.account_id == account_id)
                .order_by(ActionLog.created_at.desc())
                .limit(limit).all())


def get_action_counts(account_id: int) -> dict:
    with scope() as s:
        rows = (s.query(ActionLog.action, func.count(ActionLog.id))
                .filter(ActionLog.account_id == account_id)
                .group_by(ActionLog.action)
                .all())
    return {r[0]: r[1] for r in rows}


# ═══════════════════════════════════════════════════════════════════════════ #
#                              DAILY STATS                                    #
# ═══════════════════════════════════════════════════════════════════════════ #

def _bump_today(field: str, amount: int = 1) -> None:
    day = _today()
    with scope() as s:
        row = s.get(DailyStat, day)
        if row is None:
            row = DailyStat(day=day)
            s.add(row)
            s.flush()
        setattr(row, field, (getattr(row, field) or 0) + amount)


def stat_added(amount: int = 1) -> None:
    _bump_today("added", amount)


def stat_cleaned(amount: int = 1) -> None:
    _bump_today("cleaned", amount)


def stat_dead(amount: int = 1) -> None:
    _bump_today("dead", amount)


def stat_bought_lzt(amount: int = 1, spent_rub: int = 0) -> None:
    _bump_today("bought_lzt", amount)
    if spent_rub:
        _bump_today("spent_rub", spent_rub)


def get_daily_stats(days: int = 30):
    with scope() as s:
        return (s.query(DailyStat)
                .order_by(DailyStat.day.desc())
                .limit(days).all())


def get_totals_stats() -> dict:
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


# ═══════════════════════════════════════════════════════════════════════════ #
#                             LZT PURCHASES                                   #
# ═══════════════════════════════════════════════════════════════════════════ #

def add_lzt_purchase(item_id: int, phone: Optional[str], price: int,
                     country: str = "", status: str = "bought",
                     error: str = "") -> int:
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
    with scope() as s:
        row = s.query(LZTPurchase).filter(LZTPurchase.item_id == item_id).first()
        if row:
            row.status = status
            if phone:
                row.phone = phone
            row.error = error or ""
    return True


def get_lzt_purchase(item_id: int):
    with scope() as s:
        return s.query(LZTPurchase).filter(LZTPurchase.item_id == item_id).first()


def get_lzt_purchases(status: Optional[str] = None, limit: int = 200):
    with scope() as s:
        q = s.query(LZTPurchase)
        if status:
            q = q.filter(LZTPurchase.status == status)
        return q.order_by(LZTPurchase.bought_at.desc()).limit(limit).all()


def get_known_lzt_item_ids() -> set:
    with scope() as s:
        rows = s.query(LZTPurchase.item_id).all()
    return {r[0] for r in rows}


def get_lzt_stats() -> dict:
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