import os

import pytest

from yandex_market_farmer.errors import SessionError
from yandex_market_farmer.session import SessionStore, load_cookies

TEXT = (
    "# Netscape HTTP Cookie File\n"
    "#HttpOnly_.yandex.ru\tTRUE\t/\tTRUE\t0\tSession_id\tprivate\n"
    ".evil.invalid\tTRUE\t/\tTRUE\t0\tx\tsecret\n"
)


def source(tmp_path):
    p = tmp_path / "export.txt"
    p.write_text(TEXT)
    p.chmod(0o600)
    return p


def test_import_filters_domains_preserves_session_cookie(tmp_path):
    jar = load_cookies(source(tmp_path))
    assert len(jar) == 1
    cookie = next(iter(jar))
    assert cookie.name == "Session_id"
    assert cookie.expires is None
    assert cookie.has_nonstandard_attr("HTTPOnly")


def test_rejects_public_file_and_symlink(tmp_path):
    p = source(tmp_path)
    p.chmod(0o644)
    with pytest.raises(SessionError):
        load_cookies(p)
    p.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(SessionError):
        load_cookies(link)


def test_atomic_save_lock_and_reload(tmp_path):
    path = tmp_path / "private" / "cookies.txt"
    with SessionStore(path) as store:
        store.import_file(source(tmp_path))
        with pytest.raises(SessionError):
            with SessionStore(path):
                pass
        assert os.stat(path).st_mode & 0o777 == 0o600
    with SessionStore(path) as store:
        assert next(iter(store.load())).value == "private"


def test_expired_cookie_removed(tmp_path):
    p = source(tmp_path)
    p.write_text(TEXT.replace("\t0\t", "\t1\t"))
    with pytest.raises(SessionError):
        load_cookies(p)


def test_malformed_cookies_never_leak_in_warnings(tmp_path):
    import warnings

    p = source(tmp_path)
    p.write_text("# Netscape HTTP Cookie File\nprivate-secret\n")
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(SessionError) as error:
            load_cookies(p)
    assert "private-secret" not in str(error.value)
    assert not emitted
