from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.callback_data.account import AccountAction as Action
from src.keyboards.accounts import account_button
from src.services.emoji import EmojiCatalog
from src.services.qr_login import LoginAttempt


def login_keyboard(attempt: LoginAttempt, icons: EmojiCatalog, *, ready=False):
    buttons = []
    if ready:
        buttons.append(
            [
                account_button(
                    'Сохранить вход',
                    attempt.account_id,
                    Action.QR_SAVE,
                    value=attempt.nonce,
                    icon='confirm',
                    icons=icons,
                    style=ButtonStyle.SUCCESS,
                )
            ]
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton(
                    text='Открыть Яндекс',
                    url=attempt.link.get_secret_value(),
                    style=ButtonStyle.PRIMARY,
                )
            ]
        )
    buttons.append(
        [
            account_button(
                'Отмена',
                attempt.account_id,
                Action.QR_CANCEL,
                value=attempt.nonce,
                icon='back',
                icons=icons,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
