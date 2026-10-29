"""Interactive local login; never reads passwords, automates games or solves CAPTCHA."""

import math
import shutil
import sys
import time
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from urllib.parse import urlsplit

from .client import PAGE
from .errors import SessionError
from .page import challenge_page, challenge_url
from .session import allowed_domain

LOGIN_URL = "https://passport.yandex.ru/auth?retpath=https%3A%2F%2Fmarket.yandex.ru%2Fkolesoprizov"


def browser_cookies_to_jar(cookies) -> MozillaCookieJar:
    jar = MozillaCookieJar()
    for item in cookies:
        if not allowed_domain(item["domain"]) or item.get("partitionKey"):
            continue
        expiry = item["expires"]
        if not math.isfinite(expiry):
            raise SessionError("Invalid browser cookie expiry")
        expires = int(expiry) if expiry > 0 else None
        domain = item["domain"]
        rest = {"HTTPOnly": ""} if item["httpOnly"] else {}
        cookie = Cookie(
            version=0,
            name=item["name"],
            value=item["value"],
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=domain.startswith("."),
            domain_initial_dot=domain.startswith("."),
            path=item["path"],
            path_specified=True,
            secure=item["secure"],
            expires=expires,
            discard=expires is None,
            comment=None,
            comment_url=None,
            rest=rest,
            rfc2109=False,
        )
        if not cookie.is_expired():
            jar.set_cookie(cookie)
    return jar


def wait_for_session(context, page, *, timeout=300) -> MozillaCookieJar:
    from playwright.sync_api import Error

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.is_closed():
            raise SessionError("Login window closed before authentication")
        # Wait for Passport's return to Market, not merely creation of a Passport cookie.
        url = urlsplit(page.url)
        if (
            url.scheme == "https"
            and url.hostname == "market.yandex.ru"
            and not challenge_url(page.url)
        ):
            applicable = browser_cookies_to_jar(context.cookies([PAGE]))
            if any(c.name == "Session_id" and c.value for c in applicable):
                try:
                    active_challenge = challenge_page(page.content())
                except Error:
                    # Navigation may still be in flight; keep window available for the user.
                    pass
                else:
                    if not active_challenge:
                        return browser_cookies_to_jar(context.cookies())
        page.wait_for_timeout(500)
    raise SessionError("Login timed out: authenticated Market page not ready; run ymf login again")


def _channel(browser):
    if browser != "auto":
        return None if browser == "chromium" else browser
    if (
        Path("/Applications/Google Chrome.app").is_dir()
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
    ):
        return "chrome"
    if Path("/Applications/Microsoft Edge.app").is_dir() or shutil.which("microsoft-edge"):
        return "msedge"
    return None


def login(*, browser="auto") -> MozillaCookieJar:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        raise SessionError("Browser login requires dependencies: uv sync") from None
    print(
        "Войди в Яндекс в открывшемся браузере. Cookies сохранятся автоматически.", file=sys.stderr
    )
    try:
        with sync_playwright() as playwright:
            try:
                instance = playwright.chromium.launch(
                    channel=_channel(browser),
                    headless=False,
                    chromium_sandbox=True,
                )
            except Error:
                raise SessionError(
                    "Cannot open browser. Install Chrome, or run: "
                    "uv run playwright install chromium; then uv run ymf login --browser chromium. "
                    "A desktop graphical session is required."
                ) from None
            try:
                context = instance.new_context(accept_downloads=False)
                page = context.new_page()
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
                return wait_for_session(context, page)
            finally:
                # Cleanup failure must not mask cancellation or the original auth error.
                try:
                    instance.close()
                except Error:
                    pass
    except Error:
        raise SessionError(
            "Browser login interrupted or navigation failed; run ymf login again"
        ) from None
    except KeyboardInterrupt:
        raise SessionError("Login cancelled; existing session was not changed") from None
