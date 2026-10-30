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
from src.services.reward_schedule import defer_daily_review, review_daily, schedule_daily


class FarmingWorker:
    def __init__(self, factory: ClientFactory, session_dir: Path) -> None:
        self._factory = factory
        self._session_dir = session_dir

    def execute(
        self, account: Account, run: FarmRun, journal: OperationJournal, read_only: bool
    ) -> None:
        mutating = False
        try:
            with self._factory(self._session_dir / f'{account.id}.txt') as client:
                journal.call('auth', client.check_auth)
                account.auth_state = 'ready'
                run.balance_before = journal.call('balance_before', client.balance)
                journal.snapshot(run.balance_before)
                run.state = 'success'
                if not read_only:
                    mutating = True
                    if run.trigger == 'games':
                        self._collect_games(client, account, run, journal)
                    elif account.daily_needs_review:
                        self._review_daily(client, account, run, journal)
                    else:
                        self._collect_daily(client, account, run, journal)
                if run.state in (
                    'unavailable',
                    'no_rewards',
                    'reconciled',
                    'blocked',
                    'review_pending',
                ):
                    run.balance_after = run.balance_before
                elif read_only:
                    run.balance_after = run.balance_before
                    if account.daily_needs_review:
                        self._review_daily(client, account, run, journal)
                    else:
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
            elif isinstance(exc, UncertainClaim) or (mutating and not isinstance(exc, MarketError)):
                run.state = 'uncertain'
                if run.trigger == 'games':
                    account.games_needs_review = True
                else:
                    defer_daily_review(account)
            elif account.daily_needs_review and run.trigger != 'games':
                defer_daily_review(account)

    @staticmethod
    def _collect_daily(
        client: RewardsClient, account: Account, run: FarmRun, journal: OperationJournal
    ) -> None:
        daily = journal.call('daily', client.claim_daily)
        if daily.state == 'claimed':
            run.state = 'success'
            status = DailyStatus(available=False, next_reward_at=daily.next_reward_at)
        elif daily.state == 'submitted_unverified':
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
    def _review_daily(
        client: RewardsClient, account: Account, run: FarmRun, journal: OperationJournal
    ) -> None:
        status = journal.call('daily_reconcile', client.daily_status)
        if review_daily(account, status):
            run.state = 'reconciled'
        else:
            run.state = 'review_pending'
            run.error_code = 'DailyReviewPending'

    @staticmethod
    def _collect_games(
        client: RewardsClient, account: Account, run: FarmRun, journal: OperationJournal
    ) -> None:
        hub = journal.call('games', client.games)
        if account.games_needs_review:
            # Read-only reconciliation. Never repeat unknown claims based on balance alone.
            if not account.games_review_game:
                run.state = 'blocked'
                run.error_code = 'GameReviewRequired'
                return
            levels = journal.call(
                'game_review:' + account.games_review_game[:64],
                partial(client.game_levels, account.games_review_game),
            )
            if levels and not any(level.claim_name for level in levels):
                account.games_needs_review = False
                account.games_review_game = None
                run.state = 'reconciled'
            else:
                run.state = 'blocked'
                run.error_code = 'GameReviewRequired'
            return
        claimed = False
        for game in hub.games:
            try:
                account.games_review_game = game.game_id
                reward = journal.call(
                    'game:' + game.game_id[:64], partial(client.claim_game, game.game_id)
                )
                claimed = claimed or bool(reward.claimed)
                if reward.unconfirmed:
                    raise UncertainClaim('Unconfirmed game reward')
                account.games_review_game = None
            except ProtocolError:
                account.games_review_game = None
                run.state = 'partial'
        if not claimed and run.state == 'success':
            run.state = 'no_rewards'
