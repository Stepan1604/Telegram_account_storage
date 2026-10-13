import logging

from ._state import Context
from .menu import register as register_menu
from .clean import register as register_clean
from .storage import register as register_storage
from .lzt_buy import register as register_lzt_buy
from .reports import register as register_reports
from .lzt_logs import register as register_lzt_logs
from .add_account import register as register_add_account

logger = logging.getLogger(__name__)


def register_handlers(bot):
    """
    Регистрирует все хендлеры бота в единой точке входа.

    Порядок регистрации важен:
      1. clean — создаёт schedule_auto_clean, который нужен другим модулям.
      2. menu — главное меню и /start.
      3. storage — карточка аккаунта и действия над ним.
      4. lzt_buy — покупка через LZT.
      5. reports — отчёты и экспорт.
      6. lzt_logs — LZT-логи и повторный вход.
      7. add_account — FSM добавления аккаунта. Регистрируется последним,
         чтобы его text_router не перехватывал команды других модулей.

    :param bot: экземпляр AsyncTeleBot.
    """
    ctx = Context(bot)

    register_clean(ctx)
    register_menu(ctx)
    register_storage(ctx)
    register_lzt_buy(ctx)
    register_reports(ctx)
    register_lzt_logs(ctx)
    register_add_account(ctx)

    logger.info("✅ Все хендлеры зарегистрированы")