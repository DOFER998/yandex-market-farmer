"""Experimental Passport QR flow; independent of Telegram, storage and scheduler."""

import json
import re
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

from pydantic import SecretStr

from src.market.errors import AccessDenied, ProtocolError, RateLimited, TransportError
from src.market.page import challenge_page, challenge_url

PASSPORT = 'https://passport.yandex.ru'
BASE = PASSPORT + '/pwl-yandex'
API = BASE + '/api/passport'


def trusted_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == 'https'
        and parsed.hostname
        in {'passport.yandex.ru', 'yandex.ru', 'www.yandex.ru', 'market.yandex.ru'}
        and parsed.port in (None, 443)
        and not parsed.username
        and not parsed.password
    )


class QrAuth:
    """A short-lived login attempt in its own HTTP session. Never accepts a password."""

    def __init__(self, session, *, clock=time.monotonic, observe: Callable | None = None):
        self._session = session
        self._clock = clock
        self._observe = observe
        self._deadline = 0.0
        self._headers = {}
        self._auth = None
        self._finished = False
        self.stage = 'new'

    def _request(self, method, url, **kwargs):
        for _ in range(6):
            if not trusted_url(url):
                raise ProtocolError('Unexpected Passport redirect host')
            if challenge_url(url):
                raise AccessDenied('Manual browser verification required')
            try:
                response = self._session.request(
                    method, url, timeout=20, allow_redirects=False, **kwargs
                )
            except Exception:
                raise TransportError('Passport request failed') from None
            if self._observe:
                self._observe({'stage': self.stage, 'http_status': response.status_code})
            location = response.headers.get('Location') or response.headers.get('location')
            if location and challenge_url(urljoin(url, location)):
                raise AccessDenied('Manual browser verification required')
            if response.status_code == 429:
                raise RateLimited(60)
            if response.status_code in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                if response.status_code in (301, 302, 303):
                    method, kwargs = 'GET', {}
                continue
            if challenge_page(response.text):
                raise AccessDenied('Manual browser verification required')
            if response.status_code != 200:
                raise TransportError('Unexpected Passport HTTP status')
            return response.text
        raise ProtocolError('Too many Passport redirects')

    def _post(self, endpoint, **kwargs):
        raw = self._request('POST', API + endpoint, headers=self._headers, **kwargs)
        try:
            value = json.loads(raw)
        except ValueError:
            raise ProtocolError('Passport returned invalid JSON') from None
        if not isinstance(value, dict):
            raise ProtocolError('Passport returned invalid object')
        if self._observe:
            self._observe({'stage': self.stage, 'fields': sorted(value)})
        if value.get('errors') or value.get('error'):
            raise ProtocolError('Passport rejected login attempt')
        return value

    def start(self) -> SecretStr:
        if self.stage != 'new':
            raise ProtocolError('Login attempt already started')
        self._deadline = self._clock() + 300
        self.stage = 'bootstrap'
        page = self._request('GET', BASE)
        match = re.search(r'__CSRF__\s*=\s*"([^"\r\n]+)"', page)
        if not match:
            raise ProtocolError('Passport CSRF field missing')
        self._headers = {'X-CSRF-Token': match[1]}
        self.stage = 'create_track'
        self._auth = self._post('/auth/password/submit', json={'retpath': PASSPORT + '/'})
        track = self._auth.get('track_id')
        if not isinstance(track, str) or not track:
            raise ProtocolError('Passport login track missing')
        self.stage = 'create_link'
        result = self._post(
            '/auth/magic/code',
            data={
                'location_id': '0',
                'magic_track_id': track,
                'track_id': '',
            },
        )
        link = result.get('link')
        if not isinstance(link, str) or not trusted_url(link):
            raise ProtocolError('Unexpected Passport login link')
        self.stage = 'waiting'
        return SecretStr(link)

    def poll(self) -> bool:
        if self._finished:
            return True
        if self.stage != 'waiting' or self._clock() >= self._deadline:
            raise ProtocolError('Login attempt expired or not ready')
        result = self._post('/auth/magic/code/status', json=self._auth)
        if result.get('state') != 'otp_auth_finished':
            return False
        track = result.get('trackId')
        if not isinstance(track, str) or not track:
            raise ProtocolError('Confirmed Passport track missing')
        self.stage = 'get_session'
        self._request(
            'POST',
            API + '/sessions/get_session',
            headers=self._headers,
            data={'track_id': track},
        )
        self._finished = True
        self.stage = 'finished'
        return True
