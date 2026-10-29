import json

from yandex_market_farmer.cli import main
from yandex_market_farmer.config import Settings
from yandex_market_farmer.transport import make_session


def test_cli_import_keeps_values_out_of_output(tmp_path, capsys):
    source = tmp_path / "export.txt"
    source.write_text(
        "# Netscape HTTP Cookie File\n.yandex.ru\tTRUE\t/\tTRUE\t0\tSession_id\tsecret-value\n"
    )
    source.chmod(0o600)
    assert (
        main(
            ["--session", str(tmp_path / "private" / "cookies.txt"), "import-session", str(source)]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"imported": True}


def test_cli_errors_are_sanitized(tmp_path, capsys):
    assert main(["--session", str(tmp_path / "private" / "cookies.txt"), "balance"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_transport_has_pinned_identity_and_no_env_proxy():
    with make_session(None) as session:
        assert session.impersonate == "chrome146"
        assert session.trust_env is False
        assert session.verify is True
        assert session.allow_redirects is False


def test_config_env(monkeypatch):
    monkeypatch.setenv("YMF_INTERVAL_SECONDS", "5")
    assert Settings().interval_seconds == 5


def test_uncertain_claim_survives_save_failure(tmp_path, monkeypatch, capsys):
    from yandex_market_farmer import cli
    from yandex_market_farmer.errors import UncertainClaim

    path = tmp_path / "private" / "cookies.txt"
    path.parent.mkdir(mode=0o700)
    path.write_text(
        "# Netscape HTTP Cookie File\n.yandex.ru\tTRUE\t/\tTRUE\t0\tSession_id\tprivate\n"
    )
    path.chmod(0o600)

    def claim(_):
        raise UncertainClaim("Claim outcome unknown; do not retry")

    def save(*_):
        raise OSError("sensitive local path")

    monkeypatch.setattr(cli.MarketClient, "claim_daily", claim)
    monkeypatch.setattr(cli.SessionStore, "save", save)
    assert main(["--session", str(path), "--enable-claims", "claim-daily"]) == 2
    output = capsys.readouterr().err
    assert "UncertainClaim" in output
    assert "unknown" in output
    assert "sensitive" not in output


def test_transport_explicitly_disables_libcurl_env_proxy():
    with make_session(None) as session:
        assert session.proxies.get("all") == ""


def test_diagnose_auth_cli_outputs_safe_report(tmp_path, monkeypatch, capsys):
    from yandex_market_farmer import cli

    path = tmp_path / "private" / "cookies.txt"
    path.parent.mkdir(mode=0o700)
    path.write_text(
        "# Netscape HTTP Cookie File\n.yandex.ru\tTRUE\t/\tTRUE\t0\tSession_id\tprivate\n"
    )
    path.chmod(0o600)
    monkeypatch.setattr(cli.MarketClient, "diagnose_auth", lambda _: {"desktop_version_matches": 0})
    assert main(["--session", str(path), "diagnose-auth"]) == 0
    assert json.loads(capsys.readouterr().out) == {"desktop_version_matches": 0}
