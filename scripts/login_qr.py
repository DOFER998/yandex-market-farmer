"""Standalone experimental login: new private session, no replacement of bot accounts."""

import json
import os
import time
from html import escape
from http.cookiejar import CookieJar
from pathlib import Path
from tempfile import mkdtemp

from loguru import logger

from scripts.terminal_qr import show_qr
from src.common.utils import setup_logging
from src.market.client import MarketClient
from src.market.qr_auth import QrAuth
from src.market.session import SessionStore
from src.market.transport import make_session


def main():
    os.umask(0o077)
    setup_logging()
    root = Path('.secrets/qr-login')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(mkdtemp(prefix='run-', dir=root)).resolve()
    page = directory / 'login.html'
    stage = 'initialization'
    auth = None
    with (directory / 'events.jsonl').open('x', encoding='utf-8') as events:

        def record(value):
            events.write(json.dumps(value, ensure_ascii=False) + '\n')
            events.flush()

        try:
            with make_session(CookieJar()) as session:
                auth = QrAuth(session, observe=record)
                link = auth.start()
                page.write_text(
                    '<!doctype html><html lang="ru"><meta charset="utf-8">'
                    '<meta name="referrer" content="no-referrer">'
                    '<title>Подключение Яндекс Маркета</title>'
                    '<h1>Подтвердите вход в Яндексе</h1>'
                    '<p>Это новая сессия для вашего сервера. '
                    'Ссылка действует ограниченное время.</p>'
                    f'<p><a rel="noreferrer" href="{escape(link.get_secret_value(), quote=True)}">'
                    'Открыть Яндекс для подтверждения</a></p>'
                    '<p>Оставьте команду в терминале работающей. Отмена: Ctrl+C.</p></html>',
                    encoding='utf-8',
                )
                show_qr(link)
                logger.info('Запасной вариант — ссылка на странице: {}', page)
                logger.info('Ожидаю подтверждение в Яндексе, максимум 5 минут. Отмена: Ctrl+C.')
                while True:
                    time.sleep(5)
                    if auth.poll():
                        break
                stage = 'verify_market'
                client = MarketClient(session, allow_claims=False)
                client.check_auth()
                coins = client.balance()
                with SessionStore(directory / 'cookies.txt') as store:
                    store.save(session.cookies.jar)
                    store.load()  # Verify session cookies survived storage and domain filtering.
                record({'stage': stage, 'api_verified': True, 'balance': coins})
                logger.info(
                    'Вход проверен API Маркета. Баланс: {}. Новая сессия: {}',
                    coins,
                    directory / 'cookies.txt',
                )
                logger.info('Сессии подключённых аккаунтов не изменены.')
        except (Exception, KeyboardInterrupt) as exc:
            record(
                {
                    'stage': auth.stage if auth and stage != 'verify_market' else stage,
                    'error': type(exc).__name__,
                }
            )
            logger.error(
                'Вход не завершён: {}. Диагностика: {}',
                type(exc).__name__,
                directory / 'events.jsonl',
            )
            raise SystemExit(1) from None
        finally:
            if page.exists():
                page.write_text(
                    '<meta charset="utf-8"><p>Попытка входа завершена. '
                    'Результат указан в терминале.</p>',
                    encoding='utf-8',
                )


if __name__ == '__main__':
    main()
