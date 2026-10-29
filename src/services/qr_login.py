"""One temporary login per owner chat, with isolated cookies and bounded lifetime."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http.cookiejar import CookieJar
from secrets import randbelow

from loguru import logger
from pydantic import SecretStr

from src.common.tasks import settle
from src.market.client import MarketClient
from src.market.qr_auth import QrAuth
from src.market.transport import make_session


@dataclass
class LoginAttempt:
    account_id: str
    nonce: int
    session: object
    auth: QrAuth
    link: SecretStr
    login: str = ''
    balance: int | None = None
    error: str | None = None
    task: asyncio.Task | None = None


class QrLoginService:
    def __init__(
        self,
        *,
        session_factory=make_session,
        auth_factory=QrAuth,
        client_factory=MarketClient,
        poll_seconds=5,
    ):
        self._sessions = session_factory
        self._auth = auth_factory
        self._clients = client_factory
        self._poll_seconds = poll_seconds
        self._attempts: dict[int, LoginAttempt] = {}

    @staticmethod
    async def _io(action):
        result, cancelled = await settle(asyncio.create_task(asyncio.to_thread(action)))
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def begin(self, chat_id: int, account_id: str) -> LoginAttempt:
        await self.cancel(chat_id)
        session = self._sessions(CookieJar())
        auth = self._auth(session)
        try:
            link = await self._io(auth.start)
        except BaseException:
            session.close()
            raise
        attempt = LoginAttempt(account_id, randbelow(2**31), session, auth, link)
        self._attempts[chat_id] = attempt
        return attempt

    def get(self, chat_id: int, account_id: str, nonce: int) -> LoginAttempt | None:
        attempt = self._attempts.get(chat_id)
        return (
            attempt
            if attempt and attempt.account_id == account_id and attempt.nonce == nonce
            else None
        )

    def watch(self, chat_id: int, callback: Callable[[LoginAttempt], Awaitable[None]]) -> None:
        attempt = self._attempts[chat_id]
        attempt.task = asyncio.create_task(self._watch(attempt, callback))

    async def _watch(self, attempt: LoginAttempt, callback) -> None:
        try:
            while True:
                await asyncio.sleep(self._poll_seconds)
                if await self._io(attempt.auth.poll):
                    break
            client = self._clients(attempt.session, allow_claims=False)
            context = await self._io(client.check_auth)
            attempt.balance = await self._io(client.balance)
            attempt.login = context.login
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            attempt.error = type(exc).__name__
        try:
            await callback(attempt)
        except Exception:
            # A failed UI delivery must not expose auth material or kill polling infrastructure.
            logger.warning('QR login screen delivery failed')
        if not attempt.error:
            await asyncio.sleep(300)
            attempt.error = 'SessionError'
            attempt.balance = None
            attempt.session.close()
            try:
                await callback(attempt)
            except Exception:
                logger.warning('Expired QR screen delivery failed')
        else:
            attempt.session.close()

    async def cancel(self, chat_id: int) -> None:
        attempt = self._attempts.pop(chat_id, None)
        if attempt:
            if attempt.task:
                attempt.task.cancel()
                await asyncio.gather(attempt.task, return_exceptions=True)
            attempt.session.close()

    async def close(self) -> None:
        for chat_id in list(self._attempts):
            await self.cancel(chat_id)
