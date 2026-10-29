from datetime import UTC, datetime, timedelta

from src.database.models import Account
from src.market.models import DailyStatus
from src.services.reward_schedule import schedule_daily

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def test_server_deadline_with_persisted_jitter():
    account = Account(id='main', next_run_at=NOW)
    deadline = NOW + timedelta(hours=10)
    status = DailyStatus(available=False, next_reward_at=deadline)
    schedule_daily(account, status, current=NOW, delay_seconds=45)
    assert account.next_run_at == deadline + timedelta(seconds=45)
    assert account.daily_reward_at == deadline
    schedule_daily(account, status, current=NOW + timedelta(minutes=1), delay_seconds=80)
    assert account.next_run_at == deadline + timedelta(seconds=45)


def test_available_reward_gets_short_delay_and_missing_or_past_timer_gets_slow_recheck():
    account = Account(id='main', next_run_at=NOW, interval_minutes=15)
    schedule_daily(account, DailyStatus(available=True), current=NOW, delay_seconds=30)
    assert account.next_run_at == NOW + timedelta(seconds=30)
    schedule_daily(account, DailyStatus(available=False), current=NOW, delay_seconds=45)
    assert account.next_run_at == NOW + timedelta(hours=6)
    schedule_daily(
        account,
        DailyStatus(available=False, next_reward_at=NOW - timedelta(seconds=1)),
        current=NOW,
        delay_seconds=60,
    )
    assert account.next_run_at == NOW + timedelta(hours=6)


def test_site_countdown_is_used_instead_of_six_hour_fallback():
    deadline = NOW + timedelta(hours=1, minutes=55, seconds=10)
    account = Account(id='main', next_run_at=NOW + timedelta(hours=6))
    schedule_daily(
        account,
        DailyStatus(available=False, next_reward_at=deadline),
        current=NOW,
        delay_seconds=45,
    )
    assert account.next_run_at == deadline + timedelta(seconds=45)
    assert account.next_run_at - NOW == timedelta(hours=1, minutes=55, seconds=55)


def test_unknown_timer_is_displayed_as_a_recheck_not_a_reward_collection():
    from src.views.accounts import reward_timing

    account = Account(
        id='main', auto_collect=True, daily_available=False, next_run_at=NOW + timedelta(hours=6)
    )
    text = reward_timing(account)
    assert 'Повторная проверка' in text
    assert 'Запланированный сбор' not in text


def test_known_timer_with_error_backoff_is_displayed_as_recheck():
    from src.views.accounts import reward_timing

    account = Account(
        id='main',
        auto_collect=True,
        daily_available=False,
        daily_reward_at=NOW + timedelta(hours=2),
        next_run_at=NOW + timedelta(hours=6),
    )
    assert 'Повторная проверка' in reward_timing(account)


def test_server_timer_plus_jitter_is_displayed_as_collection():
    from src.views.accounts import reward_timing

    deadline = NOW + timedelta(hours=2)
    account = Account(
        id='main',
        auto_collect=True,
        daily_available=False,
        daily_reward_at=deadline,
        next_run_at=deadline + timedelta(seconds=45),
    )
    text = reward_timing(account)
    assert 'Запланированный сбор' in text
    assert 'Повторная проверка' not in text
