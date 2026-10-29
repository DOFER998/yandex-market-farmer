# Исследование API Яндекс Маркета

Дата: 2026-10-29. Источник — публичная ветка `main` [xob0t/market-coin-farmer](https://github.com/xob0t/market-coin-farmer).
Это reverse engineering стороннего клиента, **не официальная спецификация и не live-проверка Маркета**.

## Ограничение актуальности

DNS GitHub в терминале недоступен; использован веб-инструмент. Он вернул страницы GitHub с датой обхода «сегодня», но часть raw-файлов — из кеша (до двух месяцев). Например, GitHub показывает 635 строк `yaapi_games.go`, raw — 594. Ключевые игровые вызовы сверены с GitHub-страницей. SHA текущего HEAD получить не удалось. Ссылки ниже привязаны к `main`, следовательно могут сдвигаться. Перед production необходимо клонировать репозиторий, закрепить SHA и повторить сравнение. Утверждения ниже подтверждены прочитанными исходниками, но не гарантированно самым последним коммитом.

## Первоначальный вход

[`browserauthservice.go`, Login / waitForLogin / cookiesToNetscape, L59–254](https://github.com/xob0t/market-coin-farmer/blob/main/browserauthservice.go#L59-L254): открывается обычный Chromium с временным профилем; пользователь самостоятельно входит на Passport с retpath Маркета. CDP Storage.GetCookies читает в том числе HttpOnly cookies. Ожидание ограничено пятью минутами, опрос — 1,5 секунды; сигналом служит непустой Session_id. Это ещё не проверка авторизации API. Затем cookies сериализуются в Netscape. Для session cookies референс искусственно задаёт годовой expiry; Python сохраняет исходную session-семантику. Имя может извлекаться из yandex_login. При закрытии окна/таймауте вход прекращается. Python не автоматизирует ввод пароля: `uv run ymf login` открывает отдельный браузер, ждёт возврата на Маркет и автоматически сохраняет cookies. Ручной Netscape-импорт — резервный вариант.

## Хранение

[`configservice.go`, Account / AddAccountToConfig / SaveGlobalConfig, L26–112](https://github.com/xob0t/market-coin-farmer/blob/main/configservice.go#L26-L112): cookies кодируются base64 и сохраняются в config.yaml вместе с именем и proxy. Base64 не шифрует. SK, версия приложения, glue и время авторизации исключены из постоянной конфигурации. В коде есть логирование аккаунта и запись с os.ModePerm — эту практику не переносим.

В Python: отдельная директория 0700, файл 0600, проверка владельца и symlink, атомарная замена, flock на всю CLI-операцию. В Git и stdout секреты не попадают. Это защита файловыми правами, не шифрование диска. Удалённые/обновлённые сервером cookies сохраняются из jar после запроса, включая ошибочные ответы. Для нескольких копий одного аккаунта в разных файлах блокировка не общая.

## Контекст и транспорт референса

[`yaapi_transport.go`, ensureAuth / getProfileInfo / createAPIRequest / getClient, L20–265](https://github.com/xob0t/market-coin-farmer/blob/main/yaapi_transport.go#L20-L265): GET страницы бонусов извлекает sk, login, -version (desktop), marketFrontGlue, lang. Кеш контекста — час, ключ клиента — proxy+cookies. Jar загружается из base64 Netscape; явного сохранения изменившегося jar обратно в config не найдено. OAuth refresh token не обнаружен.

POST отправляет JSON, Origin/Referer, sk, x-market-app-version, x-market-front-glue, x-market-apphost-target, x-market-page-id=market:fortune-wheel, x-requested-with=XMLHttpRequest, x-retpath-y и browser headers. Target обычно PROMOLOYALTY, bulk — WEB. Timeout 30 секунд. Go использует tls-client/fhttp и Chrome_146, при UA Chrome/151: полного совпадения идентичности нет. Причина выбора fingerprint не доказана A/B-тестом; вероятная мотивация — совместимость с браузерным TLS/HTTP-профилем.

403 обозначается как IP-block, прочие не-200 и пустые ответы — ошибки; автоматического восстановления после 401/403 не найдено. Python не считает любой 403 доказательством блокировки IP: останавливается, как и при CAPTCHA. Контекст обновляется через час/явную auth-check; истёкшая сессия требует повторного локального входа. Ошибочные claim-запросы не переотправляются.

## Баланс и ежедневный бонус

[`yaapisservice.go`, константы и GetRewardsJson / ClaimDailyCoins, L15–171](https://github.com/xob0t/market-coin-farmer/blob/main/yaapisservice.go#L15-L171).

База: `https://market.yandex.ru/api/web/market.front.mfPromoLoyalty.MfPromoLoyalty/`.
Оболочка POST: `{"path":"/kolesoprizov?track=menu","params":{}}`.

| Суффикс | Назначение / проверенное поле |
|---|---|
| resolveFortuneWheelMainScreenData | Баланс: result.wheelInfo.userInfo.coins |
| resolveFortuneWheelDailyRewardsScreenData | Доступность: result.dailySignInInfo.rewardAvailable |
| resolveFortuneWheelDailySignInReceiveReward | Claim только при rewardAvailable=true |
| resolveFortuneWheelPrizesScreenData | Просмотр призов; params содержит wheel_ids; в Python не нужен |

Общий путь страницы не означает вращение: чтение баланса и daily независимы от spin. Вращение полностью исключено. Ответ daily claim референс возвращает сырым, тип успешного начисления не описан. Поэтому CLI сообщает submitted_unverified, а не «монеты начислены»; необходимо отдельно сравнить баланс и daily-состояние. Неизвестные/неполные схемы не превращаются в нулевой баланс.

## Игры

[`yaapi_games.go`, структуры L18–75, ClaimGameRewards L94–220, processGamesHubGame / bulk L508–635](https://github.com/xob0t/market-coin-farmer/blob/main/yaapi_games.go#L18-L635).

- AppHost `resolveGamesHubMiniMainScreenDataV2`: gamesSection.games, featuredGame.button.action.openGameAction, missionsSection.missionGroups[].missions. Игры содержат gameId/gameToken; задания — missionId/status/rewardAmount/progress(accumulatedValue,goalValue).
- Legacy POST `/api/resolve/?r=<URL-encoded resolver>`, body `{path,params:[{...}]}`.
- Status resolver: `src/resolvers/gamesHub/resolveGamesHubGameStatusV2:resolveGamesHubGameStatusV2`, params gameToken. Ответ results[0].data.gameStatus.levels: level/requiredScore/rewardPoints/achievedToken/isAchieved.
- Bulk resolver: `src/resolvers/cashbackLevels/fortune/resolveFetchFortuneGameRewardBulk:resolveFortuneGameRewardBulk`, params gameId и levels. Имена уровней получаются удалением RESULT_LEVEL_ и lowercase. Успех: results[0].data.result сопоставляет achievedToken со строкой success.

Референс также отправляет MissionClick, GameEvent(count=недостающий прогресс) и ProcessGameResultV2(score=максимум). Это противоречит задаче: такие операции отсутствуют в Python. Не переносится и слепой bulk market_rush без списка уровней. Python заново читает hub/status, допускает только isAchieved=true плюс непустой achievedToken; неуспешные подтверждения возвращает отдельно. Это консервативное дополнительное условие; семантику повторного claim предстоит проверить аккаунтом. Прогресс задания сам по себе не разрешает начисление.

## Выбор HTTP-библиотеки

| Вариант | Оценка для этого проекта |
|---|---|
| [httpx](https://www.python-httpx.org/advanced/ssl/) | Удобные sync/async API и SSLContext; готового Chrome-профиля в описанном API нет |
| [aiohttp](https://docs.aiohttp.org/en/stable/client_advanced.html) | Асинхронность, jar, SSLContext; потребовалась бы отдельная работа для браузерной идентичности |
| [curl_cffi](https://curl-cffi.readthedocs.io/en/latest/api.html) | Session, CookieJar, browser impersonation, TLS validation; выбран |

Решение: curl_cffi 0.16.3, фиксированный chrome146, соответствующий семейству Go-профиля. UA/client hints задаёт сам профиль, не копируем несогласованный Chrome/151. [Документация профилей](https://curl-cffi.readthedocs.io/en/latest/impersonate/_index.html). Выбор — инженерная гипотеза совместимости, не обещание обхода защиты. Нет ротации IP/профилей, автоматических CAPTCHA-решателей или отключения TLS-проверки. Redirect запрещён: SK не должен уходить на иной origin. Синхронный клиент достаточен для последовательного ядра; будущий aiogram может вызывать его через рабочий поток.

## Нерешённые вопросы / gate перед VPS

1. Закрепить действительный HEAD референса и сверить исходники без кеша.
2. С настоящим аккаунтом проверить HTML-контекст, headers/TLS, срок жизни cookies и Set-Cookie.
3. Сопоставить live-ответы со строгими моделями (особенно optional-поля hub и levels).
4. Проверить daily claim до/после, повторный запуск и смену дня/часового пояса.
5. Для реально сыгранной игры подтвердить meaning isAchieved/achievedToken, повторные награды и частичный bulk. Не создавать прогресс ради теста.
6. Уточнить ошибки sk/session, квоты и Retry-After. Значение 2 секунды между запросами — локальный осторожный default, не известный лимит Яндекса.
7. Ошибка сети после mutation имеет неопределённый результат; нужен durable operation journal перед автономным режимом.

Интеграционные запросы к Маркету в этой работе не выполнялись: сессия не предоставлена. Все API-ответы тестов синтетические и не доказывают начисление монет.

## Автоматический локальный захват сессии

Команда login использует [Playwright BrowserContext.cookies](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-cookies)
для HttpOnly cookies. Видимый Chrome/Edge либо Chromium открывается с изолированным непостоянным контекстом;
пароль, QR и 2FA вводит пользователь. Ожидается HTTPS-возврат на market.yandex.ru и Session_id,
применимый к странице Маркета. Partitioned cookies не преобразуются в обычные; session expiry сохраняется.
Наличие cookie не объявляется успешной API-авторизацией: для этого есть auth-check.
Браузер закрывается при завершении/ошибке, старый jar заменяется только после успешного захвата.
Реальный вход здесь не выполнен; автоматизация протестирована на подставных ответах браузера.
