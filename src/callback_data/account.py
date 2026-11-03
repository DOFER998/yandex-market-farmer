from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AccountAction(StrEnum):
    BUSY = 'busy'
    HOME = 'home'
    ADD = 'add'
    RENAME = 'rename'
    DELETE = 'delete'
    DELETE_CONFIRM = 'delete_yes'
    SETTINGS = 'settings'
    INTERVAL = 'interval'
    CONNECT = 'connect'
    QR_START = 'qr_start'
    QR_SAVE = 'qr_save'
    QR_CANCEL = 'qr_cancel'
    RESUME = 'resume'
    NOTIFY_ON = 'notify_on'
    NOTIFY_OFF = 'notify_off'
    BALANCE = 'balance'
    COLLECT = 'collect'
    COLLECT_GAMES = 'games'
    GAMES = 'games_menu'
    LIVE = 'live'
    LIVE_REFRESH = 'live_refresh'
    GAMES_ON = 'games_on'
    GAMES_OFF = 'games_off'
    LIVE_ON = 'live_on'
    LIVE_OFF = 'live_off'
    HISTORY = 'history'
    STATS = 'stats'
    STATUS = 'status'
    AUTO_ON = 'on'
    AUTO_OFF = 'off'


class AccountCallback(CallbackData, prefix='account'):
    action: AccountAction
    account_id: str
    value: int = 0
