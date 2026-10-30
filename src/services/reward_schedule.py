"""Schedule from the observed Market deadline; jitter is sampled once and persisted."""

from datetime import datetime, timedelta
from random import SystemRandom

from src.common.time import now
from src.database.models import Account
from src.market.models import DailyStatus

_RANDOM = SystemRandom()


def schedule_daily(
    account: Account,
    status: DailyStatus,
    *,
    current: datetime | None = None,
    delay_seconds: int | None = None,
) -> None:
    current = current or now()
    previous_time = account.daily_reward_at
    previous_available = account.daily_available
    account.daily_reward_at = status.next_reward_at
    account.daily_available = status.available
    delay = delay_seconds if delay_seconds is not None else _RANDOM.randint(30, 90)
    if not 30 <= delay <= 90:
        raise ValueError('Reward delay must be 30..90 seconds')
    if status.available:
        if previous_available and current <= account.next_run_at <= current + timedelta(seconds=90):
            return
        account.next_run_at = current + timedelta(seconds=delay)
    elif status.next_reward_at and status.next_reward_at > current:
        if previous_time == status.next_reward_at and status.next_reward_at + timedelta(
            seconds=30
        ) <= account.next_run_at <= status.next_reward_at + timedelta(seconds=90):
            return
        account.next_run_at = status.next_reward_at + timedelta(seconds=delay)
    else:
        # Missing or stale deadline is not permission to guess a daily reset.
        account.next_run_at = current + timedelta(minutes=max(360, account.interval_minutes))


def defer_daily_review(account: Account) -> None:
    """Retry only a status read, with persisted exponential backoff (5 min .. 6 h)."""
    account.daily_needs_review = True
    account.daily_review_attempts += 1
    delay = min(300 * 2 ** min(account.daily_review_attempts - 1, 7), 21600)
    account.next_run_at = now() + timedelta(seconds=delay)


def review_daily(account: Account, status: DailyStatus) -> bool:
    if status.available:
        # Availability alone does not prove a timed-out claim was rejected.
        defer_daily_review(account)
        return False
    account.daily_needs_review = False
    account.daily_review_attempts = 0
    schedule_daily(account, status)
    return True
