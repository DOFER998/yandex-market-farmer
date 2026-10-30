# Развёртывание и обслуживание

[← Документация](README.md)

## Автоматическая установка на VPS

Ubuntu 22.04/24.04/26.04 и Debian 12/13 с systemd, amd64/arm64. Выполните **от root**:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/DOFER998/yandex-market-farmer/main/scripts/install.sh)
```

Под обычным пользователем с sudo:

```bash
sudo bash -c 'bash <(curl -fsSL https://raw.githubusercontent.com/DOFER998/yandex-market-farmer/main/scripts/install.sh)'
```

Если нет curl: `sudo apt-get update && sudo apt-get install -y curl`.
Скрипт запрашивает токен бота скрытым вводом и ваш числовой Telegram ID. Остальное автоматизировано.

### Что устанавливается

1. Docker Engine и Compose из официального APT-репозитория, если Docker ещё не установлен.
2. curl, jq, OpenSSL и сертификаты; Git, make, Python, uv и Buildx не требуются.
3. **Только Compose и digest образа** из последнего стабильного GitHub Release.
4. Системный пользователь `ymf` без входа в shell, приватная конфигурация и каталог сессий.
5. Готовый образ приложения из GHCR и PostgreSQL из Docker Hub; миграции и запуск с healthcheck.

Приложение не собирается на VPS, репозиторий не клонируется. GHCR-образ проекта доступен
анонимно. Для форка его видимость тоже должна быть Public.

```text
/opt/yandex-market-farmer/
├── docker-compose.yml
├── .env                       # токен, ID, пароль БД, UID/GID и YMF_IMAGE с digest
├── .ymf-install               # служебная отметка версии
└── .secrets/accounts/          # сессии Яндекса
```

PostgreSQL хранит данные в Docker volume `ymf-prod_postgres_data`, а не в исходниках проекта.
Миграции, код, Python и зависимости уже находятся внутри образа.

Работающий Docker не переустанавливается. Конфликтующие пакеты и чужие контейнеры скрипт не удаляет.
Нужен Compose 2.20+. Скрипт не редактирует SSH/firewall вручную и не перезагружает VPS;
Docker управляет собственными сетевыми правилами и включается в автозапуск.
Установка Docker основана на инструкциях [Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
и [Debian](https://docs.docker.com/engine/install/debian/).

После запуска: **Начать → Добавить аккаунт → Подключение → Войти через QR → Сохранить вход**.
Проверьте логин и включите автосбор в настройках. Браузер на VPS не нужен.

В репозитории файл называется `docker-compose.prod.yml`, на сервере — `docker-compose.yml`.
Поэтому параметр `-f` не нужен. При повторной установке старое имя автоматически
заменяется на `docker-compose.yml` без изменения содержимого.

### Вывод установщика

Установщик показывает этапы и время выполнения; вывод APT и Docker сохраняет
в отдельный `/var/log/ymf/install-XXXXXXXX.log` с правами `0600`.
При ошибке этап отмечается `✗`, установка останавливается и показывает путь к логу.
Не публикуйте полный лог без проверки: сообщения сторонних программ могут содержать
служебные данные. Без TTY вывод обычный, без управляющих кодов; `NO_COLOR=1` отключает цвета.
Миграцию выполняет Compose: PostgreSQL → миграция → бот. Бот не запускается,
если миграция завершилась с ошибкой. Проверка готовности ограничена 180 секундами.

### Выбор версии и повторный запуск

По умолчанию выбирается последний стабильный GitHub Release, не `main` или `dev`.
Можно передать `--version v0.1.1`; для форка — `--repo OWNER/REPO`.
Если релиза или его файлов нет, установка останавливается без перехода к сборке из исходников.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/DOFER998/yandex-market-farmer/main/scripts/install.sh) --version v0.1.1
```

Повторный запуск сохраняет существующие Compose, `.env`, cookies, БД и выбранный digest.
Известная ошибка `tmpfs` в Compose v0.1.0 исправляется автоматически с резервной копией `.before-tmpfs-fix`; остальные настройки сохраняются.
Это восстановление установки, **не обновление версии**. Старую установку со сборкой из исходников
скрипт автоматически не перезаписывает: сначала сделайте резервную копию, перенесите Compose
и настройки образа вручную, сохранив `.env`, каталог сессий, UID/GID и имя Compose-проекта.

## Управление

Из `/opt/yandex-market-farmer`, от root или с sudo:

```bash
docker compose ps
docker compose logs -f bot
docker compose down           # данные сохраняются
```

## Обновление готового образа

Сначала сделайте резервную копию. На странице нужного [релиза](https://github.com/DOFER998/yandex-market-farmer/releases)
скачайте `docker-compose.prod.yml` и `image.ref`. Первый сохраните на сервере как `docker-compose.yml`; содержимое второго
становится значением `YMF_IMAGE` в существующем `.env`. Не заменяйте остальные настройки `.env`.
Compose и digest должны быть из **одного релиза**.

```bash
docker compose config --quiet
docker compose pull
docker compose stop bot
docker compose up -d --no-build --wait --wait-timeout 180
```

При ошибке миграции бот остаётся остановленным. Смена образа не откатывает схему БД:
для несовместимого отката нужна согласованная резервная копия. Не выполняйте downgrade вслепую.
Не запускайте локальный и VPS-бот с одним токеном одновременно.

## Разработка и сборка из исходников

Это отдельный сценарий для разработчика, не действие установщика:

```bash
make sync
make setup-env
make db-up
make start
```

Бот работает локально, Compose поднимает только БД на порту из `.env` (пример: `15432`).
В PyCharm запускайте модуль `src.main` из корня проекта, интерпретатор `.venv/bin/python`.
Для своей Docker-сборки используйте `make docker-run-prod` из checkout с подготовленным `.env`.
Команда использует отдельный `docker-compose.build.yml`; публичному установщику он не нужен.

## Резервные копии

Нужны три составляющие: PostgreSQL dump, `.env`, `.secrets/accounts`.
Для согласованного снимка остановите бот; БД оставьте запущенной.

```bash
umask 077
mkdir -p .secrets/backups
docker compose -f docker-compose.prod.yml stop bot
docker compose -f docker-compose.prod.yml exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > .secrets/backups/database.dump
tar -czf .secrets/backups/config-and-sessions.tar.gz .env .secrets/accounts
docker compose -f docker-compose.prod.yml up -d bot
```

Копируйте резервные копии на другое защищённое хранилище; архив содержит доступ к аккаунтам.
Для восстановления в **новую пустую БД**, пока бот остановлен:

```bash
docker compose -f docker-compose.prod.yml up -d --wait db
docker compose -f docker-compose.prod.yml exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' \
  < .secrets/backups/database.dump
```

Восстановите `.env` и каталог сессий из соответствующего архива, проверьте владельца/права,
затем примените миграции и запустите Compose по командам обновления ниже. Проверяйте восстановление на отдельной установке.
Не используйте `down -v`: он удаляет данные PostgreSQL.

## Поведение контейнеров

| Компонент | Назначение |
|---|---|
| `db` | PostgreSQL 17, постоянный volume, без опубликованных production-портов |
| `migrate` | Одноразовый `alembic upgrade head`; бот ждёт успешного завершения |
| `bot` | Non-root, read-only root filesystem, writable каталог сессий, long polling |

Healthcheck проверяет heartbeat и `SELECT 1`, а не доступность Telegram/Маркета.
`restart: unless-stopped` перезапускает завершившийся процесс; состояние `unhealthy` само по себе
не перезапускает контейнер. Остановка ждёт HTTP worker, поэтому grace period составляет 15 минут.
Singleton-lock действует только в пределах одной БД; не запускайте локальный и VPS-бот одновременно.

## Если что-то пошло не так

| Симптом | Действие |
|---|---|
| Порт занят локально | Измените `POSTGRES__PORT` в `.env` |
| Нет доступа к cookies | Проверьте UID/GID и права bind mount |
| Сессия истекла | Повторите QR-вход в разделе подключения |
| CAPTCHA / ограничение | Выполните ручную проверку в Яндексе; обход не предусмотрен |
| Неизвестный результат сбора | Проверьте историю и состояние аккаунта; не повторяйте начисление вслепую |
| `unhealthy` | Изучите `docker compose -f docker-compose.prod.yml logs -f bot`, доступность БД и состояние polling |

[Диагностические команды](research-tools.md) · [Конфигурация](configuration.md)
