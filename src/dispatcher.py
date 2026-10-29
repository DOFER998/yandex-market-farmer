from aiogram import Dispatcher
from aiogram.fsm.strategy import FSMStrategy

from src.middlewares.errors import error_handler
from src.middlewares.updates import UpdateTasks
from src.routers import include_routers
from src.services.accounts import AccountService
from src.services.emoji import EmojiCatalog
from src.services.farming import FarmingService
from src.services.qr_login import QrLoginService
from src.services.screens import ScreenService


def create_dispatcher(
    *,
    accounts: AccountService,
    farming: FarmingService,
    owner_id: int,
    screens: ScreenService | None = None,
    updates: UpdateTasks | None = None,
    qr_login: QrLoginService | None = None,
) -> Dispatcher:
    dispatcher = Dispatcher(
        accounts=accounts,
        qr_login=qr_login,
        farming=farming,
        screens=screens or ScreenService(EmojiCatalog()),
        fsm_strategy=FSMStrategy.USER_IN_CHAT,
    )
    if updates is not None:
        dispatcher.update.outer_middleware(updates)
    dispatcher.errors.register(error_handler)
    include_routers(dispatcher, owner_id=owner_id)
    return dispatcher
