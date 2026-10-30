#!/usr/bin/env bash
# Run the real production mounts and migrations with disposable data and no API calls.
set -Eeuo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
smoke_dir=$(mktemp -d)
smoke_project="ymf-smoke-$(date +%s)-$$"
compose() { docker compose --project-name "$smoke_project" --project-directory "$smoke_dir" -f "$smoke_dir/docker-compose.prod.yml" "$@"; }
cleanup() {
    compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf -- "$smoke_dir"
}
trap cleanup EXIT
cp "$repo_dir/docker-compose.prod.yml" "$smoke_dir/"
mkdir -p "$smoke_dir/.secrets/accounts"
# Disposable path only; production permissions are set by the installer.
chmod 755 "$smoke_dir" "$smoke_dir/.secrets"
chmod 777 "$smoke_dir/.secrets/accounts"
cat > "$smoke_dir/.env" <<ENV
BOT__TOKEN=123456:smoke_test_token
BOT__OWNER_ID=123
POSTGRES__USER=ymf
POSTGRES__PASSWORD=smoke-only-password
POSTGRES__DATABASE=ymf
YMF_UID=1000
YMF_GID=1000
YMF_IMAGE=${1:-ymf:ci}
ENV
# Exercise the same dependency chain as the installer, without Telegram requests.
cat > "$smoke_dir/health-only.yml" <<'YAML'
services:
  bot:
    command:
      - python
      - -c
      - |
        import time
        from pathlib import Path
        stamp = Path('/tmp/ymf/heartbeat')
        stamp.parent.mkdir(parents=True, exist_ok=True)
        while True:
            stamp.write_text(str(time.time()))
            time.sleep(1)
YAML
docker compose --project-name "$smoke_project" --project-directory "$smoke_dir" \
    -f "$smoke_dir/docker-compose.prod.yml" -f "$smoke_dir/health-only.yml" \
    up -d --no-build --wait --wait-timeout 180
compose run --rm -T --no-deps bot python - <<'PY'
import asyncio
import errno
import os
import time
from pathlib import Path
from scripts.health import check
from src.common.config import Environment
assert os.getuid() == 1000
settings = Environment()
probe = settings.market.session_dir / 'smoke-session'
probe.write_text('test')
probe.unlink()
stamp = settings.app.heartbeat_file
stamp.parent.mkdir(parents=True, exist_ok=True)
stamp.write_text(str(time.time()))
asyncio.run(check())
try:
    Path('/app/read-only-probe').write_text('test')
except OSError as exc:
    assert exc.errno in (errno.EROFS, errno.EACCES)
else:
    raise AssertionError('Application filesystem is writable')
print('Production Compose: migrations, session storage, heartbeat and database health passed')
PY
