from src.database.models import FarmRun
from src.market.models import DailyStatus
from src.views.accounts import format_run


def test_failed_run_keeps_last_known_balance_and_explains_recovery():
    text = format_run(
        FarmRun(account_id='main', state='failed', balance_before=37, error_code='ProtocolError')
    )
    assert '37' in text
    assert 'None' not in text
    assert 'failed' not in text
    assert 'ProtocolError' not in text
    assert 'Маркет' in text


def test_unconfirmed_claim_is_not_presented_as_success():
    text = format_run(
        FarmRun(account_id='main', state='partial', balance_before=37, balance_after=37)
    )
    assert 'подтвержден' in text.lower()


def test_stale_job_does_not_replace_newer_navigation():
    import asyncio
    from types import SimpleNamespace

    from aiogram.types import InlineKeyboardMarkup

    from src.services.emoji import EmojiCatalog
    from src.services.screens import ScreenService

    async def scenario():
        edits = []

        class Bot:
            async def edit_message_text(self, **kwargs):
                edits.append(kwargs['text'])

        message = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=10, bot=Bot())
        screens = ScreenService(EmojiCatalog())
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        job = screens.begin(message)
        await screens.show(message, 'loading', keyboard, job)
        menu = screens.begin(message)
        await screens.show(message, 'menu', keyboard, menu)
        await screens.show(message, 'old job result', keyboard, job)
        assert edits == ['loading', 'menu']

    asyncio.run(scenario())


def test_deleted_screen_is_recreated_and_emoji_rejection_falls_back():
    import asyncio
    from types import SimpleNamespace

    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from src.services.emoji import EmojiCatalog
    from src.services.screens import ScreenService

    async def scenario():
        edits = []
        sent = []

        class Bot:
            async def edit_message_text(self, **kwargs):
                edits.append(kwargs)
                if len(edits) == 1:
                    raise TelegramBadRequest(
                        method=EditMessageText(**kwargs), message='CUSTOM_EMOJI_INVALID'
                    )

        async def answer(text, **kwargs):
            sent.append(text)
            return SimpleNamespace(message_id=99)

        message = SimpleNamespace(
            chat=SimpleNamespace(id=1), message_id=10, bot=Bot(), answer=answer
        )
        icons = EmojiCatalog(ids={'home': '123'})
        screens = ScreenService(icons)
        revision = screens.begin(message)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text='Home', callback_data='home', icon_custom_emoji_id='123'
                    )
                ]
            ]
        )
        await screens.show(message, icons.heading('home', 'Menu'), keyboard, revision)
        assert len(edits) == 2
        assert '<tg-emoji' not in edits[1]['text']
        assert edits[1]['reply_markup'].inline_keyboard[0][0].icon_custom_emoji_id is None

        async def deleted(**kwargs):
            raise TelegramBadRequest(
                method=EditMessageText(**kwargs), message='message to edit not found'
            )

        message.bot.edit_message_text = deleted
        await screens.show(message, 'New', keyboard, screens.begin(message))
        assert sent == ['New']

    asyncio.run(scenario())


def test_update_shutdown_drains_inflight_handlers():
    import asyncio

    from src.middlewares.updates import UpdateTasks

    async def scenario():
        scope = UpdateTasks()
        started, finish = asyncio.Event(), asyncio.Event()

        async def handler(event, data):
            started.set()
            await finish.wait()

        running = asyncio.create_task(scope(handler, None, {}))
        await started.wait()
        draining = asyncio.create_task(scope.close())
        await asyncio.sleep(0)
        assert not draining.done()
        finish.set()
        await asyncio.wait_for(draining, 1)
        await running
        assert not scope.active
        assert scope.closing

    asyncio.run(scenario())


def test_read_only_worker_fetches_balance_once():
    from contextlib import contextmanager
    from pathlib import Path

    from src.database.models import Account
    from src.services.worker import FarmingWorker

    calls = []

    class Client:
        def check_auth(self):
            pass

        def daily_status(self):
            return DailyStatus(available=False)

        def balance(self):
            calls.append('balance')
            return 37

    class Journal:
        def call(self, name, action):
            return action()

        def snapshot(self, coins):
            assert coins == 37

    @contextmanager
    def factory(path):
        yield Client()

    run = FarmRun(account_id='main')
    FarmingWorker(factory, Path('.')).execute(Account(id='main'), run, Journal(), True)
    assert calls == ['balance']
    assert run.balance_before == run.balance_after == 37
    assert run.state == 'success'


def test_account_list_is_paginated_with_bounded_buttons():
    from src.callback_data.account import AccountAction, AccountCallback
    from src.database.models import Account
    from src.keyboards.accounts import accounts_keyboard

    accounts = [Account(id=f'a{i}') for i in range(100)]
    for page in (0, 6, 12, 1000, -1):
        keyboard = accounts_keyboard(accounts, page=page)
        buttons = [b for row in keyboard.inline_keyboard for b in row]
        assert len(buttons) <= 11
        assert any(
            AccountCallback.unpack(b.callback_data).action == AccountAction.ADD for b in buttons
        )


def test_history_and_connection_keep_icons_when_pack_has_no_match():
    from src.callback_data.account import AccountAction, AccountCallback
    from src.database.models import Account
    from src.keyboards.accounts import account_keyboard
    from src.services.emoji import EmojiCatalog

    keyboard = account_keyboard(Account(id='main'), EmojiCatalog(ids={}))
    labels = {
        AccountCallback.unpack(b.callback_data).action: b.text
        for row in keyboard.inline_keyboard
        for b in row
    }
    assert labels[AccountAction.HISTORY] == '🕓 История'
    assert labels[AccountAction.CONNECT] == '🔗 Подключение'


def test_owner_supplied_emoji_ids_are_used_on_buttons():
    from src.callback_data.account import AccountAction, AccountCallback
    from src.database.models import Account
    from src.keyboards.accounts import account_keyboard, settings_keyboard
    from src.services.emoji import EmojiCatalog

    icons = EmojiCatalog()
    account = Account(id='main')
    buttons = [
        b
        for keyboard in (account_keyboard(account, icons), settings_keyboard(account, icons))
        for row in keyboard.inline_keyboard
        for b in row
    ]
    identifiers = {
        AccountCallback.unpack(b.callback_data).action: b.icon_custom_emoji_id for b in buttons
    }
    assert identifiers[AccountAction.RENAME] == '6050847684355428245'
    assert identifiers[AccountAction.HISTORY] == '5775896410780079073'
    assert identifiers[AccountAction.CONNECT] == '5895364284782743985'


def test_telegram_time_uses_unix_entities_and_relative_format():
    from datetime import UTC, datetime

    from src.database.models import Account
    from src.views.accounts import reward_timing
    from src.views.time import telegram_time

    stamp = datetime.fromtimestamp(1793307600, UTC)
    assert 'unix="1793307600" format="dT"' in telegram_time(stamp, style='dT')
    account = Account(id='main', auto_collect=True, daily_reward_at=stamp, next_run_at=stamp)
    text = reward_timing(account)
    assert 'format="r"' in text and 'format="dT"' in text


def test_markup_sections_escape_names_and_do_not_nest_blockquotes():
    from html.parser import HTMLParser

    from src.database.models import Account, FarmRun
    from src.services.accounts import AccountHistory
    from src.views.accounts import connection_text, history_text, settings_text, status_text

    class Markup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []

        def handle_starttag(self, tag, attrs):
            if tag == 'blockquote':
                assert 'blockquote' not in self.stack
            self.stack.append(tag)

        def handle_endtag(self, tag):
            assert self.stack.pop() == tag

    account = Account(id='main', display_name='<Основной & семья>')
    for text in [
        status_text(account),
        settings_text(account),
        connection_text(account),
        history_text(AccountHistory(runs=[FarmRun(account_id='main')], operations=[])),
    ]:
        parser = Markup()
        parser.feed(text)
        assert not parser.stack
        assert '<blockquote' in text
        assert '<Основной' not in text
        assert len(text) < 4096
    assert '<blockquote expandable>' in connection_text(account)
    assert '&lt;Основной &amp; семья&gt;' in status_text(account)


def test_localized_dates_in_quotes_keep_relative_times_outside():
    from html.parser import HTMLParser

    from src.database.models import Account, FarmRun, now
    from src.views.accounts import settings_text, status_text

    class TimeMarkup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.times = 0

        def handle_starttag(self, tag, attrs):
            if tag == 'tg-time':
                if dict(attrs)['format'] == 'r':
                    assert not self.stack, self.stack
                else:
                    assert self.stack == ['blockquote'], self.stack
                self.times += 1
            self.stack.append(tag)

        def handle_endtag(self, tag):
            assert self.stack.pop() == tag

    account = Account(id='main', auto_collect=True, daily_reward_at=now())
    run = FarmRun(account_id='main', balance_after=37)
    status = status_text(account, run)
    for text in (status, settings_text(account)):
        parser = TimeMarkup()
        parser.feed(text)
        assert parser.times >= 3
        assert text.count('<blockquote>') == 2
        assert 'МСК' not in text
    assert status.index('Баланс обновлён') < status.index('Ежедневная награда')


def test_telegram_time_preserves_instant_across_input_timezones():
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from src.views.time import telegram_time

    instant = datetime(2026, 10, 29, 21, tzinfo=UTC)
    assert telegram_time(instant) == telegram_time(instant.astimezone(ZoneInfo('Asia/Tokyo')))


def test_collection_report_distinguishes_unavailable_daily_from_games_failure():
    from src.database.models import FarmRun, Operation
    from src.views.accounts import format_run

    run = FarmRun(account_id='main', state='failed', error_code='ProtocolError', balance_before=37)
    operations = [
        Operation(account_id='main', run_id=run.id, kind='daily', state='unavailable'),
        Operation(
            account_id='main',
            run_id=run.id,
            kind='games',
            state='failed',
            error_code='ProtocolError',
        ),
        Operation(account_id='main', run_id='another-run', kind='auth', state='failed'),
    ]
    text = format_run(run, operations=operations)
    assert 'Ежедневная награда сейчас недоступна' in text
    assert 'Запрос начисления не отправлялся' in text
    assert 'Не удалось прочитать список игровых наград' in text
    assert 'Проверка входа' not in text
    assert 'ProtocolError' not in text


def test_qr_photo_is_removed_before_returning_to_text_menu():
    import asyncio
    from types import SimpleNamespace

    from aiogram.types import InlineKeyboardMarkup

    from src.services.emoji import EmojiCatalog
    from src.services.screens import ScreenService

    async def scenario():
        calls = []

        class Bot:
            async def edit_message_media(self, **kwargs):
                calls.append('qr')

            async def delete_message(self, **kwargs):
                calls.append('delete_qr')

        async def answer(*args, **kwargs):
            calls.append('menu')
            return SimpleNamespace(message_id=11)

        message = SimpleNamespace(
            chat=SimpleNamespace(id=123), message_id=10, bot=Bot(), answer=answer
        )
        screens = ScreenService(EmojiCatalog())
        revision = screens.begin(message)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        await screens.show_qr(message, b'png', 'QR', keyboard, revision)
        await screens.show(message, 'Ready', keyboard, revision)
        assert calls == ['qr', 'delete_qr', 'menu']
        assert screens._chats[123].message_id == 11
        assert not screens._chats[123].photo

    asyncio.run(scenario())
