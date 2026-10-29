"""Passive, local browser API inspection. Never replays requests or drives a player."""

import asyncio
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .browser_auth import _channel
from .errors import SessionError

# Only these schema names may be written literally. Dynamic map keys may contain tokens/PII.
FIELDS = set(
    """result results data params path error errors code status success message
items item type name value values meta payload response request actions action
coins coinsAmount coinBalance balance amount count total limit remaining available
rewardAvailable rewardAmount rewardPoints points maxCoins dailyLimit earnedCoins
watchedSeconds watchTime duration seconds elapsed progress accumulatedValue goalValue
isAvailable isAchieved isCompleted completed enabled hasMore next hasNext totalCount
streams stream streamId livestreams livestreamId live liveId videos video videoId
broadcast broadcastId broadcasts content contents contentId playlist session sessionId
rewards reward missions missionGroups missionId games gameId gameToken levels level
requiredScore achievedToken wheelInfo userInfo dailySignInInfo gamesSection missionsSection
openGameAction featuredGame button title description url id timestamp date startTime endTime
viewingTime viewingDuration watchDuration timeSpent watchedTime totalTime watchProgress
rewardStatus rewardType rewardValue coinCount maxReward dailyReward maxDailyReward
coinReward watching watchingTime watchingSeconds userRewards userProgress
""".split()
)
COUNTERS = set(
    """coins coinsAmount coinBalance balance amount count total limit remaining
rewardAmount rewardPoints points maxCoins dailyLimit earnedCoins watchedSeconds watchTime
duration seconds elapsed accumulatedValue goalValue totalCount requiredScore viewingTime
viewingDuration watchDuration timeSpent watchedTime totalTime watchProgress rewardValue
coinCount maxReward dailyReward maxDailyReward coinReward watchingTime watchingSeconds
""".split()
)
MAX_BODY = 1_048_576


def redact_json(value):
    budget = [1000]

    def visit(node, field="", depth=0):
        budget[0] -= 1
        if depth > 10 or budget[0] < 0:
            return "<truncated>"
        if node is None or type(node) is bool:
            return node
        if type(node) in (int, float):
            if field in COUNTERS and abs(node) <= 1_000_000_000 and math.isfinite(node):
                return node
            return "<number>"
        if isinstance(node, str):
            return "<string>"
        if isinstance(node, list):
            return [visit(child, depth=depth + 1) for child in node[:10]]
        if isinstance(node, dict):
            result = {}
            for index, (key, child) in enumerate(node.items()):
                if index >= 100 or budget[0] < 0:
                    result["<truncated>"] = True
                    break
                safe_key = key if key in FIELDS else f"<field:{index}>"
                result[safe_key] = visit(child, key, depth + 1)
            return result
        return "<unsupported>"

    return visit(value)


def describe_endpoint(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "market.yandex.ru":
            return None
        if not parsed.path.startswith("/api/"):
            return None
        endpoint = {"host": "market.yandex.ru", "route": "/api/<redacted>"}
        if parsed.path.rstrip("/") == "/api/resolve":
            endpoint["route"] = "/api/resolve/"
            resolvers = parse_qs(parsed.query).get("r", [])
            if len(resolvers) == 1 and re.fullmatch(
                r"src/resolvers/[A-Za-z0-9_/]+:resolve[A-Za-z0-9_]+", resolvers[0]
            ):
                endpoint["resolver"] = resolvers[0]
        elif re.fullmatch(
            r"/api/web/[A-Za-z][A-Za-z0-9_.]{0,160}/resolve[A-Za-z0-9_]{1,120}", parsed.path
        ):
            endpoint["route"] = parsed.path
        return endpoint
    except ValueError:
        return None


class Report:
    def __init__(self, root: Path, *, max_events=3000):
        self.root = Path(root).absolute()
        self.max_events = max_events
        self.count = 0
        self.dropped = 0
        self.bytes_written = 0
        self.file = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.root.stat()
        if self.root.resolve() != self.root or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SessionError(
                "Inspection directory must be private, owned by you, without symlinks"
            )
        directory = Path(tempfile.mkdtemp(prefix="run-", dir=self.root))
        self.path = directory / "events.jsonl"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.file = os.fdopen(fd, "w", encoding="utf-8")
        return self

    def write(self, record):
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        size = len(encoded.encode())
        if self.count >= self.max_events or self.bytes_written + size > 8_388_608:
            self.dropped += 1
            return
        self.file.write(encoded)
        self.file.flush()
        self.count += 1
        self.bytes_written += size

    def __exit__(self, *_):
        if self.file:
            self.file.close()


class Recorder:
    def __init__(self, report):
        self.report = report
        self.started = time.monotonic()
        self.pending = {}
        self.sequence = 0
        self.tasks = set()
        self.callback_errors = 0
        self.recording_failed = False

    def _record(self, kind, identity, **fields):
        try:
            self.report.write(
                {
                    "kind": kind,
                    "id": identity,
                    "elapsed_s": round(time.monotonic() - self.started, 3),
                    **fields,
                }
            )
        except OSError:
            self.recording_failed = True

    def request(self, request):
        try:
            self._request(request)
        except Exception:
            self.callback_errors += 1
            pending = self.pending.pop(id(request), None)
            if pending:
                self._record("request_unavailable", pending[1])

    def _request(self, request):
        endpoint = describe_endpoint(request.url)
        if endpoint is None or request.resource_type not in ("xhr", "fetch"):
            return
        if self.sequence >= self.report.max_events or len(self.pending) >= 100:
            self.report.dropped += 1
            return
        self.sequence += 1
        identity = self.sequence
        self.pending[id(request)] = (request, identity)
        payload = "<not-json>"
        raw = request.post_data_buffer
        if raw and len(raw) <= MAX_BODY:
            try:
                payload = redact_json(json.loads(raw))
            except (ValueError, UnicodeError, RecursionError):
                pass
        self._record("request", identity, method=request.method, endpoint=endpoint, payload=payload)

    async def finished(self, request):
        pending = self.pending.pop(id(request), None)
        if pending is None:
            return
        identity = pending[1]
        try:
            response = await request.response()
            if response is None:
                self._record("response_unavailable", identity)
                return
            content_type = response.headers.get("content-type", "").lower()
            size = response.headers.get("content-length", "")
            payload = "<not-json-or-too-large>"
            if "json" in content_type and (not size.isdigit() or int(size) <= MAX_BODY):
                body = await response.body()
                if len(body) <= MAX_BODY:
                    payload = redact_json(json.loads(body))
            self._record("response", identity, status=response.status, payload=payload)
        except Exception:
            self.callback_errors += 1
            self._record("response_unavailable", identity)

    def failed(self, request):
        pending = self.pending.pop(id(request), None)
        if pending is not None:
            self._record("request_failed", pending[1])

    def schedule_finished(self, request):
        task = asyncio.create_task(self.finished(request))
        self.tasks.add(task)

        def completed(done):
            self.tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self.callback_errors += 1
                self.recording_failed = True

        task.add_done_callback(completed)

    async def drain(self):
        if self.tasks:
            _, pending = await asyncio.wait(self.tasks, timeout=3)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


def _browser_cookies(jar):
    result = []
    for cookie in jar or []:
        if cookie.is_expired():
            continue
        item = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
            "httpOnly": cookie.has_nonstandard_attr("HTTPOnly"),
        }
        if cookie.expires is not None:
            item["expires"] = cookie.expires
        result.append(item)
    return result


async def _inspect(report, *, jar, browser, minutes):
    from playwright.async_api import Error, async_playwright

    recorder = Recorder(report)
    report.write(
        {
            "kind": "start",
            "format_version": 1,
            "max_minutes": minutes,
            "scope": "market.yandex.ru/api/ xhr/fetch only; values redacted",
        }
    )
    async with async_playwright() as playwright:
        try:
            instance = await playwright.chromium.launch(
                channel=_channel(browser),
                headless=False,
                chromium_sandbox=True,
            )
        except Error:
            raise SessionError(
                "Cannot launch browser; install Chrome or run uv run playwright install chromium"
            ) from None
        try:
            context = await instance.new_context(accept_downloads=False)
            if jar:
                await context.add_cookies(_browser_cookies(jar))
            context.on("request", recorder.request)
            context.on("requestfinished", recorder.schedule_finished)
            context.on("requestfailed", recorder.failed)
            page = await context.new_page()
            print(
                "Открыл браузер для исследования. Если нужно, войди в Яндекс.\n"
                "Открой эфир и посмотри 30–60 секунд, следя за счётчиком монет.\n"
                "Затем закрой это окно браузера. Отчёт сохраняется непрерывно.\n"
                f"Файл: {report.path}",
                file=sys.stderr,
                flush=True,
            )
            try:
                await page.goto(
                    "https://market.yandex.ru/live", wait_until="domcontentloaded", timeout=30_000
                )
            except Error:
                print(
                    "Страница не загрузилась автоматически; открой эфир вручную в этом окне.",
                    file=sys.stderr,
                )
            deadline = time.monotonic() + minutes * 60
            while instance.is_connected() and context.pages and time.monotonic() < deadline:
                if recorder.recording_failed:
                    raise SessionError("Inspection report write failed; capture stopped")
                await asyncio.sleep(0.25)
        except Error:
            # Closing the research window is an expected way of ending a capture.
            report.write({"kind": "browser_closed_or_interrupted"})
        finally:
            await recorder.drain()
            try:
                await instance.close()
            except Error:
                pass
            await recorder.drain()
            report.write(
                {
                    "kind": "end",
                    "requests": recorder.sequence,
                    "callback_errors": recorder.callback_errors,
                    "dropped_events": report.dropped,
                }
            )


def inspect_live(
    *, jar=None, browser="auto", minutes=10, report_root=Path(".secrets/live-inspection")
):
    if not 1 <= minutes <= 30:
        raise SessionError("Inspection duration must be between 1 and 30 minutes")
    with Report(report_root) as report:
        try:
            asyncio.run(_inspect(report, jar=jar, browser=browser, minutes=minutes))
        except KeyboardInterrupt:
            print("Запись остановлена. Уже собранные данные сохранены.", file=sys.stderr)
        except ImportError:
            raise SessionError("Install browser dependencies with uv sync") from None
        return {
            "report": str(report.path),
            "events": report.count,
            "dropped_events": report.dropped,
        }
