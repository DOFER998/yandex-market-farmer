from types import SimpleNamespace

import pytest

from src.market.errors import AccessDenied, ProtocolError
from src.market.qr_auth import QrAuth


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def response(payload=None, *, text='', status=200, headers=None):
    import json

    return SimpleNamespace(
        status_code=status,
        text=json.dumps(payload) if payload is not None else text,
        headers=headers or {},
    )


def test_qr_flow_uses_same_track_and_gets_session_only_after_confirmation():
    session = Session(
        response(text='__CSRF__ = "private-csrf"'),
        response({'track_id': 'private-track'}),
        response({'link': 'https://passport.yandex.ru/auth/magic?code=private-code'}),
        response({'state': 'waiting'}),
        response({'state': 'otp_auth_finished', 'trackId': 'finished-track'}),
        response({'status': 'ok'}),
    )
    auth = QrAuth(session)
    link = auth.start()
    assert 'private-code' not in repr(link)
    assert not auth.poll()
    assert auth.poll()
    assert session.calls[-1][2]['data'] == {'track_id': 'finished-track'}
    assert session.calls[3][2]['json'] == {'track_id': 'private-track'}
    assert all('password' not in str(call[2]) for call in session.calls)
    assert len(session.calls) == 6
    assert auth.poll()  # Finalization must not repeat.
    assert len(session.calls) == 6


@pytest.mark.parametrize(
    'url',
    [
        'https://evil.example/auth',
        'http://passport.yandex.ru/auth',
        'https://passport.yandex.ru@evil.example/auth',
    ],
)
def test_qr_rejects_untrusted_links(url):
    auth = QrAuth(
        Session(
            response(text='__CSRF__ = "csrf"'),
            response({'track_id': 'track'}),
            response({'link': url}),
        )
    )
    with pytest.raises(ProtocolError):
        auth.start()


def test_qr_captcha_stops_without_following_redirect():
    session = Session(
        response(status=302, headers={'Location': 'https://passport.yandex.ru/showcaptcha'})
    )
    with pytest.raises(AccessDenied):
        QrAuth(session).start()
    assert len(session.calls) == 1


def test_qr_expiration_makes_no_more_requests():
    clock = [0.0]
    session = Session(
        response(text='__CSRF__ = "csrf"'),
        response({'track_id': 'track'}),
        response({'link': 'https://passport.yandex.ru/auth/magic'}),
    )
    auth = QrAuth(session, clock=lambda: clock[0])
    auth.start()
    clock[0] = 301
    with pytest.raises(ProtocolError):
        auth.poll()
    assert len(session.calls) == 3


def test_session_finalization_accepts_html_as_reference_does():
    auth = QrAuth(
        Session(
            response(text='__CSRF__ = "csrf"'),
            response({'track_id': 'track'}),
            response({'link': 'https://passport.yandex.ru/auth/magic'}),
            response({'state': 'otp_auth_finished', 'trackId': 'done'}),
            response(text='<html>Signed in</html>'),
        )
    )
    auth.start()
    assert auth.poll()
