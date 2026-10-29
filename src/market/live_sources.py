"""Fetch public bundles referenced by a saved probe; never execute their code."""

import json
import re
from pathlib import Path

from src.market.errors import ProtocolError, TransportError
from src.market.inspection import Report
from src.market.transport import make_session

BUNDLE = re.compile(
    r'https://yastatic\.net/s3/market-b2c-static-production/'
    r'mf-promoloyalty-(?:touch|desktop)/browser/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.js'
)


def source_urls(probe: dict) -> list[str]:
    try:
        if probe['kind'] != 'live_page' or not isinstance(probe['scripts'], list):
            raise ValueError
        urls = []
        for url in probe['scripts']:
            if isinstance(url, str) and BUNDLE.fullmatch(url) and url not in urls:
                urls.append(url)
        if not 1 <= len(urls) <= 16:
            raise ValueError
        return urls
    except (KeyError, TypeError, ValueError):
        raise ProtocolError('Probe has no supported public Market bundles') from None


def collect_sources(probe: Path, *, root=Path('.secrets/live-sources')) -> dict:
    try:
        if probe.stat().st_size > 32_000_000:
            raise ValueError
        urls = source_urls(json.loads(probe.read_text()))
    except (ValueError, RecursionError):
        raise ProtocolError('Invalid live probe file') from None
    downloaded = 0
    total = 0
    with Report(root) as report:
        for url in urls:
            # A fresh public session per asset; no account cookies or cross-request state.
            with make_session(None) as session:
                response = None
                try:
                    response = session.get(url, stream=True, allow_redirects=False, timeout=30)
                    if response.status_code != 200:
                        raise TransportError(f'Public bundle HTTP {response.status_code}; stopped')
                    body = bytearray()
                    for chunk in response.iter_content():
                        body.extend(chunk)
                        if len(body) > 8_388_608 or total + len(body) > 33_554_432:
                            raise ProtocolError('Public bundle size limit exceeded')
                    source = body.decode('utf-8')
                except (TransportError, ProtocolError):
                    raise
                except Exception:
                    raise TransportError(
                        'Public bundle download failed; no retry attempted'
                    ) from None
                finally:
                    if response is not None:
                        response.close()
            total += len(body)
            report.write({'kind': 'public_script', 'url': url, 'source': source})
            if report.dropped:
                raise ProtocolError('Public bundle report size limit exceeded')
            downloaded += 1
        return {'report': str(report.path), 'downloaded': downloaded, 'bytes': total}
