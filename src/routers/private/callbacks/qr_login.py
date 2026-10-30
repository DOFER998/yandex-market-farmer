import asyncio

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from src.callback_data.account import AccountAction as Action
from src.callback_data.account import AccountCallback
from src.common.utils.qr import qr_png
from src.keyboards.accounts import account_keyboard, back_keyboard, connection_keyboard
from src.keyboards.qr_login import login_keyboard
from src.services.accounts import AccountService
from src.services.qr_login import QrLoginService
from src.services.screens import ScreenService
from src.views.accounts import connection_text, status_text
from src.views.qr_login import (
    login_expired,
    login_instructions,
    login_loading,
    login_result,
    login_saved,
)

router = Router(name=__name__)


@router.callback_query(
    AccountCallback.filter(F.action.in_({Action.QR_START, Action.QR_SAVE, Action.QR_CANCEL}))
)
async def qr_callback(
    query: CallbackQuery,
    callback_data: AccountCallback,
    accounts: AccountService,
    screens: ScreenService,
    qr_login: QrLoginService,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    message = query.message
    revision = screens.begin(message)
    account = await accounts.get(callback_data.account_id)
    if callback_data.action == Action.QR_CANCEL:
        await qr_login.cancel(message.chat.id)
        await screens.show(
            message,
            connection_text(account, screens.icons),
            connection_keyboard(account, screens.icons),
            revision,
        )
        return
    if callback_data.action == Action.QR_SAVE:
        attempt = qr_login.get(message.chat.id, account.id, callback_data.value)
        if not attempt or attempt.error or attempt.balance is None:
            await screens.show(
                message,
                login_expired(screens.icons),
                connection_keyboard(account, screens.icons),
                revision,
            )
            return
        account = await accounts.attach_verified(
            account.id, attempt.session.cookies.jar, attempt.balance
        )
        await qr_login.cancel(message.chat.id)
        await screens.show(
            message,
            login_saved(screens.icons)
            + '\n\n'
            + status_text(account, await accounts.latest_run(account.id), screens.icons),
            account_keyboard(account, screens.icons),
            revision,
        )
        return
    await screens.show(
        message, login_loading(screens.icons), back_keyboard(account.id, screens.icons), revision
    )
    attempt = await qr_login.begin(message.chat.id, account.id)
    try:
        png = await asyncio.to_thread(qr_png, attempt.link)
        await screens.show_qr(
            message,
            png,
            login_instructions(screens.icons),
            login_keyboard(attempt, screens.icons),
            revision,
        )
    except BaseException:
        await qr_login.cancel(message.chat.id)
        raise

    async def completed(result):
        keyboard = (
            connection_keyboard(account, screens.icons)
            if result.error
            else login_keyboard(result, screens.icons, ready=True)
        )
        await screens.show(message, login_result(result, screens.icons), keyboard, revision)

    qr_login.watch(message.chat.id, completed)
