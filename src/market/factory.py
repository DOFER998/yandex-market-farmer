from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from src.market.client import MarketClient
from src.market.errors import UncertainClaim
from src.market.session import SessionStore
from src.market.transport import make_session


@contextmanager
def account_client(
    path: Path, interval: float, *, submit_game_results: bool = False
) -> Iterator[MarketClient]:
    with SessionStore(path) as store:
        with make_session(store.load()) as session:
            try:
                yield MarketClient(
                    session,
                    allow_claims=True,
                    interval=interval,
                    submit_game_results=submit_game_results,
                )
            finally:
                try:
                    store.save(session.cookies.jar)
                except Exception:
                    raise UncertainClaim('Session persistence failed; reconcile account') from None


@contextmanager
def live_client(path: Path, interval: float):
    from src.market.live_client import LiveClient

    with SessionStore(path) as store:
        with make_session(store.load(), mobile=True) as session:
            try:
                yield LiveClient(session, allow_claims=True, interval=interval)
            finally:
                store.save(session.cookies.jar)
