from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.keyboards.accounts import account_keyboard, back_keyboard, connection_keyboard
from src.routers.private.messages.menu import menu_handler
from src.services.accounts import AccountService
from src.services.screens import ScreenService
from src.states.account import AccountForm
from src.views.accounts import connection_text, status_text

router = Router(name=__name__)


@router.message(AccountForm.name, F.text)
async def name_handler(
    message: Message, accounts: AccountService, screens: ScreenService, state: FSMContext
) -> None:
    if await state.get_state() != AccountForm.name.state:
        await menu_handler(message, accounts, screens, state)
        return
    revision = screens.begin(message, callback=False)
    name = (message.text or '').strip()
    if not 1 <= len(name) <= 40 or name.startswith('/') or any(ord(c) < 32 for c in name):
        await screens.show(
            message,
            'Название: от 1 до 40 символов, без команд и переносов строк.',
            back_keyboard(icons=screens.icons),
            revision,
        )
        return
    data = await state.get_data()
    await state.clear()
    if account_id := data.get('rename'):
        account = await accounts.rename(account_id, name)
        text, keyboard = (
            status_text(account, icons=screens.icons),
            account_keyboard(account, screens.icons),
        )
    else:
        account = await accounts.create(name)
        text, keyboard = (
            connection_text(account, screens.icons),
            connection_keyboard(account, screens.icons),
        )
    await screens.show(message, text, keyboard, revision)
