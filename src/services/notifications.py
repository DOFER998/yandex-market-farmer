from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from loguru import logger

from src.database.models import FarmRun
from src.services.accounts import AccountService
from src.views.accounts import account_name, format_run


class NotificationService:
    def __init__(self, bot: Bot, owner_id: int, accounts: AccountService) -> None:
        self._accounts = accounts
        self._bot = bot
        self._owner_id = owner_id

    async def report_run(self, run: FarmRun) -> None:
        try:
            name = account_name(await self._accounts.get(run.account_id))
            operations = await self._accounts.run_operations(run)
            await self._bot.send_message(
                self._owner_id,
                f'<b>{name}</b>\n\n{format_run(run, operations=operations)}',
                parse_mode=ParseMode.HTML,
            )
        except TelegramAPIError as exc:
            logger.error('Notification failed: {}', type(exc).__name__)
