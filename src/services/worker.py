from datetime import timedelta
from functools import partial
from pathlib import Path

from src.common.time import now
from src.database.models import Account, FarmRun
from src.market.errors import (
    AccessDenied,
    AuthRequired,
    MarketError,
    ProtocolError,
    RateLimited,
    SessionError,
    UncertainClaim,
)
from src.market.models import DailyStatus
from src.services.journal import OperationJournal
from src.services.ports import ClientFactory, RewardsClient
from src.services.reward_schedule import schedule_daily


class FarmingWorker:
    def __init__(self, factory: ClientFactory, session_dir: Path) -> None:
        self._factory = factory
        self._session_dir = session_dir

    def execute(
        self, account: Account, run: FarmRun, journal: OperationJournal, read_only: bool
    ) -> None:
        try:
            with self._factory(self._session_dir / f'{account.id}.txt') as client:
                journal.call('auth', client.check_auth)
                account.auth_state = 'ready'
                run.balance_before = journal.call('balance_before', client.balance)
                journal.snapshot(run.balance_before)
                run.state = 'success'
                if not read_only:
                    if run.trigger == 'games':
                        self._collect_games(client, run, journal)
                    else:
                        self._collect_daily(client, account, run, journal)
                if run.state in ('unavailable', 'no_rewards'):
                    run.balance_after = run.balance_before
                elif read_only:
                    run.balance_after = run.balance_before
                    schedule_daily(account, journal.call('daily_status', client.daily_status))
                else:
                    run.balance_after = journal.call('balance_after', client.balance)
                    journal.snapshot(run.balance_after)
        except Exception as exc:
            run.error_code = type(exc).__name__
            run.state = 'failed'
            if isinstance(exc, (AuthRequired, SessionError)):
                account.auth_state = 'expired'
                account.auto_collect = False
            elif isinstance(exc, AccessDenied):
                account.auth_state = 'blocked'
                account.auto_collect = False
            elif isinstance(exc, RateLimited):
                account.next_run_at = max(
                    account.next_run_at, now() + timedelta(seconds=min(exc.retry_after, 86400 * 30))
                )
            elif isinstance(exc, UncertainClaim) or not isinstance(exc, MarketError):
                run.state = 'uncertain'
                account.needs_review = True
                account.auto_collect = False

    @staticmethod
    def _collect_daily(
        client: RewardsClient, account: Account, run: FarmRun, journal: OperationJournal
    ) -> None:
        daily = journal.call('daily', client.claim_daily)
        if daily.state == 'submitted_unverified':
            run.state = 'partial'
            try:
                status = journal.call('daily_status', client.daily_status)
            except MarketError:
                raise UncertainClaim('Daily submission could not be reconciled') from None
            if status.available:
                raise UncertainClaim('Daily availability unchanged after submission')
        else:
            run.state = 'unavailable'
            status = DailyStatus(available=False, next_reward_at=daily.next_reward_at)
        schedule_daily(account, status)

    @staticmethod
    def _collect_games(client: RewardsClient, run: FarmRun, journal: OperationJournal) -> None:
        hub = journal.call('games', client.games)
        claimed = False
        for game in hub.games:
            try:
                reward = journal.call(
                    'game:' + game.game_id[:64], partial(client.claim_game, game.game_id)
                )
                claimed = claimed or bool(reward.claimed)
                if reward.unconfirmed:
                    raise UncertainClaim('Unconfirmed game reward')
            except ProtocolError:
                run.state = 'partial'
        if not claimed and run.state == 'success':
            run.state = 'no_rewards'
