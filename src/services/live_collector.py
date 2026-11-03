"""Synchronous live lifecycle; the async service owns persistence and scheduling."""

import time
from dataclasses import dataclass
from math import ceil
from pathlib import Path

from loguru import logger

from src.database.models import BonusStatus, FarmRun
from src.market.errors import RateLimited
from src.market.live_client import LiveCursor
from src.services.ports import LiveClientFactory


@dataclass
class LiveRuntime:
    cursor: LiveCursor
    progress_at: float


class LiveCollector:
    def __init__(self, factory: LiveClientFactory, session_dir: Path) -> None:
        self._factory = factory
        self._session_dir = session_dir
        self.cursors: dict[str, LiveRuntime] = {}

    def forget(self, account_id: str) -> None:
        self.cursors.pop(account_id, None)

    def read(self, account_id: str) -> LiveCursor:
        with self._factory(self._session_dir / f'{account_id}.txt') as client:
            client.check_auth(force=False)
            return client.bootstrap_live()

    @staticmethod
    def status(account_id: str, cursor: LiveCursor) -> BonusStatus:
        limit = min(cursor.state.max, cursor.state.coinSettings.max)
        remaining = max(0, limit - cursor.state.balance)
        settings = cursor.state.coinSettings
        state = (
            'limit'
            if remaining == 0
            else 'waiting_stream'
            if cursor.ended
            else 'disabled'
            if not settings.enabled or settings.amount == 0
            else 'collecting'
        )
        seconds = (
            ceil(remaining / settings.amount) * max(cursor.interval, settings.periodSeconds)
            if state == 'collecting'
            else 0
            if state == 'limit'
            else None
        )
        return BonusStatus(
            account_id=account_id,
            source='live',
            state=state,
            balance=cursor.state.balance,
            limit=limit,
            remaining_seconds=seconds,
        )

    def poll(
        self, account_id: str, run: FarmRun
    ) -> tuple[BonusStatus | None, float, Exception | None]:
        try:
            with self._factory(self._session_dir / f'{account_id}.txt') as client:
                client.check_auth(force=False)
                runtime = self.cursors.get(account_id)
                if runtime is None or not runtime.cursor.active:
                    previous = runtime.cursor if runtime else None
                    cursor = client.bootstrap_live()
                    if previous and previous.ended and previous.stream_id == cursor.stream_id:
                        cursor.ended = True
                    runtime = LiveRuntime(cursor, time.monotonic())
                    self.cursors[account_id] = runtime
                cursor = runtime.cursor
                status = self.status(account_id, cursor)
                if status.state != 'collecting':
                    run.state = 'skipped'
                    if status.state == 'limit':
                        self.cursors.pop(account_id, None)
                    return status, 3600 if status.state == 'limit' else 60, None
                run.balance_before = client.balance()
                before = cursor.state.balance
                sent_at = time.monotonic()
                client.poll_live(cursor)
                run.balance_after = client.balance()
                run.state = 'success' if cursor.state.balance > before else 'unavailable'
                if cursor.state.balance != before:
                    runtime.progress_at = time.monotonic()
                status = self.status(account_id, cursor)
                if cursor.ended and status.state != 'limit':
                    replacement = client.bootstrap_live()
                    if replacement.stream_id != cursor.stream_id:
                        runtime = LiveRuntime(replacement, time.monotonic())
                        self.cursors[account_id] = runtime
                        status = self.status(account_id, replacement)
                        cursor = replacement
                if status.state == 'collecting' and time.monotonic() - runtime.progress_at >= max(
                    120, 4 * cursor.interval
                ):
                    status.state, status.remaining_seconds = 'stalled', None
                    self.cursors.pop(account_id, None)
                delay = (
                    3600
                    if status.state == 'limit'
                    else 60
                    if status.state in ('waiting_stream', 'disabled', 'stalled')
                    else max(1, cursor.interval - (time.monotonic() - sent_at))
                )
                if status.state == 'limit':
                    self.cursors.pop(account_id, None)
                logger.info(
                    'Live polling: account={} state={} counter={} max={} wallet={} next_seconds={}',
                    account_id,
                    status.state,
                    status.balance,
                    status.limit,
                    run.balance_after,
                    round(delay, 1),
                )
                return status, delay, None
        except Exception as exc:
            delay = max(60, exc.retry_after) if isinstance(exc, RateLimited) else 300
            return None, delay, exc
