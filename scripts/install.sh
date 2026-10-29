#!/usr/bin/env bash
# Run from a trusted checkout, or download this file and pass --repo OWNER/REPO.
# Never source .env as shell code. No credentials are accepted as command arguments.
set +x
set -Eeuo pipefail

log() { printf '\n[ymf] %s\n' "$*"; }
die() { printf '\n[ymf] Ошибка: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'HELP'
Установка Yandex Market Farmer на Linux VPS (root / sudo).

  sudo bash scripts/install.sh
  sudo bash install.sh --repo OWNER/REPO --ref v0.1.0

  --repo OWNER/REPO  Скачать публичный GitHub-репозиторий (иначе текущий checkout)
  --ref REF         Тег/ветка GitHub; по умолчанию main
  --image IMAGE     Использовать готовый GHCR-образ вместо сборки из исходников
  --help            Показать справку без изменения системы

Ubuntu 22.04/24.04/26.04, Debian 12/13; amd64/arm64; systemd.
Каталог: /opt/yandex-market-farmer. По умолчанию сборка на VPS.
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

install_dependencies() {
    local package installed conflict=false
    log 'Устанавливаю необходимые утилиты.'
    apt-get update -qq
    apt-get install -y --no-install-recommends ca-certificates curl git make openssl
    if command -v docker >/dev/null 2>&1; then
        log 'Docker уже установлен; сохраняю существующую установку.'
        docker compose version >/dev/null || die 'Нужен Docker Compose plugin. Установите его для своей версии Docker и повторите запуск.'
        docker buildx version >/dev/null || die 'Нужен Docker Buildx plugin для сборки. Установите его и повторите запуск.'
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
        apt-get update -qq
        apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    local compose_version major minor
    compose_version=$(docker compose version --short)
    compose_version=${compose_version#v}
    IFS=. read -r major minor _ <<< "$compose_version"
    [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]] || die 'Не удалось проверить версию Docker Compose'
    ((major > 2 || (major == 2 && minor >= 20))) || die 'Нужен Docker Compose 2.20 или новее'
    systemctl enable --now docker
    docker info >/dev/null || die 'Docker daemon недоступен'
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

prepare_source() {
    local source=$1 target=$2 item
    [[ ! -L "$target" ]] || die 'Каталог установки не должен быть symlink'
    if [[ -e "$target/.ymf-install" ]]; then
        log 'Использую существующие файлы установки; обновление кода не выполняется.'
        return
    fi
    if [[ -d "$target" && -n "$(ls -A "$target")" ]]; then
        die "$target уже содержит файлы другой установки. Автоматическое перезаписывание запрещено."
    fi
    [[ -f "$source/pyproject.toml" && -f "$source/docker-compose.prod.yml" ]] || die 'Не найден исходный проект'
    INSTALL_STAGE=$(mktemp -d "${target}.stage.XXXXXX")
    # An allowlist excludes .env, .git, .secrets, reports and local virtual environments.
    for item in src scripts migrations docs pyproject.toml uv.lock README.md Makefile Dockerfile \
        .dockerignore .env.example LICENSE THIRD_PARTY_NOTICES.md alembic.ini docker-compose.prod.yml docker-compose.build.yml; do
        [[ -e "$source/$item" && ! -L "$source/$item" ]] || die "Отсутствует обычный файл/каталог: $item"
        if [[ -d "$source/$item" ]] && [[ -n "$(find "$source/$item" -type l -print -quit)" ]]; then
            die "Ссылки внутри $item не поддерживаются установщиком"
        fi
        cp -R -- "$source/$item" "$INSTALL_STAGE/$item"
    done
    find "$INSTALL_STAGE" -type d -exec chmod 755 {} +
    find "$INSTALL_STAGE" -type f -exec chmod 644 {} +
    touch "$INSTALL_STAGE/.ymf-install"
    [[ ! -d "$target" ]] || rmdir -- "$target"
    mv -- "$INSTALL_STAGE" "$target"
    INSTALL_STAGE=''
}

main() {
    local repo='' ref=main image='' target=/opt/yandex-market-farmer source token='' owner='' uid gid
    while (($#)); do
        case "$1" in
            --help|-h) usage; return ;;
            --repo|--ref|--image)
                (($# >= 2)) || die "Нет значения для $1"
                case "$1" in
                    --repo) repo=$2 ;;
                    --ref) ref=$2 ;;
                    --image) image=$2 ;;
                esac
                shift 2 ;;
            *) die "Неизвестный параметр: $1" ;;
        esac
    done
    [[ -z "$repo" ]] || validate_repo "$repo" || die 'Укажите GitHub-репозиторий в формате OWNER/REPO'
    [[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$ref" != *..* ]] || die 'Недопустимый Git ref'
    [[ -z "$image" || "$image" =~ ^ghcr\.io/[a-z0-9._/-]+(:[a-zA-Z0-9._-]+|@sha256:[a-f0-9]{64})$ ]] || die 'Нужен GHCR image с явным тегом или digest'
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
    install_dependencies
    guard_existing_stack "$target"
    source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
    if [[ ! -e "$target/.ymf-install" && -n "$repo" ]]; then
        INSTALL_TEMP=$(mktemp -d)
        log "Получаю GitHub $repo, ref $ref."
        git -c core.hooksPath=/dev/null clone --depth 1 --branch "$ref" --single-branch \
            "https://github.com/$repo.git" "$INSTALL_TEMP/source"
        source=$INSTALL_TEMP/source
    fi
    prepare_source "$source" "$target"
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
        write_env .env "$token" "$owner" "$uid" "$gid" "$image"
        unset token
    else
        [[ -f .env ]] || die '.env не является обычным файлом'
        [[ -z "$image" ]] || die 'Для существующей установки задавайте YMF_IMAGE в .env; файл не перезаписан'
        [[ -d .secrets/accounts ]] || die 'Каталог сессий отсутствует; восстановите его перед запуском'
        log 'Сохраняю .env, права каталога сессий и данные существующей установки.'
    fi
    chmod 600 .env
    docker compose -f docker-compose.prod.yml config --quiet
    # Read only the presence of the image setting; never evaluate .env in bash.
    if grep -Eq '^YMF_IMAGE=.+$' .env; then
        make docker-deploy
    else
        make docker-run-prod
    fi
    docker compose -f docker-compose.prod.yml ps
    log 'Установка завершена. Откройте бота → Добавить аккаунт → Подключение → Войти через QR.'
    printf 'Каталог: %s\nЛоги: cd %s && sudo make docker-logs\n' "$target" "$target"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    INSTALL_TEMP=''
    INSTALL_STAGE=''
    trap '[[ -z "$INSTALL_TEMP" ]] || rm -rf -- "$INSTALL_TEMP"; [[ -z "$INSTALL_STAGE" ]] || rm -rf -- "$INSTALL_STAGE"' EXIT
    trap 'printf "\n[ymf] Установка прервана на строке %s. Данные не удалены; исправьте ошибку и повторите запуск.\n" "$LINENO" >&2' ERR
    main "$@"
fi
