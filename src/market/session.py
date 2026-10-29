"""Local, owner-only Netscape sessions. Linux/macOS; no password handling."""

import fcntl
import io
import os
import stat
import tempfile
import warnings
from http.cookiejar import CookieJar, MozillaCookieJar
from pathlib import Path

from src.market.errors import SessionError


def allowed_domain(domain: str) -> bool:
    domain = domain.lstrip('.').lower()
    return domain == 'yandex.ru' or domain.endswith('.yandex.ru')


def load_cookies(path: Path) -> MozillaCookieJar:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise SessionError(
                    'Session file must be owner-only (chmod 600), regular, owned by you'
                )
            text = file.read(1_048_577)
        if len(text) > 1_048_576:
            raise SessionError('Session file too large')
        # Netscape exports use both 0 and empty expiry for session cookies.
        lines = []
        for line in text.splitlines():
            if line and (not line.startswith('#') or line.startswith('#HttpOnly_')):
                parts = line.split('\t')
                if len(parts) == 7 and parts[4] == '0':
                    parts[4] = ''
                    line = '\t'.join(parts)
            lines.append(line)
        jar = MozillaCookieJar()
        with warnings.catch_warnings():
            # cookiejar includes malformed input in its diagnostic warnings.
            warnings.simplefilter('ignore', UserWarning)
            jar._really_load(io.StringIO('\n'.join(lines)), str(path), True, False)
        for cookie in list(jar):
            if not allowed_domain(cookie.domain):
                jar.clear(cookie.domain, cookie.path, cookie.name)
        if not any(c.name == 'Session_id' and c.value for c in jar):
            raise SessionError('No unexpired Yandex Session_id; sign in locally and export again')
        return jar
    except SessionError:
        raise
    except (OSError, ValueError):
        raise SessionError('Cannot read private Netscape session file') from None


class SessionStore:
    """Lock spans load → network operations → save, not just the write."""

    def __init__(self, path: Path):
        self.path = Path(path).absolute()
        self._fd = None

    def __enter__(self):
        try:
            parent = self.path.parent
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if parent.is_symlink() or parent.resolve() != parent:
                raise SessionError('Session directory must not traverse symlinks')
            info = parent.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise SessionError('Session directory must be owned by you with mode 700')
            self._fd = os.open(
                str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
            )
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except (OSError, SessionError):
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            raise SessionError('Session busy or unsafe session directory/lock') from None

    def __exit__(self, *_):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def load(self):
        self._require_lock()
        return load_cookies(self.path)

    def delete(self) -> None:
        self._require_lock()
        if self.path.is_symlink():
            raise SessionError('Refusing symlink session destination')
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise SessionError('Cannot remove session file') from None
        # Keep the lock inode: removing it could allow concurrent locks on different inodes.

    def import_file(self, source: Path):
        self.save(load_cookies(source))

    def _require_lock(self):
        if self._fd is None:
            raise SessionError('SessionStore must be used as a context manager')

    def save(self, jar: CookieJar):
        self._require_lock()
        if self.path.is_symlink():
            raise SessionError('Refusing symlink session destination')
        fd, name = tempfile.mkstemp(prefix='.session-', dir=self.path.parent)
        try:
            os.close(fd)
            safe = MozillaCookieJar(name)
            for cookie in jar:
                if allowed_domain(cookie.domain) and not cookie.is_expired():
                    safe.set_cookie(cookie)
            safe.save(ignore_discard=True, ignore_expires=False)
            with open(name, 'rb') as file:
                os.fsync(file.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
