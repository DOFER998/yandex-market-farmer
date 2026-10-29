"""Synthetic contracts reconstructed from Go types; these are NOT captured API replies."""

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from src.market.client import MarketClient
from src.market.errors import (
    AccessDenied,
    AuthRequired,
    ClaimsDisabled,
    ProtocolError,
    RateLimited,
    UncertainClaim,
)

HTML = (
    '<script>{"sk":"secret","login":"alice","-version":"v-desktop.1",'
    '"marketFrontGlue":"glue","lang":"ru"}</script>'
)


class Wire:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            code, item = item
        else:
            code = 200
        return SimpleNamespace(
            status_code=code, text=item if isinstance(item, str) else json.dumps(item), headers={}
        )


def client(*responses, claims=False):
    wire = Wire(HTML, *responses)
    return MarketClient(wire, allow_claims=claims, interval=0), wire


def test_balance_and_context_headers():
    c, w = client({'result': {'wheelInfo': {'userInfo': {'coins': 12}}}})
    assert c.balance() == 12
    assert w.calls[1][2]['headers']['sk'] == 'secret'
    assert w.calls[1][2]['json'] == {'path': '/kolesoprizov?track=menu', 'params': {}}
    assert w.calls[1][2]['allow_redirects'] is False


@pytest.mark.parametrize(
    'body',
    [
        {},
        {'result': {'wheelInfo': {'userInfo': {'coins': True}}}},
        {'result': {'wheelInfo': {'userInfo': {'coins': -1}}}},
        {'error': 'secret'},
    ],
)
def test_invalid_balance_not_silently_zero(body):
    c, _ = client(body)
    with pytest.raises(ProtocolError):
        c.balance()


def test_claim_disabled_makes_no_requests():
    c, w = client()
    with pytest.raises(ClaimsDisabled):
        c.claim_daily()
    assert not w.calls


def test_daily_unavailable_never_claims():
    c, w = client({'result': {'dailySignInInfo': {'rewardAvailable': False}}}, claims=True)
    assert c.claim_daily().state == 'unavailable'
    assert len(w.calls) == 2


def test_daily_claim_ack_is_not_proof_of_credit():
    c, w = client(
        {'result': {'dailySignInInfo': {'rewardAvailable': True}}}, {'result': {}}, claims=True
    )
    assert c.claim_daily().state == 'submitted_unverified'
    assert w.calls[-1][1].endswith('resolveFortuneWheelDailySignInReceiveReward')


def test_claim_timeout_is_not_retried():
    c, w = client(
        {'result': {'dailySignInInfo': {'rewardAvailable': True}}},
        TimeoutError('secret'),
        claims=True,
    )
    with pytest.raises(UncertainClaim, match='unknown') as error:
        c.claim_daily()
    assert 'secret' not in str(error.value)
    assert len(w.calls) == 3


@pytest.mark.parametrize(
    ('status', 'error'),
    [(401, AuthRequired), (403, AccessDenied), (429, RateLimited), (302, AuthRequired)],
)
def test_http_failures_stop_without_retry(status, error):
    c, w = client((status, 'secret'))
    with pytest.raises(error):
        c.balance()
    assert len(w.calls) == 2


def test_html_challenge_stops():
    c = MarketClient(Wire('<html><form action="/checkcaptcha"></form></html>'), interval=0)
    with pytest.raises(AccessDenied):
        c.check_auth()


HUB = {
    'result': {
        'gamesSection': {
            'games': [
                {'gameId': 'test', 'gameToken': 'game-secret', 'gameUrl': 'https://example.invalid'}
            ]
        },
        'missionsSection': {
            'missionGroups': [
                {
                    'missions': [
                        {
                            'missionId': 'm1',
                            'status': 'IN_PROGRESS',
                            'rewardAmount': 5,
                            'progress': {'accumulatedValue': 2, 'goalValue': 3},
                        }
                    ]
                }
            ]
        },
    }
}
LEVELS = {
    'results': [
        {
            'data': {
                'gameStatus': {
                    'levels': [
                        {
                            'level': 'RESULT_LEVEL_BRONZE',
                            'requiredScore': 10,
                            'rewardPoints': 5,
                            'achievedToken': 'earned',
                            'isAchieved': True,
                        },
                        {
                            'level': 'RESULT_LEVEL_GOLD',
                            'requiredScore': 100,
                            'rewardPoints': 50,
                            'achievedToken': 'not-earned',
                            'isAchieved': False,
                        },
                    ]
                }
            }
        }
    ]
}


def test_only_server_earned_levels_claimed():
    c, w = client(
        HUB,
        LEVELS,
        {'results': [{'data': {'result': {'earned': 'success', 'not-earned': 'success'}}}]},
        claims=True,
    )
    result = c.claim_game('test')
    assert result.claimed == ['bronze']
    assert result.unconfirmed == []
    payload = w.calls[-1][2]['json']
    assert payload['params'] == [{'gameId': 'test', 'levels': ['bronze']}]
    for _, url, kw in w.calls:
        assert not any(x in url for x in ('Spin', 'ProcessGameResult', 'GameEvent', 'MissionClick'))
        assert 'score' not in str(kw.get('json', {}))
    assert parse_qs(urlsplit(w.calls[-1][1]).query)['r'][0].endswith(
        ':resolveFortuneGameRewardBulk'
    )


def test_hub_reports_progress_without_game_tokens():
    c, _ = client(HUB)
    hub = c.games()
    assert hub.missions[0].accumulated == 2
    assert 'game-secret' not in hub.model_dump_json()
    assert 'game-secret' not in repr(hub)


def test_no_earned_levels_no_bulk_request():
    c, w = client(HUB, {'results': [{'data': {'gameStatus': {'levels': []}}}]}, claims=True)
    assert c.claim_game('test').claimed == []
    assert len(w.calls) == 3


def test_context_refresh_uses_new_token(monkeypatch):
    import src.market.client as module

    now = [10.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    c, w = client(
        {'result': {'wheelInfo': {'userInfo': {'coins': 1}}}},
        HTML.replace('secret', 'rotated'),
        {'result': {'wheelInfo': {'userInfo': {'coins': 2}}}},
    )
    assert c.balance() == 1
    now[0] += 3601
    assert c.balance() == 2
    assert w.calls[-1][2]['headers']['sk'] == 'rotated'


def test_spacing_and_retry_after(monkeypatch):
    import src.market.client as module

    now = [100.0]
    waits = []
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(module.time, 'sleep', sleep)
    wire = Wire(HTML, (429, 'blocked'), {'result': {'wheelInfo': {'userInfo': {'coins': 3}}}})
    original = wire.request

    def request(*args, **kwargs):
        result = original(*args, **kwargs)
        if result.status_code == 429:
            result.headers['Retry-After'] = '120'
        return result

    wire.request = request
    c = MarketClient(wire, interval=2)
    with pytest.raises(RateLimited) as error:
        c.balance()
    assert error.value.retry_after == 120
    with pytest.raises(RateLimited):
        c.balance()
    now[0] += 120
    assert c.balance() == 3
    assert waits == [0, 2, 0]


def test_access_denied_stops_subsequent_requests():
    c, w = client((403, 'no'))
    for _ in range(2):
        with pytest.raises(AccessDenied):
            c.balance()
    assert len(w.calls) == 2


def test_partial_game_ack_is_reported():
    c, _ = client(
        HUB, LEVELS, {'results': [{'data': {'result': {'earned': 'failed'}}}]}, claims=True
    )
    result = c.claim_game('test')
    assert result.claimed == []
    assert result.unconfirmed == ['bronze']


def test_unknown_claim_ack_blocks_client():
    c, w = client(HUB, LEVELS, {}, claims=True)
    with pytest.raises(UncertainClaim):
        c.claim_game('test')
    with pytest.raises(UncertainClaim):
        c.balance()
    assert len(w.calls) == 4


@pytest.mark.parametrize('interval', [float('inf'), float('nan'), -1])
def test_nonfinite_interval_rejected(interval):
    with pytest.raises(ValueError):
        MarketClient(Wire(), interval=interval)


def test_nonfinite_retry_after_uses_safe_default(monkeypatch):
    import src.market.client as module

    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    wire = Wire(HTML, (429, 'blocked'))
    original = wire.request

    def request(*args, **kwargs):
        result = original(*args, **kwargs)
        result.headers['Retry-After'] = 'inf'
        return result

    wire.request = request
    c = MarketClient(wire, interval=0)
    with pytest.raises(RateLimited) as error:
        c.balance()
    assert error.value.retry_after == 60


@pytest.mark.parametrize(
    'extra',
    [
        '<script src="https://smartcaptcha.yandexcloud.net/captcha.js"></script>',
        '<script>const endpoint="/showcaptcha";</script>',
        '<a href="/captcha/help">Help</a>',
    ],
)
def test_captcha_library_reference_is_not_a_challenge(extra):
    c = MarketClient(Wire(HTML + extra), interval=0)
    assert c.check_auth().login == 'alice'


def test_active_captcha_form_blocks_even_with_stale_context():
    c = MarketClient(
        Wire(HTML + '<form action="/checkcaptcha"><input name="rep"></form>'), interval=0
    )
    with pytest.raises(AccessDenied):
        c.check_auth()


def test_redirect_to_challenge_is_not_misreported_as_expired_session():
    wire = Wire((302, ''))
    original = wire.request

    def request(*args, **kwargs):
        result = original(*args, **kwargs)
        result.headers['Location'] = '/showcaptcha?secret=value'
        return result

    wire.request = request
    with pytest.raises(AccessDenied) as error:
        MarketClient(wire, interval=0).check_auth()
    assert 'secret' not in str(error.value)


def test_diagnose_auth_fetches_only_html_and_returns_no_secrets():
    wire = Wire(HTML.replace('v-desktop.1', 'other-module'))
    result = MarketClient(wire, interval=0).diagnose_auth()
    assert result['desktop_version_matches'] == 0
    assert result['front_glue_present'] is True
    assert len(wire.calls) == 1
    assert wire.calls[0][0] == 'GET'
    assert 'secret' not in json.dumps(result)
    assert 'alice' not in json.dumps(result)


def test_single_current_version_reaches_api_header_without_rewriting():
    wire = Wire(
        HTML.replace('v-desktop.1', '2026.10.release-17'),
        {'result': {'wheelInfo': {'userInfo': {'coins': 7}}}},
    )
    assert MarketClient(wire, interval=0).balance() == 7
    assert wire.calls[1][2]['headers']['x-market-app-version'] == '2026.10.release-17'


def test_live_page_is_single_get_with_no_api_mutations():
    wire = Wire('<html><script src="/a.js"></script></html>')
    c = MarketClient(wire, interval=0)
    assert '<html>' in c.fetch_live_page()
    assert len(wire.calls) == 1
    assert wire.calls[0][:2] == ('GET', 'https://market.yandex.ru/live')
    assert wire.calls[0][2]['json'] is None


def test_live_page_stops_on_challenge():
    wire = Wire((403, 'private challenge'))
    c = MarketClient(wire, interval=0)
    with pytest.raises(AccessDenied):
        c.fetch_live_page()


def test_daily_status_reads_captured_next_reward_ts_seconds():
    from datetime import UTC, datetime

    c, _ = client(
        {'result': {'dailySignInInfo': {'rewardAvailable': False, 'nextRewardTs': 1793307600}}}
    )
    status = c.daily_status()
    assert not status.available
    assert status.next_reward_at == datetime.fromtimestamp(1793307600, UTC)


@pytest.mark.parametrize('timestamp', [True, -1, 1.5, '1793307600', 10**30])
def test_daily_status_rejects_invalid_timestamp(timestamp):
    c, _ = client(
        {'result': {'dailySignInInfo': {'rewardAvailable': False, 'nextRewardTs': timestamp}}}
    )
    with pytest.raises(ProtocolError):
        c.daily_status()


def test_completed_mission_without_progress_does_not_break_games():
    # Observed in run-9yr22slc: COMPLETED mission omits the entire progress object.
    from copy import deepcopy

    body = deepcopy(HUB)
    mission = body['result']['missionsSection']['missionGroups'][0]['missions'][0]
    mission['status'] = 'MISSION_STATUS_COMPLETED'
    del mission['progress']
    c, wire = client(body)
    hub = c.games()
    assert len(hub.games) == 1
    assert hub.missions[0].status == 'MISSION_STATUS_COMPLETED'
    assert hub.missions[0].accumulated is None
    assert hub.missions[0].goal is None
    assert len(wire.calls) == 2  # auth page and hub read only


@pytest.mark.parametrize('progress', [None, {}, {'accumulatedValue': '0', 'goalValue': 3}])
def test_malformed_present_mission_progress_still_fails(progress):
    from copy import deepcopy

    body = deepcopy(HUB)
    body['result']['missionsSection']['missionGroups'][0]['missions'][0]['progress'] = progress
    c, _ = client(body)
    with pytest.raises(ProtocolError):
        c.games()


def test_achieved_zero_coin_levels_do_not_submit_claim():
    # Live ballz status on 2026-10-29: achieved bronze/silver, rewardPoints=0.
    from copy import deepcopy

    levels = deepcopy(LEVELS)
    bronze = levels['results'][0]['data']['gameStatus']['levels'][0]
    bronze['rewardPoints'] = 0
    c, wire = client(HUB, levels, claims=True)
    result = c.claim_game('test')
    assert result.claimed == result.unconfirmed == []
    assert len(wire.calls) == 3
    assert not any('resolveFortuneGameRewardBulk' in call[1] for call in wire.calls)


@pytest.mark.parametrize(
    ('response', 'reason', 'status'),
    [
        (TimeoutError('private upstream data'), 'network_error', None),
        ((500, 'private upstream data'), 'http_error', 500),
        ('private non-json data', 'invalid_json', 200),
        ({'errors': ['private upstream data']}, 'api_error', 200),
        ([], 'invalid_object', 200),
    ],
)
def test_uncertain_claim_has_safe_reason_without_retries(response, reason, status):
    c, wire = client(
        {'result': {'dailySignInInfo': {'rewardAvailable': True}}}, response, claims=True
    )
    with pytest.raises(UncertainClaim) as caught:
        c.claim_daily()
    assert caught.value.reason == reason
    assert caught.value.http_status == status
    assert 'private' not in str(caught.value)
    with pytest.raises(UncertainClaim):
        c.claim_daily()
    assert len(wire.calls) == 3
