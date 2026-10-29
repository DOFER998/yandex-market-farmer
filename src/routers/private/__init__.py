from aiogram import Router

from src.routers.private.callbacks import accounts as account_callbacks
from src.routers.private.callbacks import farming as farming_callbacks
from src.routers.private.callbacks import history as history_callbacks
from src.routers.private.callbacks import qr_login as qr_callbacks
from src.routers.private.messages import accounts, fallback, menu


def get_routers() -> list[Router]:
    return [
        menu.router,
        accounts.router,
        account_callbacks.router,
        farming_callbacks.router,
        history_callbacks.router,
        qr_callbacks.router,
        fallback.router,
    ]
