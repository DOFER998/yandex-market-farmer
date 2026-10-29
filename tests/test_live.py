"""Contract taken from run-9yr22slc; no private identifiers in fixtures."""

import json

import pytest

from src.market.errors import ProtocolError


def payload(balance=7):
    return {
        'result': {
            'userState': {
                'balanceState': {
                    'balance': balance,
                    'max': 250,
                    'coinSettings': {'amount': 1, 'periodSeconds': 15, 'max': 250, 'enabled': True},
                    'prepaid': 1,
                }
            }
        }
    }


def test_parse_live_state_and_reject_wrong_types():
    from src.market.live import parse_live_state

    state = parse_live_state(payload())
    assert state.balance == 7
    assert state.coinSettings.periodSeconds == 15
    assert state.remaining == 243
    for invalid in ({}, payload(True), payload(-1), payload('7')):
        with pytest.raises(ProtocolError):
            parse_live_state(invalid)


def test_report_orders_samples_and_does_not_conflate_wallet_or_reset(tmp_path):
    from src.market.live import LIVE_UPDATE, summarize_report

    events = []
    for identity, timestamp, balance in [(1, 10, 7), (2, 25, 8), (3, 40, 0)]:
        events.append(
            {
                'kind': 'request',
                'id': identity,
                'elapsed_s': timestamp,
                'endpoint': {'host': 'market.yandex.ru', 'route': LIVE_UPDATE},
            }
        )
        events.append(
            {'kind': 'response', 'id': identity, 'status': 200, 'payload': payload(balance)}
        )
    events.extend(
        [
            {
                'kind': 'request',
                'id': 4,
                'elapsed_s': 50,
                'endpoint': {'host': 'market.yandex.ru', 'route': '/unrelated'},
            },
            {'kind': 'response', 'id': 4, 'status': 200, 'payload': payload(999)},
            {'kind': 'end', 'callback_errors': 0, 'dropped_events': 0},
        ]
    )
    # Response completion order is not request order.
    events[1], events[5] = events[5], events[1]
    path = tmp_path / 'events.jsonl'
    path.write_text('\n'.join(map(json.dumps, events)))
    result = summarize_report(path)
    assert result['samples'] == 3
    assert result['first_balance'] == 7
    assert result['last_balance'] == 0
    assert result['counter_decreased'] is True
    assert result['observed_delta'] is None
    assert result['wallet_balance'] is None


def test_invalid_report_error_does_not_leak_payload(tmp_path):
    from src.market.live import summarize_report

    path = tmp_path / 'events.jsonl'
    path.write_text('secret-invalid-json')
    with pytest.raises(ProtocolError, match='Invalid inspection report') as error:
        summarize_report(path)
    assert 'secret' not in str(error.value)


def test_probe_is_private_and_extracts_scripts_without_executing(tmp_path):
    from src.market.live import save_live_probe

    html = '<html><script src="//yastatic.net/a.js"></script><script>secret</script></html>'
    result = save_live_probe(html, root=tmp_path / 'probe')
    from pathlib import Path

    path = Path(result['report'])
    record = json.loads(path.read_text())
    assert record['html'] == html
    assert record['scripts'] == ['https://yastatic.net/a.js']
    assert result['script_count'] == 1
    assert 'secret' not in json.dumps(result)
    assert path.stat().st_mode & 0o777 == 0o600


def test_cli_live_report_does_not_require_session(tmp_path, capsys):
    from scripts.market import main

    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'kind': 'end', 'dropped_events': 0, 'callback_errors': 0}))
    assert main(['--session', str(tmp_path / 'missing'), 'live-report', str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['samples'] == 0
    assert not (tmp_path / 'missing').exists()


def test_summary_excludes_failed_samples_and_keeps_wallet_separate(tmp_path):
    from src.market.live import LIVE_UPDATE, WALLET, summarize_report

    events = []
    for identity, route, status, body in [
        (1, LIVE_UPDATE, 200, payload(7)),
        (2, LIVE_UPDATE, 500, payload(999)),
        (3, LIVE_UPDATE, 200, payload(9)),
        (4, WALLET, 200, {'result': {'wheelInfo': {'userInfo': {'coins': 34}}}}),
    ]:
        events.extend(
            [
                {
                    'kind': 'request',
                    'id': identity,
                    'elapsed_s': identity * 15,
                    'endpoint': {'host': 'market.yandex.ru', 'route': route},
                },
                {'kind': 'response', 'id': identity, 'status': status, 'payload': body},
            ]
        )
    path = tmp_path / 'events.jsonl'
    path.write_text('\n'.join(map(json.dumps, events)))
    result = summarize_report(path)
    assert result['samples'] == 2
    assert result['observed_delta'] == 2
    assert result['elapsed_seconds'] == 30
    assert result['wallet_balance'] == 34
    assert result['invalid_or_failed_samples'] == 1
    assert result['capture_finished'] is False


@pytest.mark.parametrize('metadata', ['private-secret', {'token': 'private-secret'}, True, -1])
def test_invalid_end_metadata_is_not_printed(tmp_path, metadata):
    from src.market.live import summarize_report

    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'kind': 'end', 'dropped_events': metadata, 'callback_errors': 0}))
    with pytest.raises(ProtocolError):
        summarize_report(path)


def test_huge_timestamp_has_sanitized_error(tmp_path):
    from src.market.live import LIVE_UPDATE, summarize_report

    path = tmp_path / 'events.jsonl'
    events = [
        {
            'kind': 'request',
            'id': 1,
            'elapsed_s': 10**400,
            'endpoint': {'host': 'market.yandex.ru', 'route': LIVE_UPDATE},
        },
        {'kind': 'response', 'id': 1, 'status': 200, 'payload': payload()},
    ]
    path.write_text('\n'.join(map(json.dumps, events)))
    with pytest.raises(ProtocolError):
        summarize_report(path)


def test_probe_recognizes_desktop_placeholder_not_script_text(tmp_path):
    from src.market.live import save_live_probe

    message = 'Смотрите трансляцию в приложении или на мобильной версии сайта.'
    result = save_live_probe(f'<div><span>{message}</span></div>', root=tmp_path / 'one')
    assert result['desktop_placeholder'] is True
    assert result['bootstrap_markers'] == {
        'liveHub': False,
        'balanceState': False,
        'sectionHashes': False,
    }
    script = save_live_probe(f'<script>const s="{message}";</script>', root=tmp_path / 'two')
    assert script['desktop_placeholder'] is False


def bootstrap_html(balance='31', *, collection_id='stream-a'):
    import html

    patches = [
        {
            'collections': {
                'liveStreamInfo': {
                    collection_id: {
                        'userState': {
                            'balanceState': dict(
                                payload()['result']['userState']['balanceState'],
                                balance=balance,
                                prepaid='1',
                            )
                        },
                        'sections': [],
                    }
                }
            }
        },
        {
            'widgets': {
                '@marketfront/LiveStream': {
                    '/content/page/liveStream': {
                        'streamId': 'stream-a',
                        'liveHubTimeMs': 1793275884050,
                    }
                }
            }
        },
    ]
    return ''.join(
        '<noframes data-apiary="patch">' + html.escape(json.dumps(p), quote=False) + '</noframes>'
        for p in patches
    )


def test_parse_mobile_bootstrap_matches_widget_to_collection():
    from src.market.live import parse_live_page

    state = parse_live_page(bootstrap_html())
    assert state.balance == 31
    assert state.remaining == 219
    assert state.prepaid == 1
    assert state.coinSettings.periodSeconds == 15


@pytest.mark.parametrize(
    'html',
    [
        '<html>desktop placeholder</html>',
        bootstrap_html(collection_id='different-stream'),
        bootstrap_html(balance='31.0'),
        bootstrap_html(balance=True),
        bootstrap_html() + bootstrap_html(),
    ],
)
def test_mobile_bootstrap_rejects_missing_ambiguous_or_invalid_contract(html):
    from src.market.live import parse_live_page

    with pytest.raises(ProtocolError):
        parse_live_page(html)
