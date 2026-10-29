import json

from scripts.market import main
from src.market.config import Settings
from src.market.transport import make_session


def test_cli_import_keeps_values_out_of_output(tmp_path, capsys):
    source = tmp_path / 'export.txt'
    source.write_text(
        '# Netscape HTTP Cookie File\n.yandex.ru\tTRUE\t/\tTRUE\t0\tSession_id\tsecret-value\n'
    )
    source.chmod(0o600)
    assert (
        main(
            ['--session', str(tmp_path / 'private' / 'cookies.txt'), 'import-session', str(source)]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {'imported': True}


def test_cli_errors_are_sanitized(tmp_path, capsys):
    assert main(['--session', str(tmp_path / 'private' / 'cookies.txt'), 'balance']) == 2
    assert 'Traceback' not in capsys.readouterr().err


def test_transport_has_pinned_identity_and_no_env_proxy():
    with make_session(None) as session:
        assert session.impersonate == 'chrome146'
        assert session.trust_env is False
        assert session.verify is True
        assert session.allow_redirects is False


def test_config_env(monkeypatch):
    monkeypatch.setenv('MARKET__INTERVAL_SECONDS', '5')
    assert Settings().interval_seconds == 5


def test_uncertain_claim_survives_save_failure(tmp_path, monkeypatch, capsys):
    from scripts import market as cli
    from src.market.errors import UncertainClaim

    path = tmp_path / 'private' / 'cookies.txt'
    path.parent.mkdir(mode=0o700)
    path.write_text(
        '# Netscape HTTP Cookie File\n.yandex.ru\tTRUE\t/\tTRUE\t0\tSession_id\tprivate\n'
    )
    path.chmod(0o600)

    def claim(_):
        raise UncertainClaim('Claim outcome unknown; do not retry')

    def save(*_):
        raise OSError('sensitive local path')

    monkeypatch.setattr(cli.MarketClient, 'claim_daily', claim)
    monkeypatch.setattr(cli.SessionStore, 'save', save)
    assert main(['--session', str(path), '--enable-claims', 'claim-daily']) == 2
    output = capsys.readouterr().err
    assert 'UncertainClaim' in output
    assert 'unknown' in output
    assert 'sensitive' not in output


def test_transport_explicitly_disables_libcurl_env_proxy():
    with make_session(None) as session:
        assert session.proxies.get('all') == ''


def test_diagnose_auth_cli_outputs_safe_report(tmp_path, monkeypatch, capsys):
    from scripts import market as cli

    path = tmp_path / 'private' / 'cookies.txt'
    path.parent.mkdir(mode=0o700)
    path.write_text(
        '# Netscape HTTP Cookie File\n.yandex.ru\tTRUE\t/\tTRUE\t0\tSession_id\tprivate\n'
    )
    path.chmod(0o600)
    monkeypatch.setattr(cli.MarketClient, 'diagnose_auth', lambda _: {'desktop_version_matches': 0})
    assert main(['--session', str(path), 'diagnose-auth']) == 0
    assert json.loads(capsys.readouterr().out) == {'desktop_version_matches': 0}


def test_mobile_transport_uses_supported_coherent_profile():
    with make_session(None, mobile=True) as session:
        assert session.impersonate == 'chrome131_android'
        assert session.trust_env is False
        assert session.proxies == {'all': ''}
        assert session.verify is True
        assert session.allow_redirects is False


def test_live_probe_mobile_flag_reaches_http_transport(tmp_path, monkeypatch, capsys):
    from scripts import market as cli
    from src.market import live

    path = tmp_path / 'private' / 'cookies.txt'
    path.parent.mkdir(mode=0o700)
    path.write_text(
        '# Netscape HTTP Cookie File\n.yandex.ru\tTRUE\t/\tTRUE\t0\tSession_id\tprivate\n'
    )
    path.chmod(0o600)
    observed = []

    def fetch(client):
        observed.append(client._session.impersonate)
        return '<html>mobile page</html>'

    original = live.save_live_probe

    def save(html, **kwargs):
        return original(html, root=tmp_path / 'probe', **kwargs)

    monkeypatch.setattr(cli.MarketClient, 'fetch_live_page', fetch)
    monkeypatch.setattr(live, 'save_live_probe', save)
    assert main(['--session', str(path), 'live-probe', '--mobile']) == 0
    assert observed == ['chrome131_android']
    result = json.loads(capsys.readouterr().out)
    from pathlib import Path

    record = json.loads(Path(result['report']).read_text())
    assert record['client_profile'] == 'chrome131_android'
    assert record['html'] == '<html>mobile page</html>'


def test_live_status_reads_mobile_page_and_prints_only_state(tmp_path, monkeypatch, capsys):
    from scripts import market as cli

    path = tmp_path / 'private' / 'cookies.txt'
    path.parent.mkdir(mode=0o700)
    path.write_text(
        '# Netscape HTTP Cookie File\n.yandex.ru\tTRUE\t/\tTRUE\t0\tSession_id\tprivate\n'
    )
    path.chmod(0o600)
    patch = {
        'widgets': {
            '@marketfront/LiveStream': {'/content/page/liveStream': {'streamId': 'secret'}}
        },
        'collections': {
            'liveStreamInfo': {
                'secret': {
                    'userState': {
                        'balanceState': {
                            'balance': '31',
                            'max': 250,
                            'prepaid': '1',
                            'coinSettings': {
                                'amount': 1,
                                'periodSeconds': 15,
                                'max': 250,
                                'enabled': True,
                            },
                        }
                    }
                }
            }
        },
    }

    def fetch(client):
        assert client._session.impersonate == 'chrome131_android'
        return '<noframes data-apiary="patch">' + json.dumps(patch) + '</noframes>'

    monkeypatch.setattr(cli.MarketClient, 'fetch_live_page', fetch)
    assert main(['--session', str(path), 'live-status']) == 0
    output = capsys.readouterr().out
    assert 'secret' not in output
    assert json.loads(output)['balance'] == 31
    assert json.loads(output)['remaining_to_reported_max'] == 219
