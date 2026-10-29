from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.routers.private.messages.menu import menu_handler
from src.services.accounts import AccountService
from src.services.screens import ScreenService

router = Router(name=__name__)


@router.message()
async def fallback_handler(
    message: Message, accounts: AccountService, screens: ScreenService, state: FSMContext
) -> None:
    await menu_handler(message, accounts, screens, state)


@router.callback_query()
async def stale_callback(
    query: CallbackQuery, accounts: AccountService, screens: ScreenService, state: FSMContext
) -> None:
    from src.keyboards.accounts import accounts_keyboard
    from src.views.accounts import home_text

    await query.answer('Меню обновлено')
    if not isinstance(query.message, Message):
        return
    await state.clear()
    revision = screens.begin(query.message)
    items = await accounts.list()
    await screens.show(
        query.message,
        home_text(items, screens.icons),
        accounts_keyboard(items, screens.icons),
        revision,
    )
