import json

import pytest

from src.market.errors import ProtocolError

BASE = 'https://yastatic.net/s3/market-b2c-static-production/mf-promoloyalty-touch/browser/'


def test_source_urls_only_accept_exact_public_bundle_location():
    from src.market.live_sources import source_urls

    urls = [BASE + 'browser.LiveStreamPageFuture.cbd5ebb.js', BASE + 'browser.runtime.123.js']
    assert source_urls({'kind': 'live_page', 'scripts': urls + urls}) == urls
    for unsafe in [
        'http://127.0.0.1/private',
        BASE.replace('yastatic.net', 'yastatic.net.evil') + 'a.js',
        BASE + '../secret.js',
        BASE + 'a.js?token=secret',
        BASE.replace('yastatic.net', 'user:password@yastatic.net') + 'a.js',
    ]:
        with pytest.raises(ProtocolError):
            source_urls({'kind': 'live_page', 'scripts': [unsafe]})


def test_collect_saves_sources_without_execution_or_account_session(tmp_path, monkeypatch):
    from src.market import live_sources

    probe = tmp_path / 'probe.jsonl'
    probe.write_text(json.dumps({'kind': 'live_page', 'scripts': [BASE + 'browser.main.123.js']}))
    calls = []

    class Response:
        status_code = 200

        def iter_content(self):
            yield b'const code = "not executed";'

        def close(self):
            calls.append('closed')

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    def make(jar):
        assert jar is None
        return Session()

    monkeypatch.setattr(live_sources, 'make_session', make)
    result = live_sources.collect_sources(probe, root=tmp_path / 'sources')
    from pathlib import Path

    events = [json.loads(line) for line in Path(result['report']).read_text().splitlines()]
    assert events[0]['source'] == 'const code = "not executed";'
    assert result['downloaded'] == 1
    assert calls[0][1]['allow_redirects'] is False
    assert calls[1] == 'closed'


@pytest.mark.parametrize(
    'status,oversized', [(403, False), (429, False), (302, False), (200, True)]
)
def test_source_failure_stops_without_retry_and_closes(tmp_path, monkeypatch, status, oversized):
    from src.market import live_sources
    from src.market.errors import MarketError

    path = tmp_path / 'probe.jsonl'
    path.write_text(json.dumps({'kind': 'live_page', 'scripts': [BASE + 'a.js', BASE + 'b.js']}))
    calls = []
    closed = []

    class Response:
        status_code = status

        def iter_content(self):
            yield b'x' * (8_388_609 if oversized else 1)

        def close(self):
            closed.append(True)

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, *args, **kwargs):
            calls.append(args)
            return Response()

    monkeypatch.setattr(live_sources, 'make_session', lambda _: Session())
    with pytest.raises(MarketError):
        live_sources.collect_sources(path, root=tmp_path / 'sources')
    assert len(calls) == 1
    assert closed == [True]
