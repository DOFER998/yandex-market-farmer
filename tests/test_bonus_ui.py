import asyncio
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.enums import ChatType
from aiogram.types import Chat, Message

from src.callback_data.account import AccountAction as Action
from src.callback_data.account import AccountCallback
from src.database.models import Account, BonusStatus, FarmRun
from src.keyboards.accounts import account_keyboard, bonus_keyboard
from src.routers.private.callbacks.bonus import bonus_callback
from src.routers.private.callbacks.farming import farming_callback
from src.services.emoji import EmojiCatalog
from src.views.bonus import bonus_text


def actions(keyboard):
    return [
        AccountCallback.unpack(b.callback_data).action
        for row in keyboard.inline_keyboard
        for b in row
    ]


def test_bonus_navigation_and_fallbacks():
    account = Account(id='main')
    icons = EmojiCatalog(enabled=False)
    keyboard = account_keyboard(account, icons)
    assert Action.GAMES in actions(keyboard) and Action.LIVE in actions(keyboard)
    assert Action.COLLECT_GAMES not in actions(keyboard)
    row = next(row for row in keyboard.inline_keyboard if row[0].text == '🎮 Игры и задания')
    assert row[1].text == '🎥 Эфиры'
    assert actions(bonus_keyboard(account, 'games')) == [
        Action.COLLECT_GAMES,
        Action.GAMES_OFF,
        Action.STATUS,
    ]
    assert actions(bonus_keyboard(account, 'live')) == [
        Action.LIVE_REFRESH,
        Action.LIVE_OFF,
        Action.STATUS,
    ]


def test_live_screen_progress_estimate_and_relative_time_outside_quotes():
    account = Account(id='main', display_name='<Alice>', auto_collect=True)
    status = BonusStatus(
        account_id=account.id,
        source='live',
        state='collecting',
        balance=35,
        limit=100,
        remaining_seconds=121,
        checked_at=datetime.now(UTC),
        next_check_at=datetime.now(UTC),
    )
    text = bonus_text(account, 'live', status)
    assert '&lt;Alice&gt;' in text and '35 / 100' in text and '65 монет' in text
    assert 'Ориентир до лимита — 2 мин 1 с' in text and 'без пауз' in text
    for quote in re.findall(r'<blockquote.*?</blockquote>', text, re.S):
        assert '<tg-time' not in quote
    for state in ('disabled', 'waiting_stream', 'limit', 'stalled'):
        status.state = state
        assert 'Ориентир до лимита' not in bonus_text(account, 'live', status)
    status.state = 'collecting'
    assert 'Ориентир до лимита' not in bonus_text(account, 'live', status, available=False)
    account.auto_collect = False
    text = bonus_text(account, 'live', status)
    assert 'включи общий автосбор' in text and 'Ориентир до лимита' not in text


def test_unknown_and_disabled_source_are_explained():
    text = bonus_text(Account(id='main'), 'games', None, available=False)
    assert 'Нет актуальных данных' in text
    assert 'Автосбор этого источника отключён' in text
    assert 'трёх проверенных недельных заданий' in text
    assert 'При включённой отправке результатов' in text
    assert 'два бесплатных задания колеса' in text


@pytest.mark.parametrize(
    'action', [Action.GAMES, Action.LIVE, Action.LIVE_REFRESH, Action.GAMES_OFF, Action.LIVE_ON]
)
def test_bonus_router_cached_open_refresh_and_source_only_toggle(action):
    async def scenario():
        account = Account(id='main', auto_collect=False)
        accounts = SimpleNamespace(get=AsyncMock(return_value=account))
        bonus = SimpleNamespace(
            status=AsyncMock(return_value=None),
            refresh_live=AsyncMock(return_value=None),
            configure=AsyncMock(return_value=account),
            available=Mock(return_value=True),
        )
        screens = SimpleNamespace(
            icons=EmojiCatalog(), begin=Mock(return_value=3), show=AsyncMock()
        )
        query = SimpleNamespace(
            answer=AsyncMock(),
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=1, type=ChatType.PRIVATE),
            ),
        )
        await bonus_callback(
            query, AccountCallback(action=action, account_id='main'), accounts, screens, bonus
        )
        if action == Action.LIVE_REFRESH:
            bonus.refresh_live.assert_awaited_once_with('main')
            bonus.status.assert_not_awaited()
            assert actions(screens.show.await_args_list[0].args[2]) == [Action.BUSY]
        else:
            bonus.refresh_live.assert_not_awaited()
            bonus.status.assert_awaited_once()
        if action in (Action.GAMES_OFF, Action.LIVE_ON):
            bonus.configure.assert_awaited_once_with(
                'main',
                'games' if action == Action.GAMES_OFF else 'live',
                action == Action.LIVE_ON,
            )
        else:
            bonus.configure.assert_not_awaited()
        assert not account.auto_collect
        assert actions(screens.show.await_args.args[2])[-1] == Action.STATUS

    asyncio.run(scenario())


def test_games_collection_uses_bonus_service_and_returns_to_games():
    async def scenario():
        account = Account(id='main')
        accounts = SimpleNamespace(
            get=AsyncMock(return_value=account), run_operations=AsyncMock(return_value=[])
        )
        farming = SimpleNamespace(run=AsyncMock())
        bonus = SimpleNamespace(
            run_games=AsyncMock(
                return_value=FarmRun(
                    account_id='main',
                    trigger='games',
                    state='no_rewards',
                )
            )
        )
        screens = SimpleNamespace(
            icons=EmojiCatalog(), begin=Mock(return_value=1), show=AsyncMock()
        )
        query = SimpleNamespace(
            answer=AsyncMock(),
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=1, type=ChatType.PRIVATE),
            ),
        )
        await farming_callback(
            query,
            AccountCallback(action=Action.COLLECT_GAMES, account_id='main'),
            accounts,
            farming,
            screens,
            bonus,
        )
        bonus.run_games.assert_awaited_once_with('main', automatic=False)
        farming.run.assert_not_awaited()
        assert actions(screens.show.await_args.args[2]) == [Action.GAMES]

    asyncio.run(scenario())
