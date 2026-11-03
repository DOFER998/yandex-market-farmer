from copy import deepcopy

import pytest

from src.market.errors import ClaimsDisabled, ProtocolError
from src.market.live import parse_live_state
from src.market.live_client import LiveClient, LiveCursor
from tests.test_core import HTML, Wire
from tests.test_live import bootstrap_html, payload


def cursor():
    return LiveCursor(
        'stream-a',
        123,
        {
            'streamInfo': {
                'streamInfo': {'settings': {'pollingIntervalSeconds': 15}},
                'sectionHashes': {'sectionId': 'info', 'hash': 'original'},
            },
            'chat': {'chat': {}, 'sectionHashes': {'sectionId': 'chat', 'hash': 'old'}},
        },
        parse_live_state(payload()),
        15,
    )


def test_live_bootstrap_and_bounded_state():
    c = LiveClient(Wire(bootstrap_html()), interval=0)
    value = c.bootstrap_live()
    assert value.stream_id == 'stream-a'
    assert value.interval == 15
    assert value.active


def test_live_poll_keeps_original_start_hashes_and_respects_updated_interval():
    response = payload(8)
    response['result'].update(
        sections=[{'streamInfo': {'settings': {'pollingIntervalSeconds': 30}}}],
        deletedSectionIds=['chat'],
    )
    w = Wire(HTML, response)
    c = LiveClient(w, allow_claims=True, interval=0)
    value = cursor()
    c.poll_live(value)
    sent = w.calls[-1][2]['json']['params']['liveHub']
    assert sent['liveHubTimeMs'] == 123
    assert sent['sectionHashes'][0]['hash'] == 'original'
    assert value.sections['streamInfo']['sectionHashes']['hash'] == 'original'
    assert 'chat' not in value.sections
    assert value.state.balance == 8 and value.interval == 30


@pytest.mark.parametrize('stop', ['max', 'settings_max', 'disabled', 'ended'])
def test_live_never_polls_when_stopped(stop):
    value = cursor()
    if stop == 'max':
        value.state.max = 7
    elif stop == 'settings_max':
        value.state.coinSettings.max = 7
    elif stop == 'disabled':
        value.state.coinSettings.enabled = False
    else:
        value.ended = True
    w = Wire()
    LiveClient(w, allow_claims=True, interval=0).poll_live(value)
    assert not w.calls


def test_live_invalid_update_does_not_overwrite_cursor():
    value = cursor()
    original = deepcopy(value)
    c = LiveClient(Wire(HTML, {}), allow_claims=True, interval=0)
    with pytest.raises(ProtocolError):
        c.poll_live(value)
    assert value == original


def test_live_requires_explicit_claims_permission():
    w = Wire()
    with pytest.raises(ClaimsDisabled):
        LiveClient(w).poll_live(cursor())
    assert not w.calls


def test_end_notification_without_balance_state_preserves_last_counter():
    value = cursor()
    c = LiveClient(Wire(HTML, {'result': {'streamEnded': True}}), allow_claims=True, interval=0)
    state = c.poll_live(value)
    assert state.balance == 7
    assert value.ended and not value.active
