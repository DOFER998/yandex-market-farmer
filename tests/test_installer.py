"""Exercise installer guards without root, network or host mutations."""

import os
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / 'scripts/install.sh'


def shell(body, *args):
    assert INSTALLER.is_file()
    return subprocess.run(
        ['bash', '-c', 'source "$1"; shift; ' + body, 'test', str(INSTALLER), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ('distribution', 'version', 'ok'),
    [
        ('ubuntu', '24.04', True),
        ('debian', '13', True),
        ('debian', '11', False),
        ('arch', 'rolling', False),
    ],
)
def test_supported_systems(distribution, version, ok):
    result = shell('supported_os "$1" "$2"', distribution, version)
    assert (result.returncode == 0) == ok


def test_config_is_private_and_never_replaces_existing_credentials(tmp_path):
    result = shell(
        'write_env "$1" 123456:test_token 123 995 995 ghcr.io/example/app:0.1.0; '
        'write_env "$1" 999999:other 456 994 994 ghcr.io/example/app:0.2.0',
        str(tmp_path / '.env'),
    )
    assert result.returncode == 0, result.stderr
    content = (tmp_path / '.env').read_text()
    assert '123456:test_token' in content
    assert '999999:other' not in content
    assert 'YMF_UID=995' in content
    assert 'POSTGRES__PASSWORD=' in content
    assert (tmp_path / '.env').stat().st_mode & 0o777 == 0o600
    assert 'test_token' not in result.stdout + result.stderr


def test_config_rejects_symlink(tmp_path):
    victim = tmp_path / 'victim'
    victim.write_text('unchanged')
    link = tmp_path / '.env'
    link.symlink_to(victim)
    result = shell('write_env "$1" 123456:test 123 995 995 image:1', str(link))
    assert result.returncode != 0
    assert victim.read_text() == 'unchanged'


@pytest.mark.parametrize('token', ['bad', '123:hello\nINJECT=value', "123:hello'world"])
def test_credentials_cannot_inject_dotenv(token):
    result = shell('valid_credentials "$1" 123', token)
    assert result.returncode != 0


def test_help_does_not_need_root_or_docker():
    result = subprocess.run(
        ['bash', str(INSTALLER), '--help'],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, 'DOCKER_HOST': 'unix:///nonexistent'},
    )
    assert result.returncode == 0
    assert '--repo' in result.stdout


def test_invalid_repository_is_rejected():
    result = shell('validate_repo "$1"', 'owner/repo;touch /tmp/pwn')
    assert result.returncode != 0


def test_copy_does_not_include_local_secrets_and_rerun_preserves_install(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for name in ['src', 'scripts', 'migrations', 'docs']:
        (source / name).mkdir()
        (source / name / 'example').write_text('original')
    for name in [
        'LICENSE',
        'THIRD_PARTY_NOTICES.md',
        'pyproject.toml',
        'uv.lock',
        'README.md',
        'Makefile',
        'Dockerfile',
        '.dockerignore',
        '.env.example',
        'alembic.ini',
        'docker-compose.prod.yml',
        'docker-compose.build.yml',
    ]:
        (source / name).write_text('example')
    (source / '.env').write_text('private-token')
    (source / '.secrets').mkdir()
    (source / '.secrets' / 'cookies').write_text('secret-session')
    target = tmp_path / 'target'
    result = shell('prepare_source "$1" "$2"', str(source), str(target))
    assert result.returncode == 0, result.stderr
    assert not (target / '.env').exists()
    assert not (target / '.secrets').exists()
    (target / '.env').write_text('saved-token')
    (source / 'src' / 'example').write_text('new')
    result = shell('prepare_source "$1" "$2"', str(source), str(target))
    assert result.returncode == 0, result.stderr
    assert (target / '.env').read_text() == 'saved-token'
    assert (target / 'src' / 'example').read_text() == 'original'


def test_refuses_to_replace_unrelated_directory(tmp_path):
    target = tmp_path / 'existing'
    target.mkdir()
    (target / 'keep').write_text('important')
    result = shell('prepare_source "$1" "$2"', str(tmp_path), str(target))
    assert result.returncode != 0
    assert (target / 'keep').read_text() == 'important'


def test_existing_database_requires_original_config(tmp_path):
    result = shell(
        'docker() { if [[ "$1" == ps ]]; then return 0; fi; return 0; }; guard_existing_stack "$1"',
        str(tmp_path),
    )
    assert result.returncode != 0
    assert not (tmp_path / '.env').exists()


def test_existing_stack_in_other_directory_is_not_touched(tmp_path):
    result = shell(
        'docker() { printf "/opt/another-install\\n"; }; guard_existing_stack "$1"',
        str(tmp_path),
    )
    assert result.returncode != 0


def test_streamed_installer_has_builtin_repository():
    result = subprocess.run(
        ['bash', '-c', 'bash <(cat "$1") --help', 'test', str(INSTALLER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert 'DOFER998/yandex-market-farmer' in result.stdout


def test_fetch_source_downloads_default_repository_without_local_checkout(tmp_path):
    result = shell(
        'git() { printf "%s\\n" "$@" > "$CAPTURE"; }; '
        'export CAPTURE="$1"; fetch_source "$DEFAULT_REPO" main; '
        '[[ "$SOURCE_DIR" == "$INSTALL_TEMP/source" ]]; rm -rf "$INSTALL_TEMP"',
        str(tmp_path / 'git-args'),
    )
    assert result.returncode == 0, result.stderr
    args = (tmp_path / 'git-args').read_text().splitlines()
    assert 'https://github.com/DOFER998/yandex-market-farmer.git' in args
    assert args[args.index('--branch') + 1] == 'main'
