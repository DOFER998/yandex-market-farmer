from types import SimpleNamespace

import pytest

from yandex_market_farmer.browser_auth import browser_cookies_to_jar, wait_for_session
from yandex_market_farmer.errors import SessionError
from yandex_market_farmer.session import SessionStore


def cookie(**changes):
    return {
        "name": "Session_id",
        "value": "secret",
        "domain": ".yandex.ru",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "expires": -1,
        "sameSite": "None",
    } | changes


def test_browser_cookies_roundtrip_preserves_flags_and_filters_domains(tmp_path):
    jar = browser_cookies_to_jar(
        [cookie(), cookie(domain=".notyandex.ru"), cookie(name="expired", expires=1)]
    )
    with SessionStore(tmp_path / "private" / "cookies.txt") as store:
        store.save(jar)
        restored = list(store.load())
    assert len(restored) == 1
    assert restored[0].secure
    assert restored[0].has_nonstandard_attr("HTTPOnly")
    assert restored[0].expires is None
    assert restored[0].discard


def test_wait_requires_market_return_and_session_cookie():
    class Page:
        url = "https://passport.yandex.ru/auth"
        waits = 0

        def content(self):
            return (
                '<script>{"sk":"s","login":"a","-version":"v-desktop.1",'
                '"marketFrontGlue":"g"}</script>'
            )

        def is_closed(self):
            return False

        def wait_for_timeout(self, _):
            self.waits += 1
            self.url = "https://market.yandex.ru/kolesoprizov"

    page = Page()
    context = SimpleNamespace(cookies=lambda urls=None: [cookie()])
    jar = wait_for_session(context, page, timeout=2)
    assert page.waits == 1
    assert next(iter(jar)).value == "secret"


def test_close_or_timeout_does_not_return_session():
    page = SimpleNamespace(is_closed=lambda: True)
    with pytest.raises(SessionError):
        wait_for_session(None, page, timeout=1)
    with pytest.raises(SessionError):
        wait_for_session(None, page, timeout=0)


def test_partitioned_cookies_are_not_converted_to_unpartitioned():
    jar = browser_cookies_to_jar(
        [cookie(), cookie(name="partitioned", partitionKey="https://other.ru")]
    )
    assert [c.name for c in jar] == ["Session_id"]


def test_login_cli_saves_without_existing_session(tmp_path, monkeypatch, capsys):
    from yandex_market_farmer import browser_auth
    from yandex_market_farmer.cli import main

    monkeypatch.setattr(browser_auth, "login", lambda **kwargs: browser_cookies_to_jar([cookie()]))
    target = tmp_path / "private" / "cookies.txt"
    assert main(["--session", str(target), "login"]) == 0
    assert "secret" not in capsys.readouterr().out
    with SessionStore(target) as store:
        assert next(iter(store.load())).value == "secret"


def test_browser_closes_and_raw_browser_errors_are_sanitized(monkeypatch):
    from playwright.sync_api import Error

    from yandex_market_farmer import browser_auth

    closed = []

    class Manager:
        def __enter__(self):
            page = SimpleNamespace(goto=lambda *a, **kw: None)
            browser = SimpleNamespace(
                new_context=lambda **kw: SimpleNamespace(new_page=lambda: page),
                close=lambda: closed.append(True),
            )
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: browser))

        def __exit__(self, *_):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", Manager)

    def fail(*args, **kwargs):
        raise Error("cookie secret-value")

    monkeypatch.setattr(browser_auth, "wait_for_session", fail)
    with pytest.raises(SessionError) as error:
        browser_auth.login(browser="chromium")
    assert closed == [True]
    assert "secret-value" not in str(error.value)


def test_failed_login_preserves_previous_session(tmp_path, monkeypatch):
    from yandex_market_farmer import browser_auth
    from yandex_market_farmer.cli import main

    path = tmp_path / "private" / "cookies.txt"
    with SessionStore(path) as store:
        store.save(browser_cookies_to_jar([cookie(value="old")]))

    def cancelled(**kwargs):
        raise SessionError("Cancelled")

    monkeypatch.setattr(browser_auth, "login", cancelled)
    assert main(["--session", str(path), "login"]) == 2
    with SessionStore(path) as store:
        assert next(iter(store.load())).value == "old"


@pytest.mark.parametrize("path", ["/showcaptcha", "/kolesoprizov"])
def test_login_does_not_capture_session_on_active_challenge(path):
    class Page:
        url = "https://market.yandex.ru" + path
        waits = 0

        def is_closed(self):
            return False

        def content(self):
            if self.waits == 0:
                return '<form action="/checkcaptcha"></form>'
            return (
                '<script>{"sk":"s","login":"a","-version":"v-desktop.1",'
                '"marketFrontGlue":"g"}</script>'
            )

        def wait_for_timeout(self, _):
            self.waits += 1
            self.url = "https://market.yandex.ru/kolesoprizov"

    page = Page()
    context = SimpleNamespace(cookies=lambda urls=None: [cookie()])
    wait_for_session(context, page, timeout=2)
    assert page.waits == 1


def test_loaded_market_does_not_require_api_bootstrap_fields():
    """Browser DOM need not retain the bootstrap JSON consumed by the HTTP client."""

    def unexpected_wait(_):
        raise AssertionError("Login must finish once Market and session cookie are ready")

    page = SimpleNamespace(
        url="https://market.yandex.ru/kolesoprizov",
        is_closed=lambda: False,
        content=lambda: "<html><title>Яндекс Маркет</title><body>Колесо призов</body></html>",
        wait_for_timeout=unexpected_wait,
    )
    context = SimpleNamespace(cookies=lambda urls=None: [cookie()])
    assert next(iter(wait_for_session(context, page))).value == "secret"
