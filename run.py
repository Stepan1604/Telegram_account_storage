import asyncio
import logging
import os
import sys
from datetime import datetime

import telebot
from telebot.async_telebot import AsyncTeleBot

from config import BOT_TOKEN, ADMIN_ID, ADMINS

logger = logging.getLogger("runner")


def setup_logging():
    """
    Настраивает корневой логгер и приглушает шумные библиотеки.

    Формат: "дата - имя - уровень - сообщение". Устанавливает уровень
    INFO для корневого логгера и WARNING/CRITICAL для telethon,
    TeleBot, httpx, httpcore, asyncio.
    """
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

from bot.handlers import register_handlers
from bot.middlewares import AdminMiddleware


def check_encryption_key():
    """
    Проверяет наличие файла ключа шифрования.

    Ищет сначала data/secret.key, потом secret.key в корне. Если
    файла нет — предупреждает, что ключ будет сгенерирован при
    первом обращении к БД. Без ключа session не расшифруются.
    """
    try:
        if os.path.exists("data/secret.key"):
            size = os.path.getsize("data/secret.key")
            logger.info(f"🔐 Шифрование session включено (secret.key, {size} байт)")
        elif os.path.exists("secret.key"):
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
    """
    Отправляет всем админам уведомление о запуске бота.

    В сообщении — текущее время и статистика хранилища (всего,
    очищено, не очищено, мёртвых).
    """
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


async def shutdown_all() -> None:
    """
    Корректно завершает работу бота.

    Закрывает все Telethon-клиенты через shutdown_all_clients,
    даёт event loop'у досчитать оставшиеся фоновые задачи (send/recv
    циклы Telethon), отменяет зависшие, закрывает сессию SQLAlchemy
    и engine.
    """
    try:
        from bot.telethon_ops import shutdown_all_clients
        await asyncio.shield(shutdown_all_clients())
    except Exception as e:
        logger.warning(f"⚠️ shutdown_all_clients: {e}")

    try:
        await asyncio.sleep(0.5)
    except Exception:
        pass

    try:
        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks()
                   if t is not current and not t.done()]
        if pending:
            logger.info(f"⏳ Ожидаю завершения {len(pending)} задач...")
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            logger.info("✅ Все фоновые задачи завершены")
    except Exception as e:
        logger.warning(f"⚠️ Ожидание задач: {e}")

    try:
        import database as db
        db.Session.remove()
        db.engine.dispose()
        logger.info("✅ БД закрыта")
    except Exception as e:
        logger.warning(f"⚠️ Закрытие БД: {e}")


async def main():
    """
    Основная точка входа: инициализация и запуск бота.

    Порядок:
      1. Проверка ключа шифрования.
      2. Инициализация БД (создание таблиц, миграции, restore из бэкапа).
      3. Регистрация middleware для проверки прав админа.
      4. Регистрация всех хендлеров.
      5. Уведомление админов о запуске.
      6. Запуск infinity_polling.
      7. Корректное завершение через shutdown_all.
    """
    import database as db

    logger.info("=" * 60)
    logger.info("🚀 Запуск бота")
    logger.info("=" * 60)

    check_encryption_key()

    try:
        db.init_db()
        logger.info("✅ БД инициализирована")
    except Exception as e:
        logger.exception(f"❌ Ошибка БД: {e}")
        return

    try:
        bot.setup_middleware(AdminMiddleware())
        logger.info("✅ Middleware зарегистрирован")
    except Exception as e:
        logger.warning(f"⚠️ Middleware: {e}")

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
    finally:
        logger.info("🛑 Завершение работы...")
        await shutdown_all()


def run():
    """
    Синхронная обёртка для запуска asyncio-цикла.

    Ловит KeyboardInterrupt и SystemExit при остановке, а также
    любые критические ошибки. Всё логируется.
    """
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Остановлен")
    except Exception as e:
        logger.exception(f"❌ Критическая ошибка: {e}")


if __name__ == "__main__":
    run()