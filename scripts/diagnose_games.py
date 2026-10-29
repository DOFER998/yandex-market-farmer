"""Read-only game diagnostics. Raw API responses stay in a private local report."""

import json
import os
from pathlib import Path
from tempfile import mkdtemp

from loguru import logger

from src.common.config import Environment
from src.common.utils import setup_logging
from src.market.client import MarketClient
from src.market.errors import AccessDenied, AuthRequired, MarketError, RateLimited, SessionError
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
        if operation not in ('hub', 'status'):
            raise ValueError('Diagnostics permit only hub and game status reads')
        response = super()._api(operation, params)
        self.record({'operation': operation, 'response': response})
        return response


def inspect_games(client):
    hub = client.games()
    client.record({'parsed_hub': hub.model_dump(mode='json')})
    for game in hub.games:
        client.record({'reading_game': game.game_id})
        try:
            levels = client.game_levels(game.game_id)
            client.record(
                {
                    'game_id': game.game_id,
                    'levels': [
                        level.model_dump(mode='json') | {'claimable': bool(level.claim_name)}
                        for level in levels
                    ],
                }
            )
        except (AccessDenied, AuthRequired, RateLimited, SessionError):
            raise
        except MarketError as exc:
            client.record({'game_id': game.game_id, 'error': type(exc).__name__})


def main():
    os.umask(0o077)
    setup_logging()
    report_path = None
    try:
        account = validate_id(os.environ.get('ACCOUNT', 'main'))
        settings = Environment()
        root = Path('.secrets/games-diagnostics')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        report_path = Path(mkdtemp(prefix='run-', dir=root)) / 'events.jsonl'
        with report_path.open('x', encoding='utf-8') as report:
            with SessionStore(settings.market.session_dir / f'{account}.txt') as store:
                with make_session(store.load()) as session:
                    client = DiagnosticClient(session, report)
                    try:
                        inspect_games(client)
                    finally:
                        store.save(session.cookies.jar)
        logger.info('Диагностика игр сохранена: {}', report_path)
    except Exception as exc:
        logger.error('Диагностика остановлена: {}; отчёт: {}', type(exc).__name__, report_path)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
