"""Read current balance/daily state; never claim or change the database review flag."""

import json
import os
from pathlib import Path
from tempfile import mkdtemp

from loguru import logger

from src.common.config import Environment
from src.common.utils import setup_logging
from src.market.client import MarketClient
from src.market.session import SessionStore
from src.market.transport import make_session
from src.services.identity import validate_id


class DiagnosticClient(MarketClient):
    def __init__(self, session, report):
        super().__init__(session, allow_claims=False)
        self.report = report

    def record(self, data):
        self.report.write(json.dumps(data, ensure_ascii=False) + '\n')
        self.report.flush()

    def _api(self, operation, params=None):
        if operation not in ('balance', 'daily'):
            raise ValueError('Diagnostics permit only balance and daily state reads')
        response = super()._api(operation, params)
        self.record({'operation': operation, 'response': response})
        return response


def main():
    os.umask(0o077)
    setup_logging()
    report_path = None
    try:
        account = validate_id(os.environ.get('ACCOUNT', 'main'))
        settings = Environment()
        root = Path('.secrets/daily-diagnostics')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        report_path = Path(mkdtemp(prefix='run-', dir=root)) / 'events.jsonl'
        with report_path.open('x', encoding='utf-8') as report:
            with SessionStore(settings.market.session_dir / f'{account}.txt') as store:
                with make_session(store.load()) as session:
                    client = DiagnosticClient(session, report)
                    try:
                        balance = client.balance()
                        status = client.daily_status()
                        client.record(
                            {'account_id': account, 'balance': balance}
                            | status.model_dump(mode='json')
                        )
                    finally:
                        store.save(session.cookies.jar)
        logger.info('Диагностика daily сохранена: {}', report_path)
    except Exception as exc:
        logger.error('Диагностика остановлена: {}; отчёт: {}', type(exc).__name__, report_path)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
