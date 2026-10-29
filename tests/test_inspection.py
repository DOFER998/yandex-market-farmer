import json
import os
from types import SimpleNamespace

import pytest

from src.market.inspection import Report, describe_endpoint


def test_endpoint_preserves_path_and_query_values():
    endpoint = describe_endpoint(
        'https://market.yandex.ru/api/new-method/123?streamId=abc&seconds=15'
    )
    assert endpoint['route'] == '/api/new-method/123'
    assert endpoint['query'] == 'streamId=abc&seconds=15'


@pytest.mark.parametrize(
    'url',
    [
        'https://passport.yandex.ru/auth?password=secret',
        'https://market.yandex.ru.evil.invalid/api/resolve/?r=secret',
        'http://market.yandex.ru/api/resolve/',
        'https://market.yandex.ru/live/private-id',
    ],
)
def test_ignore_non_market_api_and_login_traffic(url):
    assert describe_endpoint(url) is None


def test_private_report_writes_incrementally_and_bounds_records(tmp_path):
    with Report(tmp_path / 'reports', max_events=2) as report:
        report.write({'kind': 'first'})
        assert json.loads(report.path.read_text().splitlines()[0])['kind'] == 'first'
        assert os.stat(report.path).st_mode & 0o777 == 0o600
        assert os.stat(report.path.parent).st_mode & 0o777 == 0o700
        report.write({'kind': 'second'})
        report.write({'kind': 'third'})
    assert len(report.path.read_text().splitlines()) == 2
    assert report.dropped == 1


def test_report_refuses_symlink_directory(tmp_path):
    from src.market.errors import SessionError

    (tmp_path / 'real').mkdir()
    (tmp_path / 'linked').symlink_to(tmp_path / 'real', target_is_directory=True)
    with pytest.raises(SessionError):
        with Report(tmp_path / 'linked'):
            pass


def test_recorder_captures_request_and_response_without_headers(tmp_path):
    import asyncio

    from src.market.inspection import Recorder

    request = SimpleNamespace(
        url='https://market.yandex.ru/api/resolve/?r=src/resolvers/live/resolveStatus:resolveStatus',
        method='POST',
        resource_type='fetch',
        post_data_buffer='{"params":[{"sk":"secret","count":2}]}',
    )

    class Response:
        status = 200
        headers = {'content-type': 'application/json'}

        async def body(self):
            return b'{"result":{"coins":3,"token":"secret"}}'

    async def response():
        return Response()

    request.response = response
    with Report(tmp_path / 'reports') as report:
        recorder = Recorder(report)
        recorder.request(request)
        asyncio.run(recorder.finished(request))
    events = [json.loads(x) for x in report.path.read_text().splitlines()]
    assert [x['kind'] for x in events] == ['request', 'response']
    assert events[1]['payload']['result']['coins'] == 3
    assert events[0]['id'] == events[1]['id']
    assert events[0]['payload'] == {'params': [{'sk': 'secret', 'count': 2}]}
    assert events[1]['payload'] == {'result': {'coins': 3, 'token': 'secret'}}
    assert all('headers' not in event for event in events)


def test_unreadable_request_body_does_not_break_recording(tmp_path):
    from src.market.inspection import Recorder

    class Request:
        url = 'https://market.yandex.ru/api/resolve/'
        resource_type = 'fetch'
        method = 'POST'

        @property
        def post_data_buffer(self):
            raise RuntimeError('private-secret')

    with Report(tmp_path / 'reports') as report:
        recorder = Recorder(report)
        recorder.request(Request())
    assert 'private-secret' not in report.path.read_text()
    assert recorder.callback_errors == 1


def test_browser_closing_preserves_report_and_never_requests_claim(monkeypatch, tmp_path):
    from src.market.inspection import inspect_live

    operations = []

    class Page:
        async def goto(self, url, **kwargs):
            operations.append(('goto', url))

    class Context:
        pages = []  # Simulate user closing the window immediately after navigation.

        def on(self, *args):
            pass

        async def new_page(self):
            return Page()

    class Browser:
        async def new_context(self, **kwargs):
            return Context()

        def is_connected(self):
            return True

        async def close(self):
            operations.append(('close',))

    class Chromium:
        async def launch(self, **kwargs):
            assert kwargs['headless'] is False
            assert kwargs['chromium_sandbox'] is True
            return Browser()

    class Manager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr('playwright.async_api.async_playwright', Manager)
    result = inspect_live(browser='chromium', report_root=tmp_path / 'reports', minutes=1)
    from pathlib import Path

    events = [json.loads(x) for x in Path(result['report']).read_text().splitlines()]
    assert events[0]['kind'] == 'start'
    assert events[-1]['kind'] == 'end'
    assert operations == [('goto', 'https://market.yandex.ru/live'), ('close',)]


def test_inspect_live_cli_works_without_cookie_file(monkeypatch, tmp_path, capsys):
    from scripts.market import main
    from src.market import inspection

    seen = []

    def inspect(**kwargs):
        seen.append(kwargs)
        return {'report': 'report.jsonl', 'events': 2, 'dropped_events': 0}

    monkeypatch.setattr(inspection, 'inspect_live', inspect)
    assert (
        main(
            [
                '--session',
                str(tmp_path / 'private' / 'cookies.txt'),
                'inspect-live',
                '--fresh-login',
            ]
        )
        == 0
    )
    assert seen[0]['jar'] is None
    assert json.loads(capsys.readouterr().out)['events'] == 2


def test_binary_post_is_skipped_without_interrupting_capture(tmp_path):
    from src.market.inspection import Recorder

    request = SimpleNamespace(
        url='https://market.yandex.ru/api/resolve/',
        method='POST',
        resource_type='fetch',
        post_data_buffer=b'\xff\x00',
    )
    with Report(tmp_path / 'reports') as report:
        recorder = Recorder(report)
        recorder.request(request)
    event = json.loads(report.path.read_text())
    assert event['kind'] == 'request'
    assert event['payload'] == '<not-json>'
    assert recorder.callback_errors == 0


def test_write_failure_is_signaled_without_callback_traceback(tmp_path, monkeypatch):
    from src.market.inspection import Recorder

    request = SimpleNamespace(
        url='https://market.yandex.ru/api/resolve/',
        method='GET',
        resource_type='fetch',
        post_data_buffer=None,
    )
    with Report(tmp_path / 'reports') as report:

        def fail(_):
            raise OSError('private filesystem detail')

        monkeypatch.setattr(report, 'write', fail)
        recorder = Recorder(report)
        recorder.request(request)
        assert recorder.recording_failed is True


def test_cancelled_browser_capture_still_closes_browser(monkeypatch, tmp_path):
    import asyncio

    from src.market.inspection import Report, _inspect

    closed = []

    class Page:
        async def goto(self, *args, **kwargs):
            raise asyncio.CancelledError

    class Context:
        def on(self, *args):
            pass

        async def new_page(self):
            return Page()

    class Browser:
        async def new_context(self, **kwargs):
            return Context()

        async def close(self):
            closed.append(True)

    class Chromium:
        async def launch(self, **kwargs):
            return Browser()

    class Manager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr('playwright.async_api.async_playwright', Manager)
    with Report(tmp_path / 'reports') as report:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_inspect(report, jar=None, browser='chromium', minutes=1))
    assert closed == [True]
    assert json.loads(report.path.read_text().splitlines()[-1])['kind'] == 'end'


def test_capture_preserves_unknown_fields_deep_arrays_and_numbers(tmp_path):
    from src.market.inspection import Recorder

    payload = {
        'viewingSession': {
            'events': list(range(30)),
            'seconds': '15',
            'timestamp': 1700000000000,
            'fraction': 0.25,
            'streamId': 'real-stream',
            'token': 'body-token',
        }
    }
    nested = payload
    for _ in range(15):
        nested['child'] = {}
        nested = nested['child']
    nested['text'] = 'Глубокое поле'
    request = SimpleNamespace(
        url='https://market.yandex.ru/api/resolve/',
        method='POST',
        resource_type='fetch',
        post_data_buffer=json.dumps(payload).encode(),
    )
    with Report(tmp_path / 'reports') as report:
        Recorder(report).request(request)
    event = json.loads(report.path.read_text())
    assert event['payload'] == payload
