import asyncio
from contextlib import AsyncExitStack

from aiogram import Bot
from loguru import logger

from src.bootstrap import Application, application
from src.common.config import Environment, get_environment
from src.common.health import heartbeat
from src.common.utils import setup_logging
from src.dispatcher import create_dispatcher
from src.middlewares.updates import UpdateTasks
from src.services.emoji import EmojiCatalog
from src.services.notifications import NotificationService
from src.services.qr_login import QrLoginService
from src.services.scheduler import SchedulerService
from src.services.screens import ScreenService


async def stop_task(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def serve(settings: Environment, app: Application) -> None:
    """Composition root: acquire resources, wire dependencies, run polling, unwind."""
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.database.locks.guard('__bot_process__'))
        bot = Bot(settings.bot.get_token())
        stack.push_async_callback(bot.session.close)

        notifications = NotificationService(bot, settings.bot.owner_id, app.accounts)
        scheduler = SchedulerService(app.accounts, app.farming, notifications, app.schedule_changes)
        icons = EmojiCatalog()
        updates = UpdateTasks()
        qr_login = QrLoginService()
        stack.push_async_callback(qr_login.close)
        dispatcher = create_dispatcher(
            accounts=app.accounts,
            farming=app.farming,
            owner_id=settings.bot.owner_id,
            screens=ScreenService(icons),
            updates=updates,
            qr_login=qr_login,
        )

        stack.push_async_callback(scheduler.stop)
        await scheduler.start()
        pulse = asyncio.create_task(heartbeat(settings.app.heartbeat_file))
        stack.push_async_callback(stop_task, pulse)

        stack.push_async_callback(updates.close)

        logger.info('Bot started: long polling; scheduler active')
        await dispatcher.start_polling(
            bot,
            handle_as_tasks=True,
            tasks_concurrency_limit=16,
            close_bot_session=False,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )


async def run() -> None:
    settings = get_environment()
    setup_logging(debug=settings.app.debug)
    async with application(settings) as app:
        await serve(settings, app)

    logger.info('Bot stopped')
