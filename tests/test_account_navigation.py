import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.enums import ChatType
from aiogram.types import Chat, Message

from src.callback_data.account import AccountAction as Action
from src.callback_data.account import AccountCallback
from src.database.models import Account, FarmRun
from src.routers.private.callbacks.accounts import status_callback
from src.routers.private.callbacks.farming import farming_callback
from src.services.emoji import EmojiCatalog


@pytest.mark.parametrize(
    'action', [Action.BALANCE, Action.COLLECT, Action.COLLECT_GAMES, Action.RESUME]
)
@pytest.mark.parametrize('state', ['success', 'failed'])
def test_operation_result_returns_to_current_account_without_repeating_operation(action, state):
    async def scenario():
        message = Message(
            message_id=10, date=datetime.now(UTC), chat=Chat(id=123, type=ChatType.PRIVATE)
        )
        query = SimpleNamespace(message=message, answer=AsyncMock())
        account = Account(id='main', display_name='c0mrade')
        run = FarmRun(account_id=account.id, state=state, balance_after=42)
        accounts = SimpleNamespace(
            get=AsyncMock(return_value=account),
            run_operations=AsyncMock(return_value=[]),
            latest_run=AsyncMock(return_value=run),
        )
        farming = SimpleNamespace(run=AsyncMock(return_value=run))
        screens = SimpleNamespace(
            icons=EmojiCatalog(), begin=Mock(return_value=1), show=AsyncMock()
        )
        await farming_callback(
            query, AccountCallback(action=action, account_id=account.id), accounts, farming, screens
        )
        loading = screens.show.await_args_list[0].args[2]
        assert (
            AccountCallback.unpack(loading.inline_keyboard[0][0].callback_data).action
            == Action.BUSY
        )
        result = screens.show.await_args.args[2]
        buttons = [button for row in result.inline_keyboard for button in row]
        assert len(buttons) == 1
        back = AccountCallback.unpack(buttons[0].callback_data)
        assert back.action == Action.STATUS
        assert back.account_id == account.id
        assert 'Назад к аккаунту' in buttons[0].text
        await status_callback(query, back, accounts, screens, SimpleNamespace(clear=AsyncMock()))
        assert 'c0mrade' in screens.show.await_args.args[1]
        assert '42' in screens.show.await_args.args[1]
        farming.run.assert_awaited_once()
        accounts.latest_run.assert_awaited_once_with(account.id)

    asyncio.run(scenario())
