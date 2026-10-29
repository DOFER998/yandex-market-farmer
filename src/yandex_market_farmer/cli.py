import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .client import MarketClient
from .config import Settings
from .errors import MarketError, SessionError, UncertainClaim
from .session import SessionStore
from .transport import make_session


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Conservative Market rewards client; read-only by default"
    )
    parser.add_argument("--session", type=Path, help="Private session file (or YMF_SESSION_FILE)")
    parser.add_argument(
        "--enable-claims", action="store_true", help="Allow explicit claim commands"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("import-session").add_argument("source", type=Path)
    commands.add_parser("login").add_argument(
        "--browser", choices=("auto", "chrome", "msedge", "chromium"), default="auto"
    )
    for name in ("auth-check", "diagnose-auth", "balance", "daily", "games", "claim-daily"):
        commands.add_parser(name)
    for name in ("levels", "claim-game"):
        commands.add_parser(name).add_argument("game_id")
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        with SessionStore(args.session or settings.session_file) as store:
            if args.command == "login":
                from .browser_auth import login

                store.save(login(browser=args.browser))
                result = {"session_saved": True, "api_verified": False}
            elif args.command == "import-session":
                store.import_file(args.source)
                result = {"imported": True}
            else:
                with make_session(store.load()) as session:
                    client = MarketClient(
                        session, allow_claims=args.enable_claims, interval=settings.interval_seconds
                    )
                    try:
                        match args.command:
                            case "auth-check":
                                client.check_auth()
                                result = {"authenticated": True}
                            case "diagnose-auth":
                                result = client.diagnose_auth()
                            case "balance":
                                result = {"coins": client.balance()}
                            case "daily":
                                result = {"available": client.daily_available()}
                            case "games":
                                result = client.games().model_dump(mode="json")
                            case "levels":
                                result = [
                                    level.model_dump(mode="json")
                                    | {"claimable": bool(level.claim_name)}
                                    for level in client.game_levels(args.game_id)
                                ]
                            case "claim-daily":
                                result = client.claim_daily().model_dump(mode="json")
                            case "claim-game":
                                result = client.claim_game(args.game_id).model_dump(mode="json")
                    finally:
                        # Includes Set-Cookie rotation/deletion even on handled API errors.
                        active_error = sys.exception()
                        try:
                            store.save(session.cookies.jar)
                        except (OSError, SessionError):
                            if active_error is not None:
                                print("Session persistence also failed", file=sys.stderr)
                            elif args.command.startswith("claim-"):
                                raise UncertainClaim(
                                    "Claim may have executed; session save failed; "
                                    "reconcile before retry"
                                ) from None
                            else:
                                raise SessionError("Session persistence failed") from None
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except MarketError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValidationError, ValueError):
        print(
            "Local configuration/session error; check paths, permissions and environment",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
