import argparse
import json
import sys
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from src.common.utils import setup_logging
from src.market.client import MarketClient
from src.market.config import Settings
from src.market.errors import MarketError, SessionError, UncertainClaim
from src.market.session import SessionStore
from src.market.transport import make_session


def main(argv=None):
    setup_logging()
    parser = argparse.ArgumentParser(
        description='Conservative Market rewards client; read-only by default'
    )
    parser.add_argument('--session', type=Path, help='Private session file (or YMF_SESSION_FILE)')
    parser.add_argument(
        '--enable-claims', action='store_true', help='Allow explicit claim commands'
    )
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('import-session').add_argument('source', type=Path)
    commands.add_parser('login').add_argument(
        '--browser', choices=('auto', 'chrome', 'msedge', 'chromium'), default='auto'
    )
    inspection = commands.add_parser(
        'inspect-live', help='Observe manual viewing; save private full API report'
    )
    inspection.add_argument(
        '--browser', choices=('auto', 'chrome', 'msedge', 'chromium'), default='auto'
    )
    inspection.add_argument('--minutes', type=int, choices=range(1, 31), default=10)
    inspection.add_argument(
        '--fresh-login', action='store_true', help='Do not import saved session'
    )
    commands.add_parser('live-report', help='Analyze local live counters; no network').add_argument(
        'report', type=Path
    )
    commands.add_parser(
        'live-probe', help='Fetch live bootstrap over HTTP; no browser'
    ).add_argument(
        '--mobile', action='store_true', help='Request mobile site using Android HTTP profile'
    )
    commands.add_parser('live-sources', help='Download public JS from a saved probe').add_argument(
        'probe', type=Path
    )
    commands.add_parser('live-status', help='Read live counter using mobile HTTP GET')
    for name in ('auth-check', 'diagnose-auth', 'balance', 'daily', 'games', 'claim-daily'):
        commands.add_parser(name)
    for name in ('levels', 'claim-game'):
        commands.add_parser(name).add_argument('game_id')
    args = parser.parse_args(argv)
    try:
        if args.command == 'live-sources':
            from src.market.live_sources import collect_sources

            sys.stdout.write(json.dumps(collect_sources(args.probe), ensure_ascii=False) + '\n')
            return 0
        if args.command == 'live-report':
            from src.market.live import summarize_report

            sys.stdout.write(json.dumps(summarize_report(args.report), ensure_ascii=False) + '\n')
            return 0
        settings = Settings()
        if args.command == 'inspect-live':
            from src.market.inspection import inspect_live

            jar = None
            with SessionStore(args.session or settings.session_file) as store:
                if not args.fresh_login and store.path.exists():
                    try:
                        jar = store.load()
                    except SessionError:
                        logger.warning(
                            'Сохранённая сессия недоступна; войди вручную в открывшемся окне.'
                        )
            result = inspect_live(jar=jar, browser=args.browser, minutes=args.minutes)
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + '\n')
            return 0
        with SessionStore(args.session or settings.session_file) as store:
            if args.command == 'login':
                from src.market.browser_auth import login

                store.save(login(browser=args.browser))
                result = {'session_saved': True, 'api_verified': False}
            elif args.command == 'import-session':
                store.import_file(args.source)
                result = {'imported': True}
            else:
                with make_session(
                    store.load(),
                    mobile=args.command == 'live-status'
                    or (args.command == 'live-probe' and args.mobile),
                ) as session:
                    client = MarketClient(
                        session, allow_claims=args.enable_claims, interval=settings.interval_seconds
                    )
                    try:
                        match args.command:
                            case 'live-status':
                                from src.market.live import parse_live_page

                                state = parse_live_page(client.fetch_live_page())
                                result = state.model_dump() | {
                                    'remaining_to_reported_max': state.remaining,
                                    'source': 'mobile_page',
                                }
                            case 'live-probe':
                                from src.market.live import save_live_probe

                                result = save_live_probe(
                                    client.fetch_live_page(),
                                    client_profile='chrome131_android'
                                    if args.mobile
                                    else 'chrome146',
                                )
                            case 'auth-check':
                                client.check_auth()
                                result = {'authenticated': True}
                            case 'diagnose-auth':
                                result = client.diagnose_auth()
                            case 'balance':
                                result = {'coins': client.balance()}
                            case 'daily':
                                result = {'available': client.daily_available()}
                            case 'games':
                                result = client.games().model_dump(mode='json')
                            case 'levels':
                                result = [
                                    level.model_dump(mode='json')
                                    | {'claimable': bool(level.claim_name)}
                                    for level in client.game_levels(args.game_id)
                                ]
                            case 'claim-daily':
                                result = client.claim_daily().model_dump(mode='json')
                            case 'claim-game':
                                result = client.claim_game(args.game_id).model_dump(mode='json')
                    finally:
                        # Includes Set-Cookie rotation/deletion even on handled API errors.
                        active_error = sys.exception()
                        try:
                            store.save(session.cookies.jar)
                        except (OSError, SessionError):
                            if active_error is not None:
                                logger.warning('Session persistence also failed')
                            elif args.command.startswith('claim-'):
                                raise UncertainClaim(
                                    'Claim may have executed; session save failed; '
                                    'reconcile before retry'
                                ) from None
                            else:
                                raise SessionError('Session persistence failed') from None
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + '\n')
        return 0
    except MarketError as exc:
        logger.warning(f'{type(exc).__name__}: {exc}')
        return 2
    except (OSError, ValidationError, ValueError):
        logger.warning(
            'Local configuration/session error; check paths, permissions and environment'
        )
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
