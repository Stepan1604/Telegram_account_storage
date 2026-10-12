import asyncio
import logging
import os
import sys
from datetime import datetime

import telebot
from telebot.async_telebot import AsyncTeleBot

from config import BOT_TOKEN, ADMIN_ID, ADMINS

logger = logging.getLogger("runner")


# ═══════════════════════════════════════════════════════════════════════════ #
#                                ЛОГИРОВАНИЕ                                  #
# ═══════════════════════════════════════════════════════════════════════════ #

def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("TeleBot").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


setup_logging()


# ═══════════════════════════════════════════════════════════════════════════ #
#                              БОТ                                            #
# ═══════════════════════════════════════════════════════════════════════════ #

if ADMIN_ID not in ADMINS:
    ADMINS.append(ADMIN_ID)

bot = AsyncTeleBot(BOT_TOKEN)

from bot.handlers import register_handlers  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════ #
#                              УТИЛИТЫ                                         #
# ═══════════════════════════════════════════════════════════════════════════ #

def check_encryption_key():
    """Проверяет наличие файла ключа шифрования."""
    try:
        if os.path.exists("secret.key"):
            size = os.path.getsize("secret.key")
            logger.info(f"🔐 Шифрование session включено (secret.key, {size} байт)")
        else:
            logger.warning(
                "⚠️ Файл secret.key не найден.\n"
                "   При первом обращении к БД ключ будет сгенерирован автоматически.\n"
                "   ⚠️ СОХРАНИ его — без ключа session не расшифруются."
            )
    except Exception as e:
        logger.warning(f"⚠️ Не удалось проверить secret.key: {e}")


async def notify_startup():
    import database as db

    s = db.get_accounts_stats()
    text = (
        "🚀 *БОТ ЗАПУЩЕН*\n\n"
        f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
        "📊 *Хранилище:*\n"
        f"• Всего: *{s['total']}*\n"
        f"• Очищено: *{s['cleaned']}*\n"
        f"• Не очищено: *{s['uncleaned']}*\n"
        f"• Мёртвых: *{s['dead']}*"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Не отправлено {admin_id}: {e}")


# ═══════════════════════════════════════════════════════════════════════════ #
#                              ЗАПУСК                                          #
# ═══════════════════════════════════════════════════════════════════════════ #

async def main():
    import database as db

    logger.info("=" * 60)
    logger.info("🚀 Запуск бота")
    logger.info("=" * 60)

    # 1) Проверка ключа шифрования
    check_encryption_key()

    # 2) Инициализация БД
    try:
        db.init_db()
        logger.info("✅ БД инициализирована")
    except Exception as e:
        logger.exception(f"❌ Ошибка БД: {e}")
        return

    # 3) Регистрация хендлеров
    register_handlers(bot)
    logger.info("✅ Хендлеры зарегистрированы")

    # 4) Уведомление админов
    try:
        await notify_startup()
        logger.info("✅ Уведомление отправлено")
    except Exception as e:
        logger.warning(f"⚠️ Уведомление: {e}")

    # 5) Polling
    logger.info("✅ Polling...")
    try:
        await bot.infinity_polling(
            timeout=30,
            request_timeout=30,
            allowed_updates=telebot.util.update_types,
            logger_level=0,
        )
    except Exception as e:
        logger.exception(f"❌ Polling: {e}")


def run():
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Остановлен")
    except Exception as e:
        logger.exception(f"❌ Критическая ошибка: {e}")
    finally:
        try:
            import database as db
            db.Session.remove()
            db.engine.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    run()