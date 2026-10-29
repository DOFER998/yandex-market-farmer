"""Observed live contracts and offline analysis; never submits viewing events."""

import json
import math
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from pydantic import Field, ValidationError

from src.market.errors import ProtocolError
from src.market.models import Model

LIVE_UPDATE = '/api/web/market.front.mfPromoLoyalty.MfPromoLoyalty/resolveLiveStreamUpdateContent'
WALLET = '/api/web/market.front.mfPromoLoyalty.MfPromoLoyalty/resolveFortuneWheelMainScreenData'


class CoinSettings(Model):
    amount: int = Field(ge=0)
    periodSeconds: int = Field(gt=0)
    max: int = Field(ge=0)
    enabled: bool


class LiveState(Model):
    balance: int = Field(ge=0)
    max: int = Field(ge=0)
    coinSettings: CoinSettings
    prepaid: int = Field(ge=0)

    @property
    def remaining(self) -> int:
        return max(0, self.max - self.balance)


def parse_live_state(payload) -> LiveState:
    try:
        if payload.get('error') or payload.get('errors'):
            raise ValueError
        return LiveState.model_validate(payload['result']['userState']['balanceState'])
    except (AttributeError, KeyError, TypeError, ValueError, ValidationError):
        raise ProtocolError('Live state missing or invalid; schema may have changed') from None


def summarize_report(path: Path) -> dict:
    """Report observed counters, not inferred wallet income. Never prints body strings."""
    requests, responses = {}, []
    end = None
    try:
        if path.stat().st_size > 67_108_864:
            raise ValueError
        with path.open() as source:
            for line in source:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError
                kind = event.get('kind')
                if kind == 'request':
                    identity = event['id']
                    if type(identity) is not int or identity in requests:
                        raise ValueError
                    requests[identity] = event
                elif kind == 'response':
                    responses.append(event)
                elif kind == 'end':
                    for key in ('dropped_events', 'callback_errors'):
                        if type(event.get(key)) is not int or event[key] < 0:
                            raise ValueError
                    end = event
        samples, wallets = [], []
        invalid = 0
        seen = set()
        for response in responses:
            identity = response['id']
            if type(identity) is not int or identity in seen:
                raise ValueError
            seen.add(identity)
            request = requests.get(identity)
            if request is None:
                continue
            endpoint = request['endpoint']
            if endpoint.get('host') != 'market.yandex.ru':
                continue
            route = endpoint.get('route')
            if route not in (LIVE_UPDATE, WALLET):
                continue
            timestamp = request['elapsed_s']
            if (
                type(timestamp) not in (int, float)
                or not 0 <= timestamp <= 86_400
                or not math.isfinite(timestamp)
            ):
                raise ValueError
            if response.get('status') != 200:
                invalid += 1
                continue
            try:
                if route == LIVE_UPDATE:
                    samples.append((timestamp, parse_live_state(response['payload'])))
                else:
                    coins = response['payload']['result']['wheelInfo']['userInfo']['coins']
                    if type(coins) is not int or coins < 0:
                        raise ValueError
                    wallets.append((timestamp, coins))
            except (KeyError, TypeError, ValueError, ProtocolError):
                invalid += 1
        samples.sort(key=lambda sample: sample[0])
        wallets.sort()
        decreased = any(
            b[1].balance < a[1].balance for a, b in zip(samples, samples[1:], strict=False)
        )
        first, last = (samples[0][1], samples[-1][1]) if samples else (None, None)
        return {
            'samples': len(samples),
            'first_balance': first.balance if first else None,
            'last_balance': last.balance if last else None,
            'observed_delta': last.balance - first.balance if samples and not decreased else None,
            'counter_decreased': decreased,
            'elapsed_seconds': round(samples[-1][0] - samples[0][0], 3) if samples else None,
            'latest_state': last.model_dump() if last else None,
            'remaining_to_reported_max': last.remaining if last else None,
            'wallet_balance': wallets[-1][1] if wallets else None,
            'invalid_or_failed_samples': invalid,
            'capture_finished': end is not None,
            'dropped_events': end.get('dropped_events') if end else None,
            'callback_errors': end.get('callback_errors') if end else None,
        }
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ProtocolError('Invalid inspection report; no raw data printed') from None


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.text = []
        self.skip_text = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip_text = True
        if tag == 'script':
            source = dict(attrs).get('src')
            if source:
                self.urls.append(urljoin('https://market.yandex.ru/live', source))

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip_text = False

    def handle_data(self, data):
        if not self.skip_text:
            self.text.append(data)


def save_live_probe(
    html: str, *, root=Path('.secrets/live-probe'), client_profile='chrome146'
) -> dict:
    # Reuse private atomic directory creation; no browser dependency is invoked.
    from src.market.inspection import Report

    if len(html.encode()) > 16_777_216:
        raise ProtocolError('Live page too large for probe')
    scripts = _Scripts()
    scripts.feed(html)
    with Report(root) as report:
        report.write(
            {
                'kind': 'live_page',
                'client_profile': client_profile,
                'url': 'https://market.yandex.ru/live',
                'html': html,
                'scripts': scripts.urls,
            }
        )
        if report.dropped:
            raise ProtocolError('Live page could not fit into probe report')
        return {
            'report': str(report.path),
            'script_count': len(scripts.urls),
            'html_bytes': len(html.encode()),
            'api_verified': False,
            'desktop_placeholder': (
                'Смотрите трансляцию в приложении или на мобильной версии сайта.'
                in ' '.join(' '.join(scripts.text).split())
            ),
            'bootstrap_markers': {
                name: name in html for name in ('liveHub', 'balanceState', 'sectionHashes')
            },
        }


class _ApiaryPatches(HTMLParser):
    """Read JSON only; never evaluate the site's JavaScript."""

    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.patches = []

    def handle_starttag(self, tag, attrs):
        if tag == 'noframes' and dict(attrs).get('data-apiary') == 'patch':
            self.active = True
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'noframes' and self.active:
            self.active = False
            patch = json.loads(''.join(self.parts))
            if not isinstance(patch, dict):
                raise ValueError
            self.patches.append(patch)


def parse_live_page(html: str) -> LiveState:
    """Parse the observed mobile SSR contract, independently of API JSON parsing."""
    try:
        if len(html.encode()) > 16_777_216:
            raise ValueError
        parser = _ApiaryPatches()
        parser.feed(html)
        parser.close()
        if parser.active:
            raise ValueError
        widgets, infos = [], []
        for patch in parser.patches:
            widget = (
                patch.get('widgets', {})
                .get('@marketfront/LiveStream', {})
                .get('/content/page/liveStream')
            )
            if widget is not None:
                widgets.append(widget)
            info = patch.get('collections', {}).get('liveStreamInfo')
            if info is not None:
                infos.append(info)
        if len(widgets) != 1:
            raise ValueError
        stream_id = widgets[0]['streamId']
        if not isinstance(stream_id, str) or not stream_id:
            raise ValueError
        matches = [info[stream_id] for info in infos if stream_id in info]
        if len(matches) != 1:
            raise ValueError
        state = dict(matches[0]['userState']['balanceState'])
        # SSR encodes these two protobuf integers as decimal strings;
        # observed POST responses encode them as JSON numbers.
        for key in ('balance', 'prepaid'):
            value = state[key]
            if isinstance(value, str):
                if not value or len(value) > 20 or not value.isascii() or not value.isdecimal():
                    raise ValueError
                state[key] = int(value)
        return LiveState.model_validate(state)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ProtocolError(
            'Mobile live bootstrap missing or invalid; schema may have changed'
        ) from None
