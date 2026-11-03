from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from src.callback_data.account import AccountAction as Action
from src.callback_data.account import AccountCallback
from src.keyboards.accounts import bonus_keyboard, loading_keyboard
from src.services.accounts import AccountService
from src.services.screens import ScreenService
from src.views.bonus import bonus_loading_text, bonus_text

if TYPE_CHECKING:
    from src.services.bonus import BonusService

router = Router(name=__name__)


@router.callback_query(
    AccountCallback.filter(
        F.action.in_(
            {
                Action.GAMES,
                Action.LIVE,
                Action.LIVE_REFRESH,
                Action.GAMES_ON,
                Action.GAMES_OFF,
                Action.LIVE_ON,
                Action.LIVE_OFF,
            }
        )
    )
)
async def bonus_callback(
    query: CallbackQuery,
    callback_data: AccountCallback,
    accounts: AccountService,
    screens: ScreenService,
    bonus: 'BonusService',
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    action = callback_data.action
    revision = screens.begin(query.message, activate=action in {Action.GAMES, Action.LIVE})
    source = 'games' if action in {Action.GAMES, Action.GAMES_ON, Action.GAMES_OFF} else 'live'
    account = await accounts.get(callback_data.account_id)
    if action in {Action.GAMES_ON, Action.GAMES_OFF, Action.LIVE_ON, Action.LIVE_OFF}:
        account = await bonus.configure(
            account.id, source, action in {Action.GAMES_ON, Action.LIVE_ON}
        )
    if action == Action.LIVE_REFRESH:
        await screens.show(
            query.message,
            bonus_loading_text(source, screens.icons),
            loading_keyboard(screens.icons),
            revision,
        )
        status = await bonus.refresh_live(account.id)
    else:
        status = await bonus.status(account.id, source)
    await screens.show(
        query.message,
        bonus_text(account, source, status, screens.icons, available=bonus.available(source)),
        bonus_keyboard(account, source, screens.icons),
        revision,
    )
