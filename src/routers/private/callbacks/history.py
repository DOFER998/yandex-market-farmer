from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from src.callback_data.account import AccountAction as Action
from src.callback_data.account import AccountCallback
from src.keyboards.accounts import back_keyboard
from src.services.accounts import AccountService
from src.services.screens import ScreenService
from src.views.accounts import history_text, statistics_text

router = Router(name=__name__)


@router.callback_query(AccountCallback.filter(F.action.in_({Action.HISTORY, Action.STATS})))
async def history_callback(
    query: CallbackQuery,
    callback_data: AccountCallback,
    accounts: AccountService,
    screens: ScreenService,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    revision = screens.begin(query.message)
    if callback_data.action == Action.HISTORY:
        text = history_text(await accounts.history(callback_data.account_id), screens.icons)
    else:
        account = await accounts.get(callback_data.account_id)
        text = statistics_text(
            account.display_name or account.id, await accounts.statistics(account.id), screens.icons
        )
    await screens.show(
        query.message, text, back_keyboard(callback_data.account_id, screens.icons), revision
    )
