import asyncio
import logging
import sys
from datetime import datetime

import telebot
from telebot.async_telebot import AsyncTeleBot

from config import BOT_TOKEN, ADMIN_ID, ADMINS

logger = logging.getLogger("runner")


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


if ADMIN_ID not in ADMINS:
    ADMINS.append(ADMIN_ID)

bot = AsyncTeleBot(BOT_TOKEN)

from bot.handlers import register_handlers  # noqa: E402


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


async def main():
    import database as db

    logger.info("=" * 60)
    logger.info("🚀 Запуск бота")
    logger.info("=" * 60)

    try:
        db.init_db()
        logger.info("✅ БД инициализирована")
    except Exception as e:
        logger.exception(f"❌ Ошибка БД: {e}")
        return

    register_handlers(bot)
    logger.info("✅ Хендлеры зарегистрированы")

    try:
        await notify_startup()
        logger.info("✅ Уведомление отправлено")
    except Exception as e:
        logger.warning(f"⚠️ Уведомление: {e}")

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


if __name__ == "__main__":
    run()