"""Live polling contract observed in the current mobile frontend, without a browser."""

import json
from dataclasses import dataclass

from src.market.client import API, ORIGIN, MarketClient
from src.market.errors import ProtocolError
from src.market.live import LiveState, _ApiaryPatches, parse_live_state


@dataclass
class LiveCursor:
    stream_id: str
    started_ms: int
    sections: dict[str, dict]
    state: LiveState
    interval: int
    ended: bool = False

    @property
    def active(self):
        return (
            not self.ended
            and self.state.coinSettings.enabled
            and self.state.coinSettings.amount > 0
            and self.state.balance < min(self.state.max, self.state.coinSettings.max)
        )


def section_key(section):
    return section.get('type') or next(
        (key for key in section if key not in ('sectionHashes', 'pages')), None
    )


class LiveClient(MarketClient):
    """Must be constructed with the mobile transport profile."""

    def bootstrap_live(self) -> LiveCursor:
        parser = _ApiaryPatches()
        parser.feed(self.fetch_live_page())
        widgets, infos = [], {}
        try:
            for patch in parser.patches:
                widget = (
                    patch.get('widgets', {})
                    .get('@marketfront/LiveStream', {})
                    .get('/content/page/liveStream')
                )
                if widget is not None:
                    widgets.append(widget)
                infos.update(patch.get('collections', {}).get('liveStreamInfo', {}))
            if len(widgets) != 1:
                raise ValueError
            widget = widgets[0]
            stream_id, started = widget['streamId'], widget['liveHubTimeMs']
            if not isinstance(stream_id, str) or not stream_id or type(started) is not int:
                raise ValueError
            info = infos[stream_id]
            state = dict(info['userState']['balanceState'])
            for key in ('balance', 'prepaid'):
                if isinstance(state[key], str) and state[key].isascii() and state[key].isdecimal():
                    state[key] = int(state[key])
            state = LiveState.model_validate(state)
            sections = {section_key(s): s for s in info['sections']}
            if None in sections:
                raise ValueError
            interval = (
                sections.get('streamInfo', {})
                .get('streamInfo', {})
                .get('settings', {})
                .get('pollingIntervalSeconds')
                or 10
            )
            if type(interval) is not int or not 1 <= interval <= 86400:
                raise ValueError
            return LiveCursor(
                stream_id, started, sections, state, max(interval, state.coinSettings.periodSeconds)
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProtocolError('Live bootstrap contract changed') from None

    def poll_live(self, cursor: LiveCursor) -> LiveState:
        with self._lock:
            self._require_claims()
            if not cursor.active:
                return cursor.state
            context = self.check_auth(force=False)
            params = {
                'liveHub': {
                    'streamId': cursor.stream_id,
                    'sectionHashes': [
                        s['sectionHashes']
                        for s in cursor.sections.values()
                        if s.get('sectionHashes')
                    ],
                    'liveHubTimeMs': cursor.started_ms,
                    'adsSourceScreen': 'stream',
                    'adsPage': 'stream',
                    'isPipMode': False,
                }
            }
            text = self._send(
                'POST',
                API + 'resolveLiveStreamUpdateContent',
                headers={
                    'content-type': 'application/json',
                    'origin': ORIGIN,
                    'referer': ORIGIN + '/live',
                    'sk': context.sk.get_secret_value(),
                    'x-market-app-version': context.version,
                    'x-market-front-glue': context.glue,
                    'x-market-apphost-target': 'PROMOLOYALTY',
                    'x-requested-with': 'XMLHttpRequest',
                },
                payload={'path': '/live', 'params': params},
            )
            try:
                data = json.loads(text)
                result = data['result']
                state = (
                    cursor.state
                    if result.get('streamEnded') is True and 'userState' not in result
                    else parse_live_state(data)
                )
                sections = dict(cursor.sections)
                for section in result.get('sections', []):
                    key = section_key(section)
                    if key is None:
                        raise ValueError
                    sections[key] = {**sections.get(key, {}), **section}
                deleted = set(result.get('deletedSectionIds', []))
                sections = {
                    key: value
                    for key, value in sections.items()
                    if value.get('sectionHashes', {}).get('sectionId') not in deleted
                }
                ended = result.get('streamEnded', False)
                if type(ended) is not bool:
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ProtocolError('Live update contract changed') from None
            cursor.sections, cursor.state, cursor.ended = sections, state, ended
            interval = (
                sections.get('streamInfo', {})
                .get('streamInfo', {})
                .get('settings', {})
                .get('pollingIntervalSeconds')
            )
            if type(interval) is int and 1 <= interval <= 86400:
                cursor.interval = max(interval, state.coinSettings.periodSeconds)
            else:
                cursor.interval = max(cursor.interval, state.coinSettings.periodSeconds)
            return state
