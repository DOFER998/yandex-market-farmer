from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.keyboards.accounts import accounts_keyboard
from src.services.accounts import AccountService
from src.services.screens import ScreenService
from src.views.accounts import home_text

router = Router(name=__name__)


@router.message(CommandStart())
async def menu_handler(
    message: Message, accounts: AccountService, screens: ScreenService, state: FSMContext
) -> None:
    await state.clear()
    revision = screens.begin(message, callback=False)
    items = await accounts.list()
    await screens.show(
        message,
        home_text(items, screens.icons),
        accounts_keyboard(items, screens.icons),
        revision,
    )
