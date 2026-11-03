"""Explicit Market endpoints; optional result submission follows the upstream contract."""

import json
import math
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urljoin, urlsplit

from pydantic import ValidationError

from src.market.errors import (
    AccessDenied,
    AuthRequired,
    ClaimFailureReason,
    ClaimsDisabled,
    MarketError,
    ProtocolError,
    RateLimited,
    TransportError,
    UncertainClaim,
)
from src.market.models import (
    AuthContext,
    DailyClaim,
    DailyStatus,
    Game,
    GameClaim,
    Hub,
    Level,
    Mission,
)
from src.market.page import challenge_page, challenge_url, context_diagnostics, page_context
from src.market.wheel_missions import FREE_CLICK_MISSIONS, WheelMission, WheelMissionAttempt

ORIGIN = 'https://market.yandex.ru'
PATH = '/kolesoprizov?track=menu'
PAGE = ORIGIN + PATH
API = ORIGIN + '/api/web/market.front.mfPromoLoyalty.MfPromoLoyalty/'
ENDPOINTS = {
    'balance': 'resolveFortuneWheelMainScreenData',
    'wheel_missions': 'resolveFortuneWheelMissionsScreenData',
    'daily': 'resolveFortuneWheelDailyRewardsScreenData',
    'claim_daily': 'resolveFortuneWheelDailySignInReceiveReward',
    'hub': 'resolveGamesHubMiniMainScreenDataV2',
    'status': 'src/resolvers/gamesHub/resolveGamesHubGameStatusV2:resolveGamesHubGameStatusV2',
    'submit_game_event': 'resolveGamesHubGameEvent',
    'open_mission': (
        'src/resolvers/gamesHub/resolveGamesHubMissionClickV2:resolveGamesHubMissionClickV2'
    ),
    'submit_game_result': (
        'src/resolvers/gamesHub/resolveGamesHubProcessGameResultV2:'
        'resolveGamesHubProcessGameResultV2'
    ),
}


def _dict(value):
    if not isinstance(value, dict):
        raise ProtocolError('Expected API object; schema may have changed')
    if value.get('error') or value.get('errors'):
        raise ProtocolError('API reported an error')
    return value


def _at(value, *keys):
    try:
        for key in keys:
            value = _dict(value)[key]
        return value
    except KeyError:
        raise ProtocolError('Required API field missing; schema may have changed') from None


def _list(value):
    if not isinstance(value, list):
        raise ProtocolError('Expected API list')
    return value


def _validate(cls, value):
    try:
        return cls.model_validate(value)
    except ValidationError:
        raise ProtocolError('Invalid API field types; schema may have changed') from None


class MarketClient:
    """One account per instance; synchronous operations serialized by an RLock.

    session: curl_cffi Session-compatible object. Caller owns close/persistence.
    No mutation is automatically retried, including on stale auth or 5xx.
    """

    def __init__(self, session, *, allow_claims=False, submit_game_results=False, interval=2.0):
        if not math.isfinite(interval) or not 0 <= interval <= 3600:
            raise ValueError('interval must be nonnegative')
        self._session = session
        self._allow_claims = allow_claims
        self._submit_game_results = submit_game_results
        self._interval = interval
        self._next = 0.0
        self._context = None
        self._auth_at = 0.0
        self._lock = threading.RLock()
        self._blocked = False
        self._uncertain = False
        self.session_refreshed = False

    def _send(self, method, url, *, headers, payload=None, mutation=False, auth_hops=0):
        if self._blocked:
            raise AccessDenied('Access denied earlier; stop and resolve manually')
        if self._uncertain:
            raise UncertainClaim('Previous claim outcome unknown; reconcile before restarting')
        remaining = max(0, self._next - time.monotonic())
        if remaining > 60:
            raise RateLimited(remaining)
        time.sleep(remaining)
        try:
            response = self._session.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=30,
                allow_redirects=False,
            )
        except Exception:
            if mutation:
                self._uncertain = True
                raise UncertainClaim(
                    'Claim outcome unknown; do not blindly retry', reason=ClaimFailureReason.NETWORK
                ) from None
            raise TransportError('Network request failed') from None
        finally:
            self._next = time.monotonic() + self._interval
        status = response.status_code
        if status == 403:
            self._blocked = True
            raise AccessDenied('HTTP 403: access denied; no bypass attempted')
        if 300 <= status < 400 and challenge_url(response.headers.get('Location', '')):
            self._blocked = True
            raise AccessDenied('Redirected to challenge; resolve manually in browser')
        if status in (301, 302, 303, 307, 308) and auth_hops and method == 'GET' and not mutation:
            target = urljoin(url, response.headers.get('Location', ''))
            try:
                parsed = urlsplit(target)
                allowed = (
                    parsed.scheme == 'https'
                    and not parsed.username
                    and not parsed.password
                    and parsed.port in (None, 443)
                    and (parsed.hostname, parsed.path)
                    in {
                        ('passport.yandex.ru', '/auth/update'),
                        ('market.yandex.ru', '/kolesoprizov'),
                    }
                )
            except ValueError:
                allowed = False
            if allowed:
                text = self._send('GET', target, headers=headers, auth_hops=auth_hops - 1)
                if parsed.hostname == 'passport.yandex.ru':
                    self.session_refreshed = True
                return text
        if status == 401 or 300 <= status < 400:
            self._context = None
            raise AuthRequired(
                'Session rejected or redirected; sign in again',
                reason='unauthorized' if status == 401 else 'unsupported_auth_redirect',
                http_status=status,
            )
        if status == 429:
            delay = 60.0
            raw = response.headers.get('Retry-After', '')
            try:
                delay = max(1.0, float(raw))
            except (ValueError, TypeError):
                try:
                    delay = max(
                        1.0, (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds()
                    )
                except (ValueError, TypeError, OverflowError):
                    pass
            if not math.isfinite(delay):
                delay = 60.0
            self._next = time.monotonic() + delay
            raise RateLimited(delay)
        if status != 200:
            if mutation:
                self._uncertain = True
                raise UncertainClaim(
                    'Claim outcome unknown after HTTP error',
                    reason=ClaimFailureReason.HTTP,
                    http_status=status,
                )
            raise TransportError(f'Unexpected HTTP status {status}')
        text = response.text
        if challenge_page(text):
            self._blocked = True
            raise AccessDenied('Active challenge page; resolve manually in browser')
        return text

    def _fetch_auth_page(self):
        return self._send(
            'GET',
            PAGE,
            auth_hops=3,
            headers={
                'accept': 'text/html,application/xhtml+xml',
                'referer': ORIGIN + '/',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'same-origin',
                'sec-fetch-user': '?1',
            },
        )

    def fetch_live_page(self) -> str:
        """Read bootstrap HTML without submitting viewing progress or claiming rewards."""
        with self._lock:
            return self._send(
                'GET',
                ORIGIN + '/live',
                headers={'accept': 'text/html,application/xhtml+xml', 'referer': ORIGIN + '/'},
            )

    def diagnose_auth(self) -> dict[str, bool | int]:
        with self._lock:
            return context_diagnostics(self._fetch_auth_page())

    def check_auth(self, *, force=True) -> AuthContext:
        with self._lock:
            if not force and self._context and time.monotonic() - self._auth_at < 3600:
                return self._context
            self._context = None
            text = self._fetch_auth_page()
            self._context = page_context(text)
            self._auth_at = time.monotonic()
            return self._context

    def _api(self, operation, params=None):
        context = self.check_auth(force=False)
        mutation = operation.startswith(('claim_', 'submit_')) or operation == 'open_mission'
        legacy = operation in ('status', 'submit_game_result', 'open_mission')
        url = (
            (ORIGIN + '/api/resolve/?' + urlencode({'r': ENDPOINTS[operation]}))
            if legacy
            else API + ENDPOINTS[operation]
        )
        headers = {
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9' if legacy else context.language,
            'content-type': 'application/json',
            'origin': ORIGIN,
            'referer': PAGE,
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'sk': context.sk.get_secret_value(),
            'x-market-app-version': context.version,
            'x-market-front-glue': context.glue,
            'x-market-apphost-target': 'PROMOLOYALTY',
            'x-market-core-service': '<UNKNOWN>',
            'x-market-page-id': 'market:fortune-wheel',
            'x-requested-with': 'XMLHttpRequest',
            'x-retpath-y': PAGE,
        }
        text = self._send(
            'POST',
            url,
            headers=headers,
            mutation=mutation,
            payload={'path': PATH, 'params': [params or {}] if legacy else params or {}},
        )
        reason = ClaimFailureReason.INVALID_JSON
        try:
            response = json.loads(text)
            reason = (
                ClaimFailureReason.API_ERROR
                if isinstance(response, dict)
                else ClaimFailureReason.INVALID_OBJECT
            )
            return _dict(response)
        except (ValueError, ProtocolError):
            if mutation:
                self._uncertain = True
                raise UncertainClaim(
                    'Claim outcome unknown; invalid API acknowledgement',
                    reason=reason,
                    http_status=200,
                ) from None
            raise ProtocolError('Invalid API response') from None

    def balance(self) -> int:
        with self._lock:
            coins = _at(self._api('balance'), 'result', 'wheelInfo', 'userInfo', 'coins')
            if type(coins) is not int or coins < 0:
                raise ProtocolError('Invalid coin balance')
            return coins

    def wheel_missions(self) -> list[WheelMission]:
        with self._lock:
            items = _list(
                _at(self._api('wheel_missions'), 'result', 'missionsResponse', 'missions')
            )
            missions = []
            for item in items:
                item = _dict(item)
                if item.get('type') != 'for_coin':
                    continue
                progress = _dict(item['progress']) if 'progress' in item else None
                missions.append(
                    _validate(
                        WheelMission,
                        {
                            'mission_id': _at(item, 'id'),
                            'title': item.get('title', ''),
                            'status': _at(item, 'status'),
                            'reward': _at(item, 'completeCost'),
                            'accumulated': _at(progress, 'accumulatedValue')
                            if progress is not None
                            else None,
                            'goal': _at(progress, 'goalValue') if progress is not None else None,
                        },
                    )
                )
            return missions

    def complete_free_wheel_mission(self, mission_id: str) -> WheelMissionAttempt:
        """Click only exact missions verified live; never join channels or follow links."""
        with self._lock:
            self._require_claims()
            if mission_id not in FREE_CLICK_MISSIONS:
                return WheelMissionAttempt(mission_id=mission_id, state='unsupported')
            mission = next((m for m in self.wheel_missions() if m.mission_id == mission_id), None)
            if mission is None or mission.reward <= 0 or mission.status not in ('new', 'completed'):
                return WheelMissionAttempt(mission_id=mission_id, state='unavailable')
            if mission.status == 'completed':
                return WheelMissionAttempt(mission_id=mission_id, state='already_completed')
            before = self.balance()
            self._api('open_mission', {'missionId': mission_id, 'surface': 'wheel'})
            try:
                updated = next(
                    (m for m in self.wheel_missions() if m.mission_id == mission_id), None
                )
                after = self.balance()
            except MarketError:
                self._uncertain = True
                raise UncertainClaim('Wheel mission outcome could not be verified') from None
            completed = updated is not None and updated.status == 'completed'
            if not completed:
                self._uncertain = True
            return WheelMissionAttempt(
                mission_id=mission_id,
                state='completed' if completed else 'unconfirmed',
                balance_before=before,
                balance_after=after,
            )

    def daily_status(self) -> DailyStatus:
        with self._lock:
            info = _dict(_at(self._api('daily'), 'result', 'dailySignInInfo'))
            available = _at(info, 'rewardAvailable')
            if type(available) is not bool:
                raise ProtocolError('Invalid daily availability')
            timestamp = info.get('nextRewardTs')
            deadline = None
            if timestamp is not None:
                if type(timestamp) is not int or timestamp < 0:
                    raise ProtocolError('Invalid daily reward timestamp')
                if timestamp:
                    try:
                        deadline = datetime.fromtimestamp(timestamp, UTC)
                    except (ValueError, OverflowError, OSError):
                        raise ProtocolError('Invalid daily reward timestamp') from None
            return DailyStatus(available=available, next_reward_at=deadline)

    def daily_available(self) -> bool:
        return self.daily_status().available

    def _require_claims(self):
        if not self._allow_claims:
            raise ClaimsDisabled('Claims disabled; explicitly enable claims to proceed')

    def claim_daily(self) -> DailyClaim:
        with self._lock:
            self._require_claims()
            status = self.daily_status()
            if not status.available:
                return DailyClaim(state='unavailable', next_reward_at=status.next_reward_at)
            response = self._api('claim_daily')
            # Captured 2026-10-30: REWARD_NOT_AVAILABLE can describe the state AFTER
            # a successful credit. Only an explicit coin reward acknowledges credit.
            try:
                result = _dict(_at(response, 'result'))
                reward = _dict(_at(result, 'rewardInfo', 'reward'))
                info = _dict(_at(result, 'info'))
                amount = reward.get('amount')
                timestamp = info.get('nextRewardTs')
                if (
                    reward.get('type') == 'COIN'
                    and type(amount) is int
                    and amount > 0
                    and info.get('rewardAvailable') is False
                    and type(timestamp) is int
                    and timestamp > 0
                ):
                    return DailyClaim(
                        state='claimed',
                        amount=amount,
                        next_reward_at=datetime.fromtimestamp(timestamp, UTC),
                    )
            except (MarketError, ValueError, OverflowError, OSError):
                pass
            # Unknown acknowledgement is still not proof of credit.
            return DailyClaim(state='submitted_unverified')

    def games(self) -> Hub:
        with self._lock:
            result = _at(self._api('hub'), 'result')
            section = _at(result, 'gamesSection')
            candidates = list(_list(_at(section, 'games')))
            featured = _dict(section).get('featuredGame')
            if featured:
                candidates.append(_at(featured, 'button', 'action', 'openGameAction'))
            missions = []
            for group in _list(_at(result, 'missionsSection', 'missionGroups')):
                for mission in _list(_at(group, 'missions')):
                    # Completed missions in captured hub responses omit progress entirely.
                    # Absence is unknown, not zero and never evidence for claiming a reward.
                    progress = _dict(mission['progress']) if 'progress' in _dict(mission) else None
                    missions.append(
                        _validate(
                            Mission,
                            {
                                'game_id': mission.get('action', {})
                                .get('openGameAction', {})
                                .get('gameId', ''),
                                'mission_id': _at(mission, 'missionId'),
                                'status': _at(mission, 'status'),
                                'reward': _at(mission, 'rewardAmount'),
                                'accumulated': _at(progress, 'accumulatedValue')
                                if progress is not None
                                else None,
                                'goal': _at(progress, 'goalValue')
                                if progress is not None
                                else None,
                            },
                        )
                    )
                    action = _dict(mission).get('action', {})
                    game = _dict(action).get('openGameAction')
                    if game:
                        candidates.append(game)
            games = {}
            for item in candidates:
                item = _dict(item)
                if item.get('gameId') and item.get('gameToken'):
                    game = _validate(Game, {'game_id': item['gameId'], 'token': item['gameToken']})
                    games[game.game_id] = game
            return Hub(games=list(games.values()), missions=missions)

    def claim_mission(self, mission_id: str) -> GameClaim:
        """Submit only event types verified against the current game bundles and API."""
        with self._lock:
            self._require_claims()
            hub = self.games()
            mission = next((m for m in hub.missions if m.mission_id == mission_id), None)
            if not self._submit_game_results or mission is None:
                return GameClaim(claimed=[], unconfirmed=[])
            if mission.status in ('MISSION_STATUS_COMPLETED', 'MISSION_STATUS_RECEIVED'):
                return GameClaim(claimed=[], unconfirmed=[])
            events = {
                ('jump', 'jump_enemy_'): 'ENEMY_STOMP',
                ('hexagon', 'hexagon_two_towers_'): 'TWO_TOWERS_ONE_TURN',
                ('2048', '2048_board_tile_256_'): 'TILE_REACH_256',
            }
            event = next(
                (
                    event
                    for (game_id, prefix), event in events.items()
                    if mission.game_id == game_id and mission_id.startswith(prefix)
                ),
                None,
            )
            if not event or mission.goal is None or mission.accumulated is None:
                return GameClaim(claimed=[], unconfirmed=[])
            remaining = mission.goal - mission.accumulated
            if remaining <= 0:
                return GameClaim(claimed=[], unconfirmed=[])
            game = next((g for g in hub.games if g.game_id == mission.game_id), None)
            if game is None:
                raise ProtocolError('Mission game is absent from hub')
            self._api('open_mission', {'missionId': mission_id})
            self._api(
                'submit_game_event',
                {
                    'gameToken': game.token.get_secret_value(),
                    'eventType': event,
                    'count': remaining,
                },
            )
            try:
                updated = next(
                    (m for m in self.games().missions if m.mission_id == mission_id), None
                )
            except MarketError:
                self._uncertain = True
                raise UncertainClaim(
                    'Mission outcome could not be verified',
                    reason=ClaimFailureReason.API_ERROR,
                ) from None
            if updated and updated.status in (
                'MISSION_STATUS_COMPLETED',
                'MISSION_STATUS_RECEIVED',
            ):
                return GameClaim(claimed=[mission_id], unconfirmed=[])
            self._uncertain = True
            return GameClaim(claimed=[], unconfirmed=[mission_id])

    def _game(self, game_id):
        for game in self.games().games:
            if game.game_id == game_id:
                return game
        raise ProtocolError('Game not present in current server hub')

    def _levels(self, game):
        response = self._api('status', {'gameToken': game.token.get_secret_value()})
        return self._parse_levels(response)

    @staticmethod
    def _parse_levels(response):
        results = _list(_at(response, 'results'))
        if len(results) != 1:
            raise ProtocolError('Expected one resolver result')
        return [_validate(Level, x) for x in _list(_at(results[0], 'data', 'gameStatus', 'levels'))]

    def game_levels(self, game_id: str) -> list[Level]:
        with self._lock:
            return self._levels(self._game(game_id))

    def claim_game(self, game_id: str) -> GameClaim:
        with self._lock:
            self._require_claims()
            game = self._game(game_id)
            levels = self._levels(game)
            names = {level.level: level.claim_name for level in levels if level.claim_name}
            if not self._submit_game_results or not names:
                return GameClaim(claimed=[], unconfirmed=[])
            score = max(level.requiredScore for level in levels)
            if score <= 0:
                return GameClaim(claimed=[], unconfirmed=[])
            response = self._api(
                'submit_game_result',
                {
                    'gameToken': game.token.get_secret_value(),
                    'score': score,
                },
            )
            try:
                updated = self._parse_levels(response)
            except MarketError:
                self._uncertain = True
                raise UncertainClaim(
                    'Game result outcome unknown',
                    reason=ClaimFailureReason.API_ERROR,
                ) from None
            # Current frontend maps isAchieved to "received". Submission grants
            # the level itself; the obsolete RewardBulk resolver must not be called.
            received = {level.level for level in updated if level.isAchieved}
            claimed = sorted(name for level, name in names.items() if level in received)
            unconfirmed = sorted(name for level, name in names.items() if level not in received)
            if unconfirmed:
                self._uncertain = True
            return GameClaim(claimed=claimed, unconfirmed=unconfirmed)
