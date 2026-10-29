"""Read one run in a read-only transaction; export no credentials or upstream bodies."""

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.common.config import Environment
from src.common.utils import setup_logging


async def main() -> None:
    os.umask(0o077)
    setup_logging()
    engine = None
    try:
        run_id = str(UUID(os.environ.get('RUN', '')))
        engine = create_async_engine(Environment().postgres.dsn)
        async with asyncio.timeout(15):
            async with engine.begin() as connection:
                await connection.execute(text('SET TRANSACTION READ ONLY'))
                run = (
                    (
                        await connection.execute(
                            text(
                                'SELECT id, account_id, trigger, state, started_at, finished_at, '
                                'balance_before, balance_after, error_code '
                                'FROM farm_runs WHERE id=:id'
                            ),
                            {'id': run_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if run is None:
                    logger.error('Запуск не найден: {}', run_id)
                    raise SystemExit(1)
                operations = (
                    (
                        await connection.execute(
                            text(
                                'SELECT kind, state, error_code, created_at FROM operations '
                                'WHERE run_id=:id ORDER BY created_at'
                            ),
                            {'id': run_id},
                        )
                    )
                    .mappings()
                    .all()
                )
                account = (
                    (
                        await connection.execute(
                            text(
                                'SELECT id, auth_state, auto_collect, '
                                'daily_available, daily_reward_at, '
                                'next_run_at, needs_review FROM accounts WHERE id=:id'
                            ),
                            {'id': run['account_id']},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                window = {
                    'start': run['started_at'] - timedelta(minutes=15),
                    'end': run['started_at'] + timedelta(minutes=15),
                }
                nearby_runs = (
                    (
                        await connection.execute(
                            text(
                                'SELECT id, account_id, trigger, state, started_at, finished_at, '
                                'balance_before, balance_after, error_code FROM farm_runs '
                                'WHERE started_at BETWEEN :start AND :end ORDER BY started_at'
                            ),
                            window,
                        )
                    )
                    .mappings()
                    .all()
                )
                nearby_operations = (
                    (
                        await connection.execute(
                            text(
                                'SELECT run_id, account_id, kind, state, error_code, created_at '
                                'FROM operations WHERE created_at BETWEEN :start AND :end '
                                'ORDER BY created_at'
                            ),
                            window,
                        )
                    )
                    .mappings()
                    .all()
                )
        report = {
            'run': dict(run),
            'operations': [dict(item) for item in operations],
            'account_current': dict(account) if account else None,
            'nearby_runs': [dict(item) for item in nearby_runs],
            'nearby_operations': [dict(item) for item in nearby_operations],
        }
        directory = Path('.secrets/diagnostics')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f'{run_id}.json'
        if path.exists():
            path = directory / f'{run_id}-{uuid4().hex[:8]}.json'
        # Exclusive creation avoids overwriting a file or following an existing symlink.
        with path.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, default=str)
        logger.info('Отчёт сохранён: {}', path)
    except (ValueError, FileExistsError) as exc:
        logger.error('Проверь RUN (UUID) и отсутствие готового отчёта: {}', type(exc).__name__)
        raise SystemExit(1) from None
    except Exception as exc:
        logger.error('Чтение журнала не удалось: {}', type(exc).__name__)
        raise SystemExit(1) from None
    finally:
        if engine:
            await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
