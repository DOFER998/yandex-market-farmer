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


def test_runtime_install_downloads_only_compose_and_pinned_image(tmp_path):
    result = shell(
        'download_file() { case "$1" in '
        '*docker-compose.prod.yml) printf "name: ymf-prod\\n" > "$2" ;; '
        '*image.ref) printf "ghcr.io/dofer998/yandex-market-farmer@sha256:%064d\\n" 0 > "$2" ;; '
        '*) return 1 ;; esac; }; '
        'prepare_runtime "$DEFAULT_REPO" v0.1.0 "$1"; '
        'printf "saved-token" > "$1/.env"; '
        'download_file() { return 1; }; '
        'prepare_runtime "$DEFAULT_REPO" latest "$1"',
        str(tmp_path / 'runtime'),
    )
    assert result.returncode == 0, result.stderr
    target = tmp_path / 'runtime'
    assert {p.name for p in target.iterdir()} == {'.env', '.ymf-install', 'docker-compose.yml'}
    assert (target / '.env').read_text() == 'saved-token'
    assert 'v0.1.0' in (target / '.ymf-install').read_text()


def test_refuses_to_replace_unrelated_directory(tmp_path):
    target = tmp_path / 'existing'
    target.mkdir()
    (target / 'keep').write_text('important')
    result = shell('prepare_runtime "$DEFAULT_REPO" v0.1.0 "$1"', str(target))
    assert result.returncode != 0
    assert (target / 'keep').read_text() == 'important'


def test_image_reference_must_be_digest_of_selected_repository(tmp_path):
    result = shell(
        'download_file() { printf "ghcr.io/other/app:latest\\n" > "$2"; }; '
        'prepare_runtime "$DEFAULT_REPO" v0.1.0 "$1"',
        str(tmp_path / 'runtime'),
    )
    assert result.returncode != 0
    assert not (tmp_path / 'runtime').exists()


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


def test_runtime_deploy_pulls_before_stopping_and_never_builds(tmp_path):
    result = shell(
        'export CAPTURE="$1"; docker() { printf "%s\\n" "$*" >> "$CAPTURE"; }; deploy_runtime',
        str(tmp_path / 'calls'),
    )
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / 'calls').read_text().splitlines()
    assert calls == [
        'compose config --quiet',
        'compose pull',
        'compose stop bot',
        'compose up -d --no-build --wait --wait-timeout 180',
    ]


def test_legacy_source_install_is_not_silently_replaced(tmp_path):
    target = tmp_path / 'old'
    target.mkdir()
    (target / '.ymf-install').touch()
    (target / '.env').write_text('keep')
    result = shell('prepare_runtime "$DEFAULT_REPO" latest "$1"', str(target))
    assert result.returncode != 0
    assert (target / '.env').read_text() == 'keep'


def test_repairs_v010_tmpfs_without_changing_other_configuration(tmp_path):
    compose = tmp_path / 'docker-compose.prod.yml'
    original = 'x-app:\n  tmpfs: [/tmp:rw,noexec,nosuid,size=64m]\n  image: custom\n'
    compose.write_text(original)
    result = shell('repair_runtime_compose "$1"; repair_runtime_compose "$1"', str(compose))
    assert result.returncode == 0, result.stderr
    assert compose.read_text() == original.replace(
        '[/tmp:rw,noexec,nosuid,size=64m]', '["/tmp:rw,noexec,nosuid,size=64m"]'
    )
    assert compose.with_suffix('.yml.before-tmpfs-fix').read_text() == original


def test_runtime_repair_leaves_custom_tmpfs_untouched(tmp_path):
    compose = tmp_path / 'docker-compose.prod.yml'
    original = 'x-app:\n  tmpfs: ["/tmp:size=128m"]\n'
    compose.write_text(original)
    result = shell('repair_runtime_compose "$1"', str(compose))
    assert result.returncode == 0, result.stderr
    assert compose.read_text() == original
    assert not compose.with_suffix('.yml.before-tmpfs-fix').exists()


def test_step_prints_one_result_without_saving_output(tmp_path):
    result = shell(
        'cd "$1"; work() { printf "verbose output"; }; run_step "Загрузка образов" work',
        str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('Загрузка образов') == 1
    assert '✓ Загрузка образов' in result.stdout
    assert 'verbose output' not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []
    assert '\x1b' not in result.stdout


def test_failed_step_shows_error_and_stops_without_saving_output(tmp_path):
    result = shell(
        'cd "$1"; work() { printf "migration failed\\n"; return 42; '
        'printf "must not execute"; }; '
        'run_step "Миграция" work; printf "must not continue"',
        str(tmp_path),
    )
    assert result.returncode == 42
    assert '✗ Миграция' in result.stderr
    assert 'migration failed' in result.stderr
    assert '✓' not in result.stdout
    assert 'must not' not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


def test_inner_failure_cannot_be_hidden_by_later_success():
    result = shell('work() { false; printf "must not execute"; }; run_step "Проверка" work')
    assert result.returncode != 0
    assert 'must not' not in result.stdout + result.stderr


def test_existing_compose_is_renamed_without_changing_content(tmp_path):
    old = tmp_path / 'docker-compose.prod.yml'
    old.write_text('name: ymf-prod\n# custom settings\n')
    result = shell('cd "$1"; normalize_compose_name; normalize_compose_name', str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'docker-compose.yml').read_text() == 'name: ymf-prod\n# custom settings\n'
    assert not old.exists()


def test_existing_default_compose_is_preserved(tmp_path):
    (tmp_path / 'docker-compose.yml').write_text('keep')
    (tmp_path / 'docker-compose.prod.yml').write_text('legacy')
    result = shell('cd "$1"; normalize_compose_name', str(tmp_path))
    assert result.returncode == 0
    assert (tmp_path / 'docker-compose.yml').read_text() == 'keep'
