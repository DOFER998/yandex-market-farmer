from secrets import randbelow

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.callback_data.account import AccountAction as Action
from src.callback_data.account import AccountCallback
from src.keyboards.accounts import (
    account_keyboard,
    accounts_keyboard,
    back_keyboard,
    connection_keyboard,
    delete_keyboard,
    settings_keyboard,
)
from src.services.accounts import AccountService
from src.services.screens import ScreenService
from src.states.account import AccountForm
from src.views.accounts import (
    connection_text,
    delete_text,
    home_text,
    name_prompt,
    settings_text,
    status_text,
)

router = Router(name=__name__)


@router.callback_query(
    AccountCallback.filter(F.action.in_({Action.HOME, Action.ADD, Action.RENAME}))
)
async def navigation_callback(
    query: CallbackQuery,
    callback_data: AccountCallback,
    accounts: AccountService,
    screens: ScreenService,
    state: FSMContext,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    revision = screens.begin(query.message)
    await state.clear()
    if callback_data.action == Action.HOME:
        items = await accounts.list()
        await screens.show(
            query.message,
            home_text(items, screens.icons),
            accounts_keyboard(items, screens.icons, page=callback_data.value),
            revision,
        )
    else:
        rename = callback_data.action == Action.RENAME
        if rename:
            await accounts.get(callback_data.account_id)
        await state.set_state(AccountForm.name)
        await state.update_data(rename=callback_data.account_id if rename else None)
        await screens.show(
            query.message,
            name_prompt(screens.icons, rename=rename),
            back_keyboard(callback_data.account_id if rename else '_', screens.icons),
            revision,
        )


@router.callback_query(
    AccountCallback.filter(F.action.in_({Action.STATUS, Action.SETTINGS, Action.CONNECT}))
)
async def status_callback(
    query: CallbackQuery,
    callback_data: AccountCallback,
    accounts: AccountService,
    screens: ScreenService,
    state: FSMContext,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    await state.clear()
    revision = screens.begin(query.message)
    account = await accounts.get(callback_data.account_id)
    if callback_data.action == Action.SETTINGS:
        text, keyboard = (
            settings_text(account, screens.icons),
            settings_keyboard(account, screens.icons),
        )
    elif callback_data.action == Action.CONNECT:
        text, keyboard = (
            connection_text(account, screens.icons),
            connection_keyboard(account, screens.icons),
        )
    else:
        text = status_text(account, await accounts.latest_run(account.id), screens.icons)
        keyboard = account_keyboard(account, screens.icons)
    await screens.show(query.message, text, keyboard, revision)


@router.callback_query(
    AccountCallback.filter(
        F.action.in_(
            {Action.AUTO_ON, Action.AUTO_OFF, Action.NOTIFY_ON, Action.NOTIFY_OFF, Action.INTERVAL}
        )
    )
)
async def settings_callback(
    query: CallbackQuery,
    callback_data: AccountCallback,
    accounts: AccountService,
    screens: ScreenService,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    revision = screens.begin(query.message)
    action = callback_data.action
    if action in {Action.AUTO_ON, Action.AUTO_OFF}:
        account = await accounts.configure(
            callback_data.account_id, enabled=action == Action.AUTO_ON
        )
    elif action == Action.INTERVAL:
        account = await accounts.configure(callback_data.account_id, minutes=callback_data.value)
    else:
        account = await accounts.set_notifications(
            callback_data.account_id, action == Action.NOTIFY_ON
        )
    await screens.show(
        query.message,
        settings_text(account, screens.icons),
        settings_keyboard(account, screens.icons),
        revision,
    )


@router.callback_query(AccountCallback.filter(F.action.in_({Action.DELETE, Action.DELETE_CONFIRM})))
async def delete_callback(
    query: CallbackQuery,
    callback_data: AccountCallback,
    accounts: AccountService,
    screens: ScreenService,
    state: FSMContext,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return
    if callback_data.action == Action.DELETE_CONFIRM:
        data = await state.get_data()
        valid = (
            await state.get_state() == AccountForm.delete.state
            and data.get('delete_account') == callback_data.account_id
            and data.get('delete_nonce') == callback_data.value
            and data.get('delete_message') == query.message.message_id
        )
        if not valid:
            await query.answer('Подтверждение устарело. Открой удаление заново.')
            return
        await state.clear()
        await query.answer()
        revision = screens.begin(query.message)
        await accounts.delete(callback_data.account_id)
        items = await accounts.list()
        await screens.show(
            query.message,
            'Аккаунт удалён.\n\n' + home_text(items, screens.icons),
            accounts_keyboard(items, screens.icons),
            revision,
        )
    else:
        await query.answer()
        revision = screens.begin(query.message)
        account = await accounts.get(callback_data.account_id)
        nonce = randbelow(2**31)
        await state.set_state(AccountForm.delete)
        await state.update_data(
            delete_account=account.id, delete_nonce=nonce, delete_message=query.message.message_id
        )
        await screens.show(
            query.message,
            delete_text(account, screens.icons),
            delete_keyboard(account.id, nonce, screens.icons),
            revision,
        )
