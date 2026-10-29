import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from src.callback_data.account import AccountAction, AccountCallback
from src.database.models import Account
from src.dispatcher import create_dispatcher


class TelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, SendMessage):
            return Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type='private'),
                text=method.text,
            )
        return True

    async def stream_content(self, *args, **kwargs):
        yield b''


def test_dispatcher_silently_rejects_wrong_user_or_nonprivate_messages_and_callbacks():
    async def scenario():
        calls = []

        class DB:
            async def list(self):
                calls.append('accounts')
                return [Account(id='main')]

            async def latest_run(self, _):
                return None

            async def get(self, _):
                calls.append('account')
                return Account(id='main')

        accounts = DB()
        session = TelegramSession()
        bot = Bot('123456789:dummy_test_token', session=session)
        dispatcher = create_dispatcher(accounts=accounts, farming=SimpleNamespace(), owner_id=123)
        number = 0
        for callback in (False, True):
            for user, kind in [
                (456, 'private'),
                (123, 'group'),
                (123, 'supergroup'),
                (123, 'private'),
            ]:
                number += 1
                person = User(id=user, is_bot=False, first_name='Test')
                msg = Message(
                    message_id=number,
                    date=datetime.now(UTC),
                    chat=Chat(id=user, type=kind),
                    from_user=person,
                    text='/start',
                )
                update = Update(
                    update_id=number,
                    **(
                        {
                            'callback_query': CallbackQuery(
                                id=str(number),
                                from_user=person,
                                chat_instance='test',
                                message=msg,
                                data=AccountCallback(
                                    action=AccountAction.STATUS, account_id='main'
                                ).pack(),
                            )
                        }
                        if callback
                        else {'message': msg}
                    ),
                )
                count = len(session.sent)
                await dispatcher.feed_update(bot, update)
                if user != 123 or kind != 'private':
                    assert len(session.sent) == count
                else:
                    assert len(session.sent) > count
        assert calls == ['accounts', 'account']
        await bot.session.close()

    asyncio.run(scenario())


def test_typed_callbacks_use_dispatcher_services_and_edit_one_message():
    from aiogram.methods import AnswerCallbackQuery, EditMessageText

    from src.database.models import FarmRun

    async def scenario():
        calls = []

        class Accounts:
            async def run_operations(self, run):
                return []

            async def get(self, account_id):
                return Account(id=account_id, display_name='Основной')

            async def configure(self, account_id, **kwargs):
                calls.append(('configure', account_id, kwargs))
                return Account(id=account_id)

        class Farming:
            async def run(self, account_id, **kwargs):
                calls.append(('run', account_id, kwargs))
                return FarmRun(
                    account_id=account_id, state='success', balance_before=7, balance_after=7
                )

        session = TelegramSession()
        bot = Bot('123456789:dummy_test_token', session=session)
        dispatcher = create_dispatcher(accounts=Accounts(), farming=Farming(), owner_id=123)
        user = User(id=123, is_bot=False, first_name='Test')
        message = Message(message_id=8, date=datetime.now(UTC), chat=Chat(id=123, type='private'))
        for index, action in enumerate([AccountAction.AUTO_OFF, AccountAction.COLLECT]):
            query = CallbackQuery(
                id=str(index),
                from_user=user,
                chat_instance='test',
                message=message,
                data=AccountCallback(action=action, account_id='main').pack(),
            )
            await dispatcher.feed_update(bot, Update(update_id=index, callback_query=query))
        assert calls == [
            ('configure', 'main', {'enabled': False}),
            ('run', 'main', {'read_only': False, 'reconcile': False, 'target': 'daily'}),
        ]
        assert not any(isinstance(m, SendMessage) for m in session.sent)
        assert any(isinstance(m, AnswerCallbackQuery) for m in session.sent)
        edits = [m for m in session.sent if isinstance(m, EditMessageText)]
        assert all(m.message_id == 8 for m in edits)
        assert 'Собираю' in edits[1].text
        assert 'Готово' in edits[2].text
        await bot.session.close()

    asyncio.run(scenario())


def test_delivery_failure_is_logged_without_exception_payload(caplog):
    async def scenario():
        class Accounts:
            async def run_operations(self, run):
                return []

            async def list(self):
                return []

        class BrokenSession(TelegramSession):
            async def make_request(self, bot, method, timeout=None):
                raise RuntimeError('private-credential-value')

        bot = Bot('123456789:dummy_test_token', session=BrokenSession())
        dispatcher = create_dispatcher(accounts=Accounts(), farming=SimpleNamespace(), owner_id=123)
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=123, type='private'),
            from_user=User(id=123, is_bot=False, first_name='Test'),
            text='/start',
        )
        await dispatcher.feed_update(bot, Update(update_id=1, message=message))
        await bot.session.close()

    asyncio.run(scenario())
    assert 'private-credential-value' not in caplog.text


@pytest.fixture(autouse=True)
def isolated_router_modules():
    # Aiogram routers are attached once per process; each test models a fresh process.
    import importlib

    from src.routers.private import callbacks, messages

    for package, names in [
        (messages, ('menu', 'accounts', 'fallback')),
        (callbacks, ('accounts', 'farming', 'history', 'qr_login')),
    ]:
        for name in names:
            importlib.reload(importlib.import_module(f'{package.__name__}.{name}'))


def test_menu_is_locked_during_collect_and_restored_after_result():
    from aiogram.methods import EditMessageText

    from src.database.models import FarmRun

    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()

        class Accounts:
            async def run_operations(self, run):
                return []

            async def get(self, account_id):
                return Account(id=account_id, display_name='Основной')

            async def list(self):
                return [Account(id='main', display_name='Основной')]

        class Farming:
            async def run(self, account_id, **kwargs):
                started.set()
                await finish.wait()
                return FarmRun(account_id=account_id, state='success', balance_after=37)

        session = TelegramSession()
        bot = Bot('123456789:dummy_test_token', session=session)
        dispatcher = create_dispatcher(accounts=Accounts(), farming=Farming(), owner_id=123)
        user = User(id=123, is_bot=False, first_name='Test')
        message = Message(message_id=8, date=datetime.now(UTC), chat=Chat(id=123, type='private'))

        def update(action, number):
            return Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=str(number),
                    from_user=user,
                    chat_instance='test',
                    message=message,
                    data=AccountCallback(action=action, account_id='main').pack(),
                ),
            )

        collecting = asyncio.create_task(
            dispatcher.feed_update(bot, update(AccountAction.COLLECT, 1))
        )
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(dispatcher.feed_update(bot, update(AccountAction.HOME, 2)), 1)
        edits = [m for m in session.sent if isinstance(m, EditMessageText)]
        assert 'Собираю' in edits[-1].text
        assert 'Кнопки временно недоступны' in edits[-1].text
        assert session.sent[-1].text == 'Операция ещё выполняется. Дождись результата.'
        finish.set()
        await collecting
        edits = [m for m in session.sent if isinstance(m, EditMessageText)]
        assert 'Готово' in edits[-1].text
        await dispatcher.feed_update(bot, update(AccountAction.HOME, 3))
        edits = [m for m in session.sent if isinstance(m, EditMessageText)]
        assert 'Аккаунты Маркета' in edits[-1].text
        await bot.session.close()

    asyncio.run(scenario())


def test_account_creation_from_buttons():
    from aiogram.methods import EditMessageText

    async def scenario():
        created = []

        class Accounts:
            async def run_operations(self, run):
                return []

            async def create(self, name):
                created.append(name)
                return Account(id='acc_test', display_name=name)

        session = TelegramSession()
        bot = Bot('123456789:dummy_test_token', session=session)
        dispatcher = create_dispatcher(accounts=Accounts(), farming=SimpleNamespace(), owner_id=123)
        user = User(id=123, is_bot=False, first_name='Test')
        message = Message(message_id=8, date=datetime.now(UTC), chat=Chat(id=123, type='private'))
        query = CallbackQuery(
            id='1',
            from_user=user,
            chat_instance='test',
            message=message,
            data=AccountCallback(action=AccountAction.ADD, account_id='_').pack(),
        )
        await dispatcher.feed_update(bot, Update(update_id=1, callback_query=query))
        name = Message(
            message_id=9,
            date=datetime.now(UTC),
            chat=message.chat,
            from_user=user,
            text='Семейный <аккаунт>',
        )
        await dispatcher.feed_update(bot, Update(update_id=2, message=name))
        assert created == ['Семейный <аккаунт>']
        edits = [m for m in session.sent if isinstance(m, EditMessageText)]
        assert 'Семейный &lt;аккаунт&gt;' in edits[-1].text
        assert not any(isinstance(m, SendMessage) for m in session.sent)
        await bot.session.close()

    asyncio.run(scenario())


def test_home_after_delayed_rename_clears_form():
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class Accounts:
            async def run_operations(self, run):
                return []

            async def get(self, account_id):
                started.set()
                await release.wait()
                return Account(id=account_id)

            async def list(self):
                return [Account(id='main')]

        session = TelegramSession()
        bot = Bot('123456789:dummy_test_token', session=session)
        dispatcher = create_dispatcher(accounts=Accounts(), farming=SimpleNamespace(), owner_id=123)
        user = User(id=123, is_bot=False, first_name='Test')
        message = Message(message_id=8, date=datetime.now(UTC), chat=Chat(id=123, type='private'))

        def update(action, number):
            return Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=str(number),
                    from_user=user,
                    chat_instance='test',
                    message=message,
                    data=AccountCallback(action=action, account_id='main').pack(),
                ),
            )

        rename = asyncio.create_task(dispatcher.feed_update(bot, update(AccountAction.RENAME, 1)))
        await started.wait()
        home = asyncio.create_task(dispatcher.feed_update(bot, update(AccountAction.HOME, 2)))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(rename, home)
        state = FSMContext(
            storage=dispatcher.storage, key=StorageKey(bot_id=bot.id, chat_id=123, user_id=123)
        )
        assert await state.get_state() is None
        await bot.session.close()

    asyncio.run(scenario())


def test_concurrent_form_submissions_create_only_one_account():
    async def scenario():
        created = []

        class Accounts:
            async def run_operations(self, run):
                return []

            async def create(self, name):
                await asyncio.sleep(0.01)
                created.append(name)
                return Account(id='new', display_name=name)

            async def list(self):
                return [Account(id='new', display_name=created[0])] if created else []

        session = TelegramSession()
        bot = Bot('123456789:dummy_test_token', session=session)
        dispatcher = create_dispatcher(accounts=Accounts(), farming=SimpleNamespace(), owner_id=123)
        user = User(id=123, is_bot=False, first_name='Test')
        message = Message(message_id=8, date=datetime.now(UTC), chat=Chat(id=123, type='private'))
        query = CallbackQuery(
            id='1',
            from_user=user,
            chat_instance='test',
            message=message,
            data=AccountCallback(action=AccountAction.ADD, account_id='_').pack(),
        )
        await dispatcher.feed_update(bot, Update(update_id=1, callback_query=query))

        def name_update(number):
            return Update(
                update_id=number,
                message=Message(
                    message_id=number,
                    date=datetime.now(UTC),
                    chat=message.chat,
                    from_user=user,
                    text='Основной',
                ),
            )

        await asyncio.gather(
            dispatcher.feed_update(bot, name_update(2)), dispatcher.feed_update(bot, name_update(3))
        )
        assert created == ['Основной']
        await bot.session.close()

    asyncio.run(scenario())


def test_delete_requires_current_confirmation_and_no_cancels():
    from aiogram.enums import ButtonStyle
    from aiogram.methods import EditMessageText

    async def scenario():
        deleted = []

        class Accounts:
            async def get(self, account_id):
                return Account(id=account_id, display_name='<Основной>')

            async def delete(self, account_id):
                deleted.append(account_id)

            async def list(self):
                return []

        session = TelegramSession()
        bot = Bot('123456789:dummy_test_token', session=session)
        dispatcher = create_dispatcher(accounts=Accounts(), farming=None, owner_id=123)
        message = Message(message_id=8, date=datetime.now(UTC), chat=Chat(id=123, type='private'))
        user = User(id=123, is_bot=False, first_name='Test')
        counter = 0

        async def click(action, value=0):
            nonlocal counter
            counter += 1
            query = CallbackQuery(
                id=str(counter),
                from_user=user,
                chat_instance='test',
                message=message,
                data=AccountCallback(action=action, account_id='main', value=value).pack(),
            )
            await dispatcher.feed_update(bot, Update(update_id=counter, callback_query=query))

        await click(AccountAction.DELETE_CONFIRM)  # Cannot bypass the question.
        assert not deleted
        await click(AccountAction.DELETE)
        screen = [x for x in session.sent if isinstance(x, EditMessageText)][-1]
        assert '&lt;Основной&gt;' in screen.text
        yes, no = screen.reply_markup.inline_keyboard[0]
        assert yes.style == ButtonStyle.DANGER
        assert no.style == ButtonStyle.SUCCESS
        assert yes.icon_custom_emoji_id == '5774022692642492953'
        assert no.icon_custom_emoji_id == '5960671702059848143'
        nonce = AccountCallback.unpack(yes.callback_data).value
        assert not deleted
        await click(AccountAction.SETTINGS)  # No, leave account.
        await click(AccountAction.DELETE_CONFIRM, nonce)
        assert not deleted
        await click(AccountAction.DELETE)
        screen = [x for x in session.sent if isinstance(x, EditMessageText)][-1]
        nonce = AccountCallback.unpack(
            screen.reply_markup.inline_keyboard[0][0].callback_data
        ).value
        await click(AccountAction.DELETE_CONFIRM, nonce)
        await click(AccountAction.DELETE_CONFIRM, nonce)  # Duplicate callback.
        assert deleted == ['main']
        await bot.session.close()

    asyncio.run(scenario())


def test_qr_login_waits_for_verification_and_explicit_save(monkeypatch):
    from aiogram.methods import EditMessageMedia
    from pydantic import SecretStr

    from src.routers.private.callbacks import qr_login as module

    monkeypatch.setattr(module, 'qr_png', lambda _: b'png')

    async def scenario():
        saved = []
        attempt = SimpleNamespace(
            account_id='main',
            nonce=42,
            link=SecretStr('https://passport.yandex.ru/auth'),
            error=None,
            balance=None,
            login='',
            session=SimpleNamespace(cookies=SimpleNamespace(jar=object())),
        )

        class Accounts:
            async def get(self, _):
                return Account(id='main')

            async def attach_verified(self, account_id, jar, balance):
                saved.append((account_id, balance))
                return Account(id='main', auth_state='ready')

            async def latest_run(self, _):
                return None

        class Login:
            callback = None
            active = True

            async def begin(self, chat_id, account_id):
                assert chat_id == 123 and account_id == 'main'
                return attempt

            async def cancel(self, chat_id):
                self.active = False

            def get(self, chat_id, account_id, nonce):
                return attempt if self.active and nonce == 42 else None

            def watch(self, chat_id, callback):
                self.callback = callback

        login = Login()
        session = TelegramSession()
        bot = Bot('123456789:dummy_test_token', session=session)
        dispatcher = create_dispatcher(
            accounts=Accounts(), farming=None, owner_id=123, qr_login=login
        )
        message = Message(message_id=8, date=datetime.now(UTC), chat=Chat(id=123, type='private'))

        async def click(action, value=0):
            query = CallbackQuery(
                id=str(len(session.sent)),
                from_user=User(id=123, is_bot=False, first_name='Test'),
                chat_instance='test',
                message=message,
                data=AccountCallback(action=action, account_id='main', value=value).pack(),
            )
            await dispatcher.feed_update(
                bot, Update(update_id=len(session.sent), callback_query=query)
            )

        await click(AccountAction.QR_START)
        assert any(isinstance(x, EditMessageMedia) for x in session.sent)
        assert not saved
        attempt.balance, attempt.login = 96, 'owner'
        await login.callback(attempt)
        assert not saved
        await click(AccountAction.QR_SAVE, 42)
        assert saved == [('main', 96)]
        await click(AccountAction.QR_SAVE, 42)
        assert len(saved) == 1
        await bot.session.close()

    asyncio.run(scenario())
