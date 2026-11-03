from types import SimpleNamespace

import pytest

from src.market.client import PAGE, MarketClient
from src.market.errors import AccessDenied, AuthRequired
from tests.test_core import HTML


class RedirectWire:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def response(status=200, location='', text=''):
    return SimpleNamespace(status_code=status, headers={'Location': location}, text=text)


def test_bootstrap_follows_observed_passport_update_and_returns_to_market():
    wire = RedirectWire(
        response(302, 'https://passport.yandex.ru/auth/update?retpath=' + PAGE),
        response(302, PAGE),
        response(text=HTML),
    )
    client = MarketClient(wire, interval=0)
    assert client.check_auth().login == 'alice'
    assert len(wire.calls) == 3
    assert all(call[0] == 'GET' and 'sk' not in call[2]['headers'] for call in wire.calls)


@pytest.mark.parametrize(
    'target',
    [
        'https://evil.example/auth/update',
        'http://passport.yandex.ru/auth/update',
        'https://passport.yandex.ru:444/auth/update',
        'https://user@passport.yandex.ru/auth/update',
        'https://passport.yandex.ru/auth',
    ],
)
def test_bootstrap_does_not_follow_unapproved_destinations(target):
    wire = RedirectWire(response(302, target))
    with pytest.raises(AuthRequired):
        MarketClient(wire, interval=0).check_auth()
    assert len(wire.calls) == 1


def test_refresh_stops_at_captcha():
    wire = RedirectWire(response(302, 'https://passport.yandex.ru/showcaptcha'))
    with pytest.raises(AccessDenied):
        MarketClient(wire, interval=0).check_auth()
    assert len(wire.calls) == 1


def test_refresh_loop_is_bounded():
    wire = RedirectWire(
        *[response(302, 'https://passport.yandex.ru/auth/update') for _ in range(8)]
    )
    with pytest.raises(AuthRequired):
        MarketClient(wire, interval=0).check_auth()
    assert len(wire.calls) <= 4


def test_post_is_never_replayed_through_refresh():
    wire = RedirectWire(response(302, 'https://passport.yandex.ru/auth/update'))
    with pytest.raises(AuthRequired):
        MarketClient(wire, interval=0)._send('POST', PAGE, headers={}, mutation=True)
    assert len(wire.calls) == 1


def test_refreshed_cookies_survive_client_close(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from src.market import factory
    from src.market.session import SessionStore
    from tests.test_cookies import source

    path = tmp_path / 'sessions' / 'main.txt'
    with SessionStore(path) as store:
        store.import_file(source(tmp_path))

    @contextmanager
    def session(jar):
        wire = RedirectWire(
            response(302, 'https://passport.yandex.ru/auth/update'),
            response(302, PAGE),
            response(text=HTML),
        )
        for cookie in jar:
            if cookie.name == 'Session_id':
                cookie.value = 'rotated-test-cookie'
        wire.cookies = SimpleNamespace(jar=jar)
        yield wire

    monkeypatch.setattr(factory, 'make_session', session)
    with factory.account_client(path, interval=0) as client:
        assert client.check_auth().login == 'alice'
        assert client.session_refreshed
    with SessionStore(path) as store:
        assert (
            next(c.value for c in store.load() if c.name == 'Session_id') == 'rotated-test-cookie'
        )
