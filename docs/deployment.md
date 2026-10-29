# Развёртывание и обслуживание

[← Документация](README.md)

## Локальная разработка

```bash
make sync
make setup-env
make db-up
make start
```

Compose запускает только БД, бот работает из `.venv`. PyCharm: модуль `src.main`, рабочая
директория — корень проекта, интерпретатор `.venv/bin/python`. `make start` применяет миграции.
Порт БД задаётся в `.env`; новый пример использует `15432`, существующий конфиг не меняется.

## Автоматическая установка на VPS

Поддерживаются Ubuntu 22.04/24.04/26.04 и Debian 12/13 с systemd, архитектуры amd64/arm64.
Из уже скачанного проекта:

```bash
sudo bash scripts/install.sh
```

Для установки непосредственно из публичного GitHub-репозитория скачайте скрипт из выбранного
релиза, просмотрите его и запустите. Используйте **существующий** тег вместо `v0.1.0`. До первого релиза можно явно указать `main` вместо тега в обоих местах.

```bash
curl --fail --location --output install.sh \
  https://raw.githubusercontent.com/DOFER998/yandex-market-farmer/v0.1.0/scripts/install.sh
less install.sh
sudo bash install.sh --repo DOFER998/yandex-market-farmer --ref v0.1.0
```

Если на чистом сервере нет `curl`, сначала установите его: `sudo apt-get update && sudo apt-get install -y curl`.
Сам скрипт также устанавливает необходимые утилиты. Не запускайте неизвестные копии установщика от root.

### Что делает установщик

1. Проверяет Linux, дистрибутив, архитектуру, systemd и отсутствие второго установщика.
2. Устанавливает curl, Git, make, OpenSSL и сертификаты; на чистом VPS — Docker Engine,
   Buildx и Compose из официального APT-репозитория Docker.
3. Размещает исходники в `/opt/yandex-market-farmer`, исключая локальные `.env`, cookies и отчёты.
4. Создаёт системного пользователя `ymf` без входа в shell. Его UID/GID используются контейнером.
5. Запрашивает токен бота **скрытым вводом** и числовой Telegram ID. Генерирует пароль БД,
   сохраняет `.env` с правами `600`, готовит приватный каталог сессий.
6. Собирает образ локально, применяет миграции, запускает Compose и ждёт healthcheck.

Работающий Docker не переустанавливается. Если существующей установке не хватает Compose/Buildx
или обнаружены конфликтующие пакеты, скрипт остановится с объяснением: чужие контейнеры и runtimes
не удаляются. Скрипт не редактирует SSH/firewall вручную и не перезагружает сервер;
Docker управляет собственными сетевыми правилами. Docker включается в автозапуск,
сервисы проекта используют restart policy. Требуется Compose 2.20 или новее.
Установка Docker основана на официальных инструкциях для
[Ubuntu](https://docs.docker.com/engine/install/ubuntu/) и
[Debian](https://docs.docker.com/engine/install/debian/).

> [!NOTE]
> Наш код скачивается с **GitHub** и по умолчанию собирается на сервере.
> Наш образ не публикуется на Docker Hub. Базовые Python/PostgreSQL скачиваются из Docker Hub,
> uv — из GHCR; серверу нужен доступ к этим реестрам и Python package index.

Опционально можно использовать опубликованный образ GitHub Container Registry:

```bash
sudo bash install.sh --repo DOFER998/yandex-market-farmer --ref v0.1.0 \
  --image ghcr.io/dofer998/yandex-market-farmer:0.1.0
```

Тег исходников и версия образа должны соответствовать друг другу. Для private GHCR предварительно
выполните `sudo docker login ghcr.io`; автоматическое скачивание private GitHub-репозитория
этот установщик не поддерживает — используйте доверенный локальный checkout.

### Повторный запуск и управление

Установщик сохраняет существующие `.env`, исходники, cookies и БД. Он может завершить прерванную
установку, но **не является автоматическим обновлятором** и не переносит произвольный каталог проекта.
Если `/opt/yandex-market-farmer` уже занят другой установкой, он откажется перезаписывать его.

```bash
cd /opt/yandex-market-farmer
sudo make docker-status
sudo make docker-logs
sudo make docker-stop-prod
```

Для обновления установки из скрипта: сделайте резервную копию, остановите бот и замените исходники
на выбранный релиз (включая скрытый `.dockerignore`), сохранив `.env` и `.secrets`. Затем выполните
`sudo make docker-run-prod`. Если используете GHCR, обновите Compose-файлы из релиза и `YMF_IMAGE`,
затем `sudo make docker-deploy`. Не копируйте `.env.example` поверх рабочего `.env`.

После установки откройте бота, добавьте аккаунт и выполните QR-вход. Браузер на VPS не нужен.

## Linux VPS: ручная установка

Нужны Docker Engine, Compose plugin, Git и make. Python/uv и браузер на хосте не нужны.
Склонируйте свой репозиторий и переключитесь на выбранный тег релиза. Работайте от отдельного
непривилегированного пользователя. Команды далее выполняются из корня проекта.

### 1. Подготовка

```bash
cp .env.example .env
chmod 600 .env
mkdir -p .secrets/accounts
chmod 700 .secrets .secrets/accounts
id -u
id -g
```

Заполните `.env` в редакторе:

- `BOT__TOKEN` и `BOT__OWNER_ID` — ваш бот и ваш ID.
- `POSTGRES__PASSWORD` — случайный пароль, например результат `openssl rand -hex 24`.
- `YMF_UID` и `YMF_GID` — результаты команд выше; каталог сессий должен принадлежать им.
- `YMF_IMAGE=ghcr.io/dofer998/yandex-market-farmer:0.1.0` — фактически опубликованный релиз; имя репозитория в нижнем регистре.

> [!IMPORTANT]
> Образ появится после первого успешного Publish workflow в GitHub-репозитории проекта.
> Для публичного скачивания переключите видимость GHCR package на Public.
> Для private package сначала выполните `docker login ghcr.io` с доступом на чтение.

### 2. Запуск

```bash
make docker-deploy
make docker-status
make docker-logs
```

Команда загружает образы, останавливает старый бот, применяет миграции и запускает стек.
Если миграция завершится ошибкой, бот останется остановленным: исправьте причину по логам мигратора.
При первом запуске команда миграции также поднимет БД через Compose dependencies.

В Telegram: **Начать → Добавить аккаунт → Подключение → Войти через QR**.
Подтвердите вход в Яндексе, сверьте логин и нажмите **Сохранить вход**. Затем включите автосбор.
Копировать cookies с компьютера не требуется.

<details>
<summary><b>Сборка из исходников, если опубликованного образа ещё нет</b></summary>

```bash
make docker-run-prod
```

Используется `docker-compose.build.yml` поверх production Compose, образ `yandex-market-farmer:local`.
Для последующего обновления из исходников сначала остановите старый бот:

```bash
docker compose -f docker-compose.prod.yml stop bot
make docker-run-prod
```

</details>

### 3. Обновление

Сначала сделайте резервную копию. Переключитесь на новый тег исходников (Compose и миграции
также версионируются), задайте новый `YMF_IMAGE` в `.env`, выполните `make docker-deploy`.
Для строгой фиксации используйте `ghcr.io/dofer998/yandex-market-farmer@sha256:...` из GitHub Release.
Плавающий `dev` предназначен для тестов; автоматического обновления сервера нет.

```bash
make docker-status
make docker-logs
make docker-stop-prod            # остановить стек, сохранить данные
```

Понижение версии образа не откатывает схему БД. Если старый код несовместим с новой схемой,
восстанавливайте согласованную резервную копию; не запускайте `alembic downgrade` вслепую.

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
затем `make docker-deploy`. Проверяйте восстановление на отдельной установке.
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
| `unhealthy` | Изучите `make docker-logs`, доступность БД и состояние polling |

[Диагностические команды](research-tools.md) · [Конфигурация](configuration.md)
