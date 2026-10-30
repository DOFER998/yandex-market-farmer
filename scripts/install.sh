#!/usr/bin/env bash
# Install release Compose + a prebuilt GHCR image; no source checkout or build.
# Never source .env as shell code. No credentials are accepted as command arguments.
set +x
set -Eeuo pipefail

DEFAULT_REPO=DOFER998/yandex-market-farmer

# Keep the UI on stdout and command output in a private per-run log.
INSTALL_LOG=''
STEP_PID=''
ACTIVE_STEP=''
COLOR='' GREEN='' RED='' RESET=''

init_ui() {
    if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR:-} ]]; then
        COLOR=$'\033[1;36m'; GREEN=$'\033[1;32m'; RED=$'\033[1;31m'; RESET=$'\033[0m'
    fi
    printf '\n%s  Yandex Market Farmer%s\n' "$COLOR" "$RESET"
    printf '  Установка на сервер · Docker + PostgreSQL\n\n'
}

log() { printf '  · %s\n' "$*"; }
die() { printf '\n%s  ✗ %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }

init_install_log() {
    install -d -m 0700 /var/log/ymf
    INSTALL_LOG=$(mktemp /var/log/ymf/install-XXXXXXXX.log)
    chmod 600 "$INSTALL_LOG"
}

run_step() {
    local title=$1 started=$SECONDS status
    shift
    ACTIVE_STEP=$title
    printf '  … %s\n' "$title"
    # Do not invoke this function inside an if/||: that disables Bash errexit.
    # Each command/function runs with errexit; wait is checked separately.
    (
        trap - ERR
        set -e
        "$@"
    ) >> "${INSTALL_LOG:-/dev/null}" 2>&1 &
    STEP_PID=$!
    while kill -0 "$STEP_PID" 2>/dev/null; do
        if [[ -t 1 ]]; then
            printf '\r\033[2K  … %s · %ss' "$title" "$((SECONDS - started))"
        fi
        sleep 1
    done
    if wait "$STEP_PID"; then status=0; else status=$?; fi
    STEP_PID=''
    [[ ! -t 1 ]] || printf '\r\033[2K'
    if ((status != 0)); then
        printf '%s  ✗ %s · %ss%s\n' "$RED" "$title" "$((SECONDS - started))" "$RESET" >&2
        return "$status"
    fi
    printf '%s  ✓ %s · %ss%s\n' "$GREEN" "$title" "$((SECONDS - started))" "$RESET"
    ACTIVE_STEP=''
}

installation_failed() {
    local status=$1
    trap - ERR
    if [[ "$ACTIVE_STEP" == 'Миграция БД и запуск бота' && -n "$INSTALL_LOG" ]]; then
        docker compose logs --no-color --tail=100 migrate bot >> "$INSTALL_LOG" 2>&1 || true
    fi
    printf '\n%s  Установка не завершена%s\n' "$RED" "$RESET" >&2
    [[ -z "$ACTIVE_STEP" ]] || printf '  Этап: %s\n' "$ACTIVE_STEP" >&2
    printf '  Данные сохранены.\n' >&2
    [[ -z "$INSTALL_LOG" ]] || printf '  Подробности: less %s\n' "$INSTALL_LOG" >&2
    exit "$status"
}

cleanup() {
    if [[ -n "$STEP_PID" ]]; then
        kill "$STEP_PID" 2>/dev/null || true
        wait "$STEP_PID" 2>/dev/null || true
    fi
    [[ -z "${INSTALL_STAGE:-}" ]] || rm -rf -- "$INSTALL_STAGE"
}

usage() {
    cat <<'HELP'
Установка Yandex Market Farmer на Linux VPS (root / sudo).

  bash <(curl -fsSL https://raw.githubusercontent.com/DOFER998/yandex-market-farmer/main/scripts/install.sh)
  bash install.sh --version v0.1.0

  --repo OWNER/REPO  Репозиторий (по умолчанию DOFER998/yandex-market-farmer)
  --version VERSION Релиз vX.Y.Z; по умолчанию latest (последний стабильный)
  --help            Показать справку без изменения системы

Ubuntu 22.04/24.04/26.04, Debian 12/13; amd64/arm64; systemd.
Каталог: /opt/yandex-market-farmer. Готовый образ скачивается из GHCR.
Токен вводится скрыто через терминал. Повторный запуск сохраняет .env и данные.
Установщик не обновляет существующую установку; обновление описано в docs/deployment.md.
HELP
}

supported_os() {
    case "$1:$2" in
        ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) return 0 ;;
        *) return 1 ;;
    esac
}

validate_repo() { [[ "$1" =~ ^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$ && "$1" != */.. ]]; }
valid_credentials() { [[ "$1" =~ ^[0-9]+:[A-Za-z0-9_-]+$ && "$2" =~ ^[1-9][0-9]{0,18}$ ]]; }

write_env() {
    local path=$1 token=$2 owner=$3 uid=$4 gid=$5 image=$6 password temporary
    [[ ! -L "$path" ]] || die '.env не должен быть символической ссылкой'
    if [[ -e "$path" ]]; then
        [[ -f "$path" ]] || die '.env не является обычным файлом'
        log 'Существующий .env сохранён без изменений.'
        return
    fi
    valid_credentials "$token" "$owner" || die 'Неверный формат токена или Telegram ID'
    password=$(openssl rand -hex 24)
    temporary=$(mktemp "${path}.XXXXXX")
    chmod 600 "$temporary"
    # printf receives credentials as data; neither shell nor dotenv can execute them.
    {
        printf 'BOT__TOKEN=%s\nBOT__OWNER_ID=%s\n' "$token" "$owner"
        printf 'POSTGRES__HOST=127.0.0.1\nPOSTGRES__PORT=15432\nPOSTGRES__USER=ymf\n'
        printf 'POSTGRES__PASSWORD=%s\nPOSTGRES__DATABASE=ymf\n' "$password"
        printf 'APP__DEBUG=false\nAPP__HEARTBEAT_FILE=.secrets/runtime/heartbeat\n'
        printf 'MARKET__SESSION_DIR=.secrets/accounts\nMARKET__INTERVAL_SECONDS=2\n'
        printf 'YMF_UID=%s\nYMF_GID=%s\n' "$uid" "$gid"
        [[ -z "$image" ]] || printf 'YMF_IMAGE=%s\n' "$image"
    } > "$temporary"
    mv -- "$temporary" "$path"
}

install_utilities() {
    apt-get update -qq
    apt-get install -y --no-install-recommends ca-certificates curl jq openssl
}

install_dependencies() {
    local package installed conflict=false
    run_step 'Проверка и установка утилит' install_utilities
    if command -v docker >/dev/null 2>&1; then
        log 'Docker уже установлен; сохраняю существующую установку.'
        docker compose version >/dev/null || die 'Нужен Docker Compose plugin. Установите его для своей версии Docker и повторите запуск.'
    else
        # Never remove a potentially active container runtime on an existing VPS.
        for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
            installed=$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)
            if [[ "$installed" == 'install ok installed' ]]; then
                printf '[ymf] Конфликтующий пакет: %s\n' "$package" >&2
                conflict=true
            fi
        done
        [[ "$conflict" == false ]] || die 'Сначала разрешите конфликты Docker вручную; пакеты автоматически не удаляются.'
        log 'Настраиваю официальный APT-репозиторий Docker.'
        install -m 0755 -d /etc/apt/keyrings
        curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
            "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/ymf-docker.asc
        chmod 644 /etc/apt/keyrings/ymf-docker.asc
        # Refuse duplicate/custom repository configuration instead of overwriting it.
        if grep -rqs 'download.docker.com' /etc/apt/sources.list /etc/apt/sources.list.d; then
            [[ -f /etc/apt/sources.list.d/ymf-docker.sources ]] || die 'Репозиторий Docker уже настроен другим способом. Завершите установку Docker вручную.'
        fi
        cat > /etc/apt/sources.list.d/ymf-docker.sources <<SOURCES
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: $VERSION_CODENAME
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/ymf-docker.asc
SOURCES
        run_step 'Обновление списка пакетов Docker' apt-get update -qq
        run_step 'Установка Docker' apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-compose-plugin
    fi
    local compose_version major minor
    compose_version=$(docker compose version --short)
    compose_version=${compose_version#v}
    IFS=. read -r major minor _ <<< "$compose_version"
    [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]] || die 'Не удалось проверить версию Docker Compose'
    ((major > 2 || (major == 2 && minor >= 20))) || die 'Нужен Docker Compose 2.20 или новее'
    run_step 'Запуск Docker' systemctl enable --now docker
    docker info >/dev/null 2>&1 || die 'Docker daemon недоступен'
}

guard_existing_stack() {
    local target=$1 directories directory
    directories=$(docker ps -a --filter label=com.docker.compose.project=ymf-prod \
        --format '{{.Label "com.docker.compose.project.working_dir"}}')
    while IFS= read -r directory; do
        [[ -z "$directory" || "$directory" == "$target" ]] || die 'Обнаружен ymf-prod из другого каталога. Остановитесь и перенесите существующую установку вручную.'
    done <<< "$directories"
    if [[ ! -f "$target/.env" ]] && docker volume inspect ymf-prod_postgres_data >/dev/null 2>&1; then
        die 'Найдена существующая БД без .env этой установки. Восстановите исходный .env; новый пароль не создаётся.'
    fi
}

download_file() {
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        --retry 3 --connect-timeout 15 --max-time 120 "$1" -o "$2"
}

validate_image() {
    local expected
    expected="ghcr.io/$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')@sha256:"
    [[ "$2" == "$expected"* && "${2#"$expected"}" =~ ^[a-f0-9]{64}$ ]]
}

prepare_runtime() {
    local repo=$1 version=$2 target=$3 base
    [[ ! -L "$target" && ! -L "$target/.ymf-install" ]] || die 'Symlink в каталоге установки запрещён'
    if [[ -f "$target/.ymf-install" ]]; then
        [[ $(head -n 1 "$target/.ymf-install") == image-runtime-v1 ]] || die 'Найдена установка из исходников. Перенос выполняется вручную с сохранением .env и сессий.'
        IMAGE=$(sed -n '3p' "$target/.ymf-install")
        validate_image "$repo" "$IMAGE" || die 'Повреждена запись версии установки'
        log 'Существующие Compose, .env и данные сохраняются; обновление версии не выполняется.'
        return
    fi
    if [[ -d "$target" && -n "$(ls -A "$target")" ]]; then
        die "$target уже содержит файлы другой установки. Автоматическое перезаписывание запрещено."
    fi
    INSTALL_STAGE=$(mktemp -d "${target}.stage.XXXXXX")
    if [[ "$version" == latest ]]; then
        download_file "https://api.github.com/repos/$repo/releases/latest" "$INSTALL_STAGE/release.json"
        version=$(jq -er '.tag_name | strings' "$INSTALL_STAGE/release.json")
        rm "$INSTALL_STAGE/release.json"
    fi
    [[ "$version" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || die 'Нужен стабильный релиз vX.Y.Z'
    base="https://github.com/$repo/releases/download/$version"
    log "Скачиваю Compose и ссылку на готовый образ релиза $version."
    download_file "$base/docker-compose.prod.yml" "$INSTALL_STAGE/docker-compose.yml"
    download_file "$base/image.ref" "$INSTALL_STAGE/image.ref"
    IMAGE=$(cat "$INSTALL_STAGE/image.ref")
    validate_image "$repo" "$IMAGE" || die 'Релиз должен содержать digest образа выбранного репозитория'
    rm "$INSTALL_STAGE/image.ref"
    printf 'image-runtime-v1\n%s\n%s\n' "$version" "$IMAGE" > "$INSTALL_STAGE/.ymf-install"
    chmod 755 "$INSTALL_STAGE"
    chmod 644 "$INSTALL_STAGE/docker-compose.yml"
    chmod 600 "$INSTALL_STAGE/.ymf-install"
    [[ ! -d "$target" ]] || rmdir -- "$target"
    mv -- "$INSTALL_STAGE" "$target"
    INSTALL_STAGE=''
}

normalize_compose_name() {
    if [[ ! -e docker-compose.yml && ! -L docker-compose.yml ]]; then
        [[ -f docker-compose.prod.yml && ! -L docker-compose.prod.yml ]] || die 'Compose установки не найден'
        mv -- docker-compose.prod.yml docker-compose.yml
    fi
    [[ -f docker-compose.yml && ! -L docker-compose.yml ]] || die 'Compose должен быть обычным файлом'
}

repair_runtime_compose() {
    local path=$1 temporary
    [[ -f "$path" && ! -L "$path" ]] || die 'Compose должен быть обычным файлом'
    if grep -Fxq '  tmpfs: [/tmp:rw,noexec,nosuid,size=64m]' "$path"; then
        [[ ! -e "$path.before-tmpfs-fix" && ! -L "$path.before-tmpfs-fix" ]] || die 'Резервная копия Compose уже существует; проверьте её перед повторной правкой'
        cp -p -- "$path" "$path.before-tmpfs-fix"
        temporary=$(mktemp "${path}.XXXXXX")
        sed 's|^  tmpfs: \[/tmp:rw,noexec,nosuid,size=64m\]$|  tmpfs: ["/tmp:rw,noexec,nosuid,size=64m"]|' "$path" > "$temporary"
        chmod 644 "$temporary"
        mv -- "$temporary" "$path"
        log 'Исправлена запись tmpfs из v0.1.0; исходный Compose сохранён в .before-tmpfs-fix.'
    fi
}

deploy_runtime() {
    run_step 'Проверка конфигурации' docker compose config --quiet
    run_step 'Загрузка образов' docker compose pull
    run_step 'Остановка предыдущего бота' docker compose stop bot
    # Compose starts migrate after the healthy DB, then bot only after migration success.
    run_step 'Миграция БД и запуск бота' docker compose up -d --no-build --wait --wait-timeout 180
}

main() {
    local repo=$DEFAULT_REPO version=latest target=/opt/yandex-market-farmer
    local token='' owner='' uid gid
    while (($#)); do
        case "$1" in
            --help|-h) usage; return ;;
            --repo|--version)
                (($# >= 2)) || die "Нет значения для $1"
                case "$1" in
                    --repo) repo=$2 ;;
                    --version) version=$2 ;;
                esac
                shift 2 ;;
            *) die "Неизвестный параметр: $1" ;;
        esac
    done
    validate_repo "$repo" || die 'Укажите GitHub-репозиторий в формате OWNER/REPO'
    [[ "$version" == latest || "$version" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || die 'Версия должна быть latest или vX.Y.Z'
    [[ $(uname -s) == Linux ]] || die 'Установщик предназначен для Linux VPS'
    ((EUID == 0)) || die 'Запустите установщик через sudo bash или от root'
    [[ -f /etc/os-release ]] || die 'Нет /etc/os-release'
    # shellcheck disable=SC1091
    source /etc/os-release
    supported_os "$ID" "$VERSION_ID" || die "Система $ID $VERSION_ID не поддерживается"
    [[ ${VERSION_CODENAME:-} =~ ^[a-z]+$ ]] || die 'Не определено кодовое имя дистрибутива'
    case "$(uname -m)" in x86_64|aarch64) ;; *) die 'Нужна архитектура amd64 или arm64' ;; esac
    [[ -d /run/systemd/system ]] || die 'Нужен VPS с systemd, не контейнер/chroot'
    [[ -z ${DOCKER_HOST:-} && -z ${DOCKER_CONTEXT:-} ]] || die 'Удалённый Docker context не поддерживается'
    export DOCKER_HOST=unix:///var/run/docker.sock
    export DEBIAN_FRONTEND=noninteractive
    umask 077
    # Fixed root-owned directory and lock; no user-controlled installation paths.
    [[ ! -L /opt && ! -L "$target" ]] || die 'Symlink в пути установки не поддерживается'
    exec 9>/run/lock/ymf-install.lock
    flock -n 9 || die 'Другой установщик уже работает'
    init_ui
    init_install_log
    install_dependencies
    guard_existing_stack "$target"
    prepare_runtime "$repo" "$version" "$target"
    cd -- "$target"
    [[ ! -L .env && ! -L .secrets && ! -L .secrets/accounts ]] || die 'Symlink для секретов запрещён'
    if [[ ! -e .env ]]; then
        [[ -r /dev/tty ]] || die 'Нужен интерактивный терминал для первого ввода токена'
        printf 'Токен Telegram-бота (ввод скрыт): ' > /dev/tty
        IFS= read -r -s token < /dev/tty
        printf '\nВаш числовой Telegram user ID: ' > /dev/tty
        IFS= read -r owner < /dev/tty
        valid_credentials "$token" "$owner" || die 'Неверный формат токена или Telegram ID'
        if ! id ymf >/dev/null 2>&1; then
            useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin ymf
        fi
        uid=$(id -u ymf); gid=$(id -g ymf)
        ((uid != 0)) || die 'Пользователь ymf не должен быть root'
        install -d -m 0700 -o "$uid" -g "$gid" .secrets .secrets/accounts
        write_env .env "$token" "$owner" "$uid" "$gid" "$IMAGE"
        unset token
    else
        [[ -f .env ]] || die '.env не является обычным файлом'
        [[ -d .secrets/accounts ]] || die 'Каталог сессий отсутствует; восстановите его перед запуском'
        log 'Сохраняю .env, права каталога сессий и данные существующей установки.'
    fi
    chmod 600 .env
    grep -Eq '^YMF_IMAGE=.+$' .env || die 'В .env отсутствует YMF_IMAGE; сборка на VPS не поддерживается'
    normalize_compose_name
    repair_runtime_compose docker-compose.yml
    deploy_runtime
    printf '\n%s  ✓ Бот запущен%s\n' "$GREEN" "$RESET"
    printf '  Откройте бота → Добавить аккаунт → Войти через QR\n\n'
    printf '  Каталог  %s\n' "$target"
    printf '  Логи     cd %s && docker compose logs -f bot\n' "$target"
    printf '  Установка  %s\n\n' "$INSTALL_LOG"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    INSTALL_STAGE=''
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'installation_failed $?' ERR
    main "$@"
fi
