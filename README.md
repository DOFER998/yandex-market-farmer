# Yandex Market Farmer

Python 3.13: исследовательское ядро и CLI для чтения и осторожного сбора доступных наград.
**Реальный API аккаунтом ещё не проверен. Автономный VPS-сервис пока не реализован.**

[Исследование API](docs/api-research.md) описывает источники, транспорт и нерешённые вопросы.
Клиент не содержит вращения рулетки, покупок, активации промокодов, отправки результатов игр или событий прогресса.

## Установка

```bash
cd /Users/c0mrade/PycharmProjects/yandex-market-farmer
uv sync
uv run ymf --help
uv run pytest -q
uv run ruff check .
```

В PyCharm выберите `.venv/bin/python` этого проекта. Для Linux достаточно Python 3.13 и uv.

В среде создания проекта сеть терминала недоступна. `.venv` установлено из уже имевшегося локального кеша;
универсальный registry `uv.lock` пока не удалось получить. На машине с доступом к PyPI выполните `uv sync`,
проверьте зависимости и сохраните созданный `uv.lock` в Git до развёртывания.
Текущие проверки можно повторить без сети: `uv run --no-sync pytest`, `uv run --no-sync ruff check .`, `uv run --no-sync ymf --help`.

## Безопасный локальный вход

Один раз запустите на своём компьютере:

```bash
uv sync
uv run ymf login
uv run ymf auth-check
uv run ymf balance
```

Откроется отдельное окно установленного Chrome/Edge. Войдите в Яндекс сами (пароль/QR/2FA),
дождитесь загрузки авторизованной страницы Маркета (если есть проверка, пройдите её вручную). Программа автоматически заберёт cookies, включая HttpOnly,
сохранит их локально и закроет окно. Ничего копировать и передавать в чат не нужно.
Ожидание входа — пять минут; закрытие окна или Ctrl+C отменяют вход без замены старой сессии.
Захват сессии ещё не подтверждает работоспособность API: это отдельно проверяет `auth-check`.

Если Chrome/Edge не установлен:

```bash
uv run playwright install chromium
uv run ymf login --browser chromium
```

Для входа нужен рабочий стол; на VPS без GUI вход выполняется локально, приватный файл сессии
можно затем перенести на свой сервер по SSH. CAPTCHA и 2FA проходите самостоятельно.
Браузер не читает ваш обычный профиль и не сохраняет пароль.

Ручной Netscape-импорт остаётся резервным вариантом:

```bash
chmod 600 /absolute/path/market-cookies.txt
uv run ymf import-session /absolute/path/market-cookies.txt
```

Чтение состояния:

```bash
uv run ymf daily
uv run ymf games
uv run ymf levels GAME_ID
```

По умолчанию jar хранится в `.secrets/cookies.txt` (0600, каталог 0700). Для другого пути:
`YMF_SESSION_FILE=/private/path/cookies.txt` или `ymf --session /private/path/cookies.txt balance`.
`YMF_INTERVAL_SECONDS=2` регулирует интервал (не менее секунды). `.env` автоматически не читается;
передавайте переменные окружения явно. Не используйте одну копию jar одновременно в разных каталогах.

Cookie jar обновляется из Set-Cookie и атомарно сохраняется. Он не продлевает авторизацию сам по себе:
при AuthRequired выполните `uv run ymf login`. AccessDenied/CAPTCHA требуют ручного решения, обхода нет.

## Явные операции начисления

После проверки чтения с аккаунтом:

```bash
uv run ymf --enable-claims claim-daily
uv run ymf balance
uv run ymf daily
uv run ymf --enable-claims claim-game GAME_ID
```

`claim-daily` сначала читает доступность. `submitted_unverified` означает только приём ответа HTTP/JSON:
структура подтверждения начисления неизвестна. Сравните баланс и доступность с состоянием до запроса.
`claim-game` заново получает hub/status и отправляет только уже достигнутые уровни с серверным токеном.
Вывод разделяет `claimed` (success от API) и `unconfirmed`. Токены исключены из вывода.

При UncertainClaim не повторяйте начисление вслепую: вручную сверяйте состояние аккаунта.
Клиент не повторяет мутации, даже при timeout/5xx; 429 сообщает время ожидания. CLI завершается кодом 2
при обработанной ошибке, 0 — при выполнении команды; 0 для daily не является доказательством зачисления.

## Библиотечное использование

`MarketClient` независим от Telegram, SQLModel и scheduler. `SessionStore` держит файловую блокировку,
`make_session` создаёт transport. Используйте их как в `cli.py`: блокировка на всю операцию,
сохранение `session.cookies.jar` в finally, закрытие session. Один экземпляр — один аккаунт.

## Проверки и дальнейшее развитие

Тесты используют синтетические Go-совместимые ответы. Проверяются схемы, приватность cookies,
выбор заработанных наград, запрет скрытых мутаций, ошибки, блокировки и CLI.
Это не live integration и не тест начисления.

После проверки ядра аккаунтом: FarmingService с журналом и идемпотентностью/сверкой неоднозначных операций;
PostgreSQL + SQLModel (через SQLAlchemy) + Alembic; блокировки на аккаунт; aiogram 3 с allowlist user ID;
APScheduler; Docker Compose с healthcheck, секретами, рестартами и persistent storage.
Эти зависимости пока не добавлены.
