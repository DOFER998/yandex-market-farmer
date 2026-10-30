from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from src.callback_data.account import AccountAction as Action
from src.callback_data.account import AccountCallback
from src.keyboards.accounts import back_keyboard, loading_keyboard
from src.services.accounts import AccountService
from src.services.farming import FarmingService
from src.services.screens import ScreenService
from src.views.accounts import account_name, format_run, loading_text

router = Router(name=__name__)


@router.callback_query(
    AccountCallback.filter(
        F.action.in_({Action.BALANCE, Action.COLLECT, Action.COLLECT_GAMES, Action.RESUME})
    )
)
async def farming_callback(
    query: CallbackQuery,
    callback_data: AccountCallback,
    accounts: AccountService,
    farming: FarmingService,
    screens: ScreenService,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    revision = screens.begin(query.message)
    collecting = callback_data.action in (Action.COLLECT, Action.COLLECT_GAMES)
    await screens.show(
        query.message,
        loading_text(collecting, screens.icons),
        loading_keyboard(screens.icons),
        revision,
    )
    account = await accounts.get(callback_data.account_id)
    run = await farming.run(
        account.id,
        read_only=not collecting,
        reconcile=callback_data.action == Action.RESUME,
        target='games' if callback_data.action == Action.COLLECT_GAMES else 'daily',
    )
    account = await accounts.get(account.id)
    operations = await accounts.run_operations(run)
    result = format_run(run, screens.icons, operations=operations)
    text = f'<b>{account_name(account)}</b>\n\n{result}'
    if callback_data.action == Action.RESUME and run.state == 'success':
        text += '\n\nВход проверен. Автосбор можно включить в настройках.'
    await screens.show(query.message, text, back_keyboard(account.id, screens.icons), revision)
