"""Only confirmed read/claim endpoints. No generic public resolver or game events."""

import json
import math
import re
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from pydantic import ValidationError

from .errors import (
    AccessDenied,
    AuthRequired,
    ClaimsDisabled,
    MarketError,
    ProtocolError,
    RateLimited,
    TransportError,
    UncertainClaim,
)
from .models import AuthContext, DailyClaim, Game, GameClaim, Hub, Level, Mission

ORIGIN = "https://market.yandex.ru"
PATH = "/kolesoprizov?track=menu"
PAGE = ORIGIN + PATH
API = ORIGIN + "/api/web/market.front.mfPromoLoyalty.MfPromoLoyalty/"
ENDPOINTS = {
    "balance": "resolveFortuneWheelMainScreenData",
    "daily": "resolveFortuneWheelDailyRewardsScreenData",
    "claim_daily": "resolveFortuneWheelDailySignInReceiveReward",
    "hub": "resolveGamesHubMiniMainScreenDataV2",
    "status": "src/resolvers/gamesHub/resolveGamesHubGameStatusV2:resolveGamesHubGameStatusV2",
    "claim_game": (
        "src/resolvers/cashbackLevels/fortune/resolveFetchFortuneGameRewardBulk:"
        "resolveFortuneGameRewardBulk"
    ),
}


def _dict(value):
    if not isinstance(value, dict):
        raise ProtocolError("Expected API object; schema may have changed")
    if value.get("error") or value.get("errors"):
        raise ProtocolError("API reported an error")
    return value


def _at(value, *keys):
    try:
        for key in keys:
            value = _dict(value)[key]
        return value
    except KeyError:
        raise ProtocolError("Required API field missing; schema may have changed") from None


def _list(value):
    if not isinstance(value, list):
        raise ProtocolError("Expected API list")
    return value


def _validate(cls, value):
    try:
        return cls.model_validate(value)
    except ValidationError:
        raise ProtocolError("Invalid API field types; schema may have changed") from None


class MarketClient:
    """One account per instance; synchronous operations serialized by an RLock.

    session: curl_cffi Session-compatible object. Caller owns close/persistence.
    No mutation is automatically retried, including on stale auth or 5xx.
    """

    def __init__(self, session, *, allow_claims=False, interval=2.0):
        if not math.isfinite(interval) or not 0 <= interval <= 3600:
            raise ValueError("interval must be nonnegative")
        self._session = session
        self._allow_claims = allow_claims
        self._interval = interval
        self._next = 0.0
        self._context = None
        self._auth_at = 0.0
        self._lock = threading.RLock()
        self._blocked = False
        self._uncertain = False

    def _send(self, method, url, *, headers, payload=None, mutation=False):
        if self._blocked:
            raise AccessDenied("Access denied earlier; stop and resolve manually")
        if self._uncertain:
            raise UncertainClaim("Previous claim outcome unknown; reconcile before restarting")
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
                raise UncertainClaim("Claim outcome unknown; do not blindly retry") from None
            raise TransportError("Network request failed") from None
        finally:
            self._next = time.monotonic() + self._interval
        status = response.status_code
        if status == 403:
            self._blocked = True
            raise AccessDenied("HTTP 403: access denied; no bypass attempted")
        if status == 401 or 300 <= status < 400:
            self._context = None
            raise AuthRequired("Session rejected or redirected; sign in locally again")
        if status == 429:
            delay = 60.0
            raw = response.headers.get("Retry-After", "")
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
                raise UncertainClaim("Claim outcome unknown after HTTP error")
            raise TransportError(f"Unexpected HTTP status {status}")
        text = response.text
        if re.search(r"(?:showcaptcha|smartcaptcha|/captcha)", text, re.I):
            self._blocked = True
            raise AccessDenied("Challenge detected; resolve manually in browser")
        return text

    def check_auth(self, *, force=True) -> AuthContext:
        with self._lock:
            if not force and self._context and time.monotonic() - self._auth_at < 3600:
                return self._context
            self._context = None
            text = self._send(
                "GET",
                PAGE,
                headers={
                    "accept": "text/html,application/xhtml+xml",
                    "referer": ORIGIN + "/",
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "same-origin",
                    "sec-fetch-user": "?1",
                },
            )

            def extract(key):
                match = re.search(r'"' + re.escape(key) + r'"\s*:\s*("(?:[^"\\]|\\.)*")', text)
                if not match:
                    return ""
                try:
                    return json.loads(match[1])
                except ValueError:
                    raise ProtocolError("Invalid page context") from None

            sk, login = extract("sk"), extract("login")
            if not sk or not login:
                raise AuthRequired("Authenticated profile missing; sign in locally again")
            version, glue = extract("-version"), extract("marketFrontGlue")
            if "-desktop." not in version or not glue:
                raise ProtocolError("Market page context missing or changed")
            self._context = _validate(
                AuthContext,
                {
                    "sk": sk,
                    "login": login,
                    "version": version,
                    "glue": glue,
                    "language": extract("lang") or "ru",
                },
            )
            self._auth_at = time.monotonic()
            return self._context

    def _api(self, operation, params=None):
        context = self.check_auth(force=False)
        mutation = operation.startswith("claim_")
        legacy = operation in ("status", "claim_game")
        url = (
            (ORIGIN + "/api/resolve/?" + urlencode({"r": ENDPOINTS[operation]}))
            if legacy
            else API + ENDPOINTS[operation]
        )
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9" if legacy else context.language,
            "content-type": "application/json",
            "origin": ORIGIN,
            "referer": PAGE,
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "sk": context.sk.get_secret_value(),
            "x-market-app-version": context.version,
            "x-market-front-glue": context.glue,
            "x-market-apphost-target": "WEB" if operation == "claim_game" else "PROMOLOYALTY",
            "x-market-core-service": "<UNKNOWN>",
            "x-market-page-id": "market:fortune-wheel",
            "x-requested-with": "XMLHttpRequest",
            "x-retpath-y": PAGE,
        }
        text = self._send(
            "POST",
            url,
            headers=headers,
            mutation=mutation,
            payload={"path": PATH, "params": [params or {}] if legacy else params or {}},
        )
        try:
            return _dict(json.loads(text))
        except (ValueError, ProtocolError):
            if mutation:
                self._uncertain = True
                raise UncertainClaim("Claim outcome unknown; invalid API acknowledgement") from None
            raise ProtocolError("Invalid API response") from None

    def balance(self) -> int:
        with self._lock:
            coins = _at(self._api("balance"), "result", "wheelInfo", "userInfo", "coins")
            if type(coins) is not int or coins < 0:
                raise ProtocolError("Invalid coin balance")
            return coins

    def daily_available(self) -> bool:
        with self._lock:
            available = _at(self._api("daily"), "result", "dailySignInInfo", "rewardAvailable")
            if type(available) is not bool:
                raise ProtocolError("Invalid daily availability")
            return available

    def _require_claims(self):
        if not self._allow_claims:
            raise ClaimsDisabled("Claims disabled; explicitly enable claims to proceed")

    def claim_daily(self) -> DailyClaim:
        with self._lock:
            self._require_claims()
            if not self.daily_available():
                return DailyClaim(state="unavailable")
            self._api("claim_daily")
            # The reference does not define the daily claim response schema.
            return DailyClaim(state="submitted_unverified")

    def games(self) -> Hub:
        with self._lock:
            result = _at(self._api("hub"), "result")
            section = _at(result, "gamesSection")
            candidates = list(_list(_at(section, "games")))
            featured = _dict(section).get("featuredGame")
            if featured:
                candidates.append(_at(featured, "button", "action", "openGameAction"))
            missions = []
            for group in _list(_at(result, "missionsSection", "missionGroups")):
                for mission in _list(_at(group, "missions")):
                    progress = _at(mission, "progress")
                    missions.append(
                        _validate(
                            Mission,
                            {
                                "mission_id": _at(mission, "missionId"),
                                "status": _at(mission, "status"),
                                "reward": _at(mission, "rewardAmount"),
                                "accumulated": _at(progress, "accumulatedValue"),
                                "goal": _at(progress, "goalValue"),
                            },
                        )
                    )
                    action = _dict(mission).get("action", {})
                    game = _dict(action).get("openGameAction")
                    if game:
                        candidates.append(game)
            games = {}
            for item in candidates:
                item = _dict(item)
                if item.get("gameId") and item.get("gameToken"):
                    game = _validate(Game, {"game_id": item["gameId"], "token": item["gameToken"]})
                    games[game.game_id] = game
            return Hub(games=list(games.values()), missions=missions)

    def _game(self, game_id):
        for game in self.games().games:
            if game.game_id == game_id:
                return game
        raise ProtocolError("Game not present in current server hub")

    def _levels(self, game):
        response = self._api("status", {"gameToken": game.token.get_secret_value()})
        results = _list(_at(response, "results"))
        if len(results) != 1:
            raise ProtocolError("Expected one resolver result")
        return [_validate(Level, x) for x in _list(_at(results[0], "data", "gameStatus", "levels"))]

    def game_levels(self, game_id: str) -> list[Level]:
        with self._lock:
            return self._levels(self._game(game_id))

    def claim_game(self, game_id: str) -> GameClaim:
        with self._lock:
            self._require_claims()
            game = self._game(game_id)
            earned = {
                level.achievedToken.get_secret_value(): level.claim_name
                for level in self._levels(game)
                if level.claim_name
            }
            names = sorted(set(earned.values()))
            if not names:
                return GameClaim(claimed=[], unconfirmed=[])
            response = self._api("claim_game", {"gameId": game.game_id, "levels": names})
            try:
                results = _list(_at(response, "results"))
                if len(results) != 1:
                    raise ProtocolError("Expected one resolver result")
                statuses = _dict(_at(results[0], "data", "result"))
                claimed = sorted(
                    {
                        earned[token]
                        for token, status in statuses.items()
                        if token in earned
                        and isinstance(status, str)
                        and status.lower() == "success"
                    }
                )
            except MarketError:
                self._uncertain = True
                raise UncertainClaim(
                    "Claim outcome unknown; invalid reward acknowledgement"
                ) from None
            return GameClaim(claimed=claimed, unconfirmed=sorted(set(names) - set(claimed)))
