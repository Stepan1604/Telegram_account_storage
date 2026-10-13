import logging

from telebot.async_telebot import AsyncTeleBot

from config import BOT_TOKEN, ADMIN_ID, ADMINS

if ADMIN_ID not in ADMINS:
    ADMINS.append(ADMIN_ID)

logger = logging.getLogger(__name__)

bot = AsyncTeleBot(BOT_TOKEN)

from .handlers import register_handlers

__all__ = ["bot", "register_handlers", "logger"]