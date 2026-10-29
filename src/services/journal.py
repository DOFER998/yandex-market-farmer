import asyncio
from collections.abc import Callable

from loguru import logger

from src.database.models import BalanceSnapshot, FarmRun, Operation
from src.market.errors import UncertainClaim
from src.market.models import DailyClaim, GameClaim
from src.services.ports import Runs


class OperationJournal:
    """Synchronous worker bridge: a committed pending row always precedes the request."""

    def __init__(
        self,
        repository: Runs,
        loop: asyncio.AbstractEventLoop,
        run: FarmRun,
    ) -> None:
        self._repository = repository
        self._loop = loop
        self._run = run

    def _persist(self, row: Operation | BalanceSnapshot) -> None:
        future = asyncio.run_coroutine_threadsafe(self._repository.save(row), self._loop)
        future.result(timeout=30)

    def call[T](self, kind: str, action: Callable[[], T]) -> T:
        operation = Operation(run_id=self._run.id, account_id=self._run.account_id, kind=kind)
        self._persist(operation)
        try:
            result = action()
        except Exception as exc:
            operation.state = 'uncertain' if isinstance(exc, UncertainClaim) else 'failed'
            operation.error_code = type(exc).__name__
            logger.warning(
                'Operation failed: account={} run={} stage={} error={} reason={} http_status={}',
                operation.account_id,
                operation.run_id,
                kind,
                operation.error_code,
                exc.reason.value if isinstance(exc, UncertainClaim) else None,
                exc.http_status if isinstance(exc, UncertainClaim) else None,
            )
            self._persist(operation)
            raise
        if isinstance(result, DailyClaim):
            operation.state = result.state
        elif isinstance(result, GameClaim):
            operation.state = (
                'unconfirmed'
                if result.unconfirmed
                else ('claimed' if result.claimed else 'unavailable')
            )
        else:
            operation.state = 'success'
        self._persist(operation)
        return result

    def snapshot(self, coins: int) -> None:
        self._persist(
            BalanceSnapshot(account_id=self._run.account_id, run_id=self._run.id, coins=coins)
        )
