import pytest

from src.market.errors import ClaimsDisabled, ProtocolError, UncertainClaim
from tests.test_core import client

HELP = 'wheel_open_help_2026_v2'
TG = 'fortune_wheel_market_subscribe_tg_channel_test_october_2026'


def missions(mid=HELP, status='new', **extra):
    return {
        'result': {
            'missionsResponse': {
                'missions': [
                    {'type': 'for_coin', 'id': mid, 'status': status, 'completeCost': 10, **extra}
                ]
            }
        }
    }


def balance(value):
    return {'result': {'wheelInfo': {'userInfo': {'coins': value}}}}


def test_read_keeps_missing_progress_unknown_and_excludes_tokens():
    c, _ = client(missions(status='completed', idempotencyToken='private'))
    m = c.wheel_missions()[0]
    assert m.mission_id == HELP and m.status == 'completed'
    assert m.accumulated is None and m.goal is None
    assert 'private' not in repr(m) + str(m.model_dump())


@pytest.mark.parametrize('mid', [HELP, TG])
def test_verified_click_checks_completion_and_observes_wallet(mid):
    c, w = client(
        missions(mid),
        balance(100),
        {'results': [{'data': None}]},
        missions(mid, 'completed'),
        balance(120),
        claims=True,
    )
    result = c.complete_free_wheel_mission(mid)
    assert result.state == 'completed'
    assert result.balance_before == 100 and result.balance_after == 120
    click = w.calls[3]
    assert 'resolveGamesHubMissionClickV2' in click[1]
    assert click[2]['json']['params'] == [{'missionId': mid, 'surface': 'wheel'}]


@pytest.mark.parametrize(
    'mid',
    [
        'wheel_create_wishlist_2026',
        'wheel_add_to_wishlist_2026',
        'wheel_watch_reviews_everyday_71',
        'wheel_spin_streak_14_days_test',
        'fotrune_wheel_market_order_price_500_10_coins',
        'unknown',
    ],
)
def test_unverified_and_paid_missions_never_make_requests(mid):
    c, w = client(claims=True)
    assert c.complete_free_wheel_mission(mid).state == 'unsupported'
    assert w.calls == []


def test_completed_mission_is_not_clicked_again():
    c, w = client(missions(status='completed'), claims=True)
    assert c.complete_free_wheel_mission(HELP).state == 'already_completed'
    assert len(w.calls) == 2


def test_readonly_client_cannot_click():
    c, w = client()
    with pytest.raises(ClaimsDisabled):
        c.complete_free_wheel_mission(HELP)
    assert not w.calls


def test_click_ack_without_completion_is_uncertain_and_prevents_repeat():
    c, w = client(
        missions(), balance(10), {'results': [{'data': None}]}, missions(), balance(10), claims=True
    )
    result = c.complete_free_wheel_mission(HELP)
    assert result.state == 'unconfirmed'
    count = len(w.calls)
    with pytest.raises(UncertainClaim):
        c.complete_free_wheel_mission(HELP)
    assert len(w.calls) == count


def test_failed_verification_blocks_further_mutation():
    c, _ = client(missions(), balance(10), {'results': [{'data': None}]}, {}, claims=True)
    with pytest.raises(UncertainClaim):
        c.complete_free_wheel_mission(HELP)
    assert c._uncertain


def test_malformed_progress_is_not_replaced_by_zero():
    c, _ = client(missions(progress={'accumulatedValue': True, 'goalValue': 1}))
    with pytest.raises(ProtocolError):
        c.wheel_missions()
