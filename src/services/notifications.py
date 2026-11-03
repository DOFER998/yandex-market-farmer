from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from loguru import logger

from src.callback_data.account import AccountAction
from src.common.utils.errors import error_details
from src.database.models import BonusStatus, FarmRun
from src.keyboards.accounts import account_button
from src.services.accounts import AccountService
from src.services.emoji import EmojiCatalog
from src.views.accounts import account_name, format_run
from src.views.bonus import bonus_text


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
            logger.error(
                'Notification failed: account={} run={} {}',
                run.account_id,
                run.id,
                error_details(exc),
            )

    async def report_bonus(self, status: BonusStatus, run: FarmRun | None = None) -> None:
        account = await self._accounts.get(status.account_id)
        icons = EmojiCatalog()
        if status.source == 'games' and run:
            operations = await self._accounts.run_operations(run)
            text = f'<b>{account_name(account)}</b>\n\n' + format_run(
                run, icons, operations=operations
            )
        else:
            text = bonus_text(account, status.source, status, icons)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    account_button(
                        'Открыть игры и задания' if status.source == 'games' else 'Открыть эфиры',
                        account.id,
                        AccountAction.GAMES if status.source == 'games' else AccountAction.LIVE,
                        icons=icons,
                        icon=status.source,
                    )
                ]
            ]
        )
        try:
            await self._bot.send_message(
                self._owner_id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
        except TelegramAPIError as exc:
            logger.error(
                'Bonus notification failed: account={} source={} {}',
                account.id,
                status.source,
                error_details(exc),
            )
