from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.callback_data.account import AccountAction as Action
from src.callback_data.account import AccountCallback
from src.database.models import Account
from src.services.emoji import ALIASES, EmojiCatalog


def account_button(
    text: str,
    account_id: str,
    action: Action,
    *,
    value: int = 0,
    icons: EmojiCatalog | None = None,
    icon: str = '',
    style: ButtonStyle | None = None,
) -> InlineKeyboardButton:
    identifier = icons.icon_id(icon) if icons else None
    if icon and not identifier:
        text = f'{ALIASES[icon][0]} {text}'
    return InlineKeyboardButton(
        text=text,
        callback_data=AccountCallback(action=action, account_id=account_id, value=value).pack(),
        icon_custom_emoji_id=identifier,
        style=style,
    )


def back_keyboard(account_id: str = '_', icons: EmojiCatalog | None = None) -> InlineKeyboardMarkup:
    action = Action.HOME if account_id == '_' else Action.STATUS
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                account_button(
                    'Назад к аккаунтам' if account_id == '_' else 'Назад к аккаунту',
                    account_id,
                    action,
                    icons=icons,
                    icon='back',
                )
            ]
        ]
    )


def account_keyboard(account: Account, icons: EmojiCatalog | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for text, action, icon in [
        ('Обновить баланс', Action.BALANCE, 'balance'),
        ('Ежедневная награда', Action.COLLECT, 'collect'),
        ('История', Action.HISTORY, 'history'),
        ('Статистика', Action.STATS, 'stats'),
        ('Настройки', Action.SETTINGS, 'settings'),
        ('Подключение', Action.CONNECT, 'connect'),
    ]:
        builder.add(account_button(text, account.id, action, icons=icons, icon=icon))
    builder.adjust(2)
    builder.row(
        account_button(
            'Игровые награды', account.id, Action.COLLECT_GAMES, icons=icons, icon='collect'
        )
    )
    if account.needs_review or account.auth_state != 'ready':
        builder.row(
            account_button('Проверить вход', account.id, Action.RESUME, icons=icons, icon='refresh')
        )
    builder.row(account_button('Все аккаунты', '_', Action.HOME, icons=icons, icon='back'))
    return builder.as_markup()


def settings_keyboard(account: Account, icons: EmojiCatalog) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        account_button(
            'Автосбор: ' + ('вкл' if account.auto_collect else 'выкл'),
            account.id,
            Action.AUTO_OFF if account.auto_collect else Action.AUTO_ON,
            icons=icons,
            icon='collect',
        )
    )
    builder.row(
        account_button(
            'Уведомления: ' + ('вкл' if account.notifications else 'выкл'),
            account.id,
            Action.NOTIFY_OFF if account.notifications else Action.NOTIFY_ON,
            icons=icons,
            icon='notify',
        )
    )
    builder.row(
        account_button('Переименовать', account.id, Action.RENAME, icons=icons, icon='rename')
    )
    builder.row(
        account_button(
            'Удалить аккаунт',
            account.id,
            Action.DELETE,
            style=ButtonStyle.DANGER,
            icons=icons,
            icon='delete',
        )
    )
    builder.row(
        account_button('Назад к аккаунту', account.id, Action.STATUS, icons=icons, icon='back')
    )
    return builder.as_markup()


def connection_keyboard(account: Account, icons: EmojiCatalog) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                account_button(
                    'Войти через QR',
                    account.id,
                    Action.QR_START,
                    icons=icons,
                    icon='connect',
                    style=ButtonStyle.PRIMARY,
                )
            ],
            [
                account_button(
                    'Проверить вход', account.id, Action.RESUME, icons=icons, icon='refresh'
                )
            ],
            [
                account_button(
                    'Назад к аккаунту', account.id, Action.STATUS, icons=icons, icon='back'
                )
            ],
        ]
    )


def accounts_keyboard(
    accounts: list[Account], icons: EmojiCatalog | None = None, page: int = 0
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    size = 8
    last = max(0, (len(accounts) - 1) // size)
    page = max(0, min(page, last))
    for account in accounts[page * size : (page + 1) * size]:
        builder.row(
            account_button(
                account.display_name or account.id,
                account.id,
                Action.STATUS,
                icons=icons,
                icon='account',
            )
        )
    navigation = []
    if page > 0:
        navigation.append(account_button('Назад', '_', Action.HOME, value=page - 1))
    if page < last:
        navigation.append(account_button('Дальше', '_', Action.HOME, value=page + 1))
    if navigation:
        builder.row(*navigation)
    builder.row(account_button('Добавить аккаунт', '_', Action.ADD, icons=icons, icon='add'))
    return builder.as_markup()


def loading_keyboard(icons: EmojiCatalog | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [account_button('Выполняется…', '_', Action.BUSY, icons=icons, icon='loading')]
        ]
    )


def delete_keyboard(account_id: str, nonce: int, icons: EmojiCatalog) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                account_button(
                    'Да, удалить',
                    account_id,
                    Action.DELETE_CONFIRM,
                    value=nonce,
                    icons=icons,
                    icon='confirm',
                    style=ButtonStyle.DANGER,
                ),
                account_button(
                    'Нет, оставить',
                    account_id,
                    Action.SETTINGS,
                    style=ButtonStyle.SUCCESS,
                    icons=icons,
                    icon='back',
                ),
            ]
        ]
    )
