import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardMarkup

from src.services.emoji import EmojiCatalog
from src.services.screens import ScreenService


def test_notification_becomes_active_screen_and_repeated_open_does_not_edit_again():
    async def scenario():
        bot = SimpleNamespace(edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock())

        def message(identifier):
            return SimpleNamespace(chat=SimpleNamespace(id=1), message_id=identifier, bot=bot)

        main, notification = message(10), message(20)
        screens = ScreenService(EmojiCatalog())
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        await screens.show(main, 'Account', keyboard, screens.begin(main))
        old_revision = screens.begin(main)
        revision = screens.begin(notification, activate=True)
        await screens.show(notification, 'Live', keyboard, revision)
        assert bot.edit_message_text.await_args.kwargs['message_id'] == 20
        bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=1, message_id=10, reply_markup=None
        )
        await screens.show(main, 'Stale result', keyboard, old_revision)
        await screens.show(
            notification, 'Live', keyboard, screens.begin(notification, activate=True)
        )
        assert bot.edit_message_text.await_count == 2
        await screens.show(notification, 'Updated Live', keyboard, screens.begin(notification))
        assert bot.edit_message_text.await_count == 3
        assert bot.edit_message_text.await_args.kwargs['message_id'] == 20

    asyncio.run(scenario())
