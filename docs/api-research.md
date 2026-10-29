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

На первоначальном этапе интеграция аккаунтом не проверялась. Все API-ответы автоматических тестов синтетические и не доказывают начисление монет. Результаты последующих реальных запусков пользователя зафиксированы в конце документа.

## Автоматический локальный захват сессии

Команда login использует [Playwright BrowserContext.cookies](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-cookies)
для HttpOnly cookies. Видимый Chrome/Edge либо Chromium открывается с изолированным непостоянным контекстом;
пароль, QR и 2FA вводит пользователь. Ожидается HTTPS-возврат на market.yandex.ru и Session_id,
применимый к странице Маркета; активная страница проверки не принимается. Partitioned cookies не преобразуются в обычные; session expiry сохраняется.
Наличие cookie не объявляется успешной API-авторизацией: для этого есть auth-check.
Браузер закрывается при завершении/ошибке, старый jar заменяется только после успешного захвата.
Реальный вход здесь не выполнен; автоматизация протестирована на подставных ответах браузера.

Детектор challenge не реагирует на строки smartcaptcha в JavaScript/ссылках. Он распознаёт
активные формы checkcaptcha/showcaptcha, challenge URL и известные заголовки страницы проверки.
Неизвестные варианты всё равно отклоняются, если отсутствует валидный HTML/API-контекст.
Список признаков не претендует на полную поддержку всех вариантов антибот-защиты.

Исправление browser gate: Go waitForLogin ждёт Session_id, а getProfileInfo получает SK/glue
отдельным HTTP-запросом. Требовать эти поля в живом DOM браузера было неподтверждённым
отклонением от референса. Оно удалено: браузерный захват и API-проверка снова разделены.

Исправление парсинга версии: appVersionRegex в Go ищет первое значение -version,
соответствующее `.+-desktop\..+`, а не первое произвольное -version. Python-порт
ошибочно проверял только первое поле; теперь выбор соответствует Go. Если контекст всё ещё
не найден, diagnose-auth возвращает только фиксированные флаги/счётчики, без сырых данных.
Никакие значения glue/version по умолчанию не выдумываются.

### Данные реального запуска пользователя

Пользователь выполнил diagnose-auth: sk_present=true, login_present=true, version_matches=1,
desktop_version_matches=0, front_glue_present=true, desktop_marker_mentioned=false.
Следовательно, текущая страница содержит единственное -version без исторического desktop-суффикса.
Теперь это значение передаётся в x-market-app-version без изменения. При наличии desktop-кандидата
сохраняется предпочтение референсного формата, но неоднозначные кандидаты отклоняются.
Пустые значения и управляющие символы запрещены. Это адаптация по фактической диагностике;
приём нового значения впоследствии подтверждён пользователем: balance вернул 7.

## Эфиры: исследование 2026-10-29

### Подтверждено официальной справкой

[Прямые эфиры на Маркете](https://www.yandex.ru/support/market/ru/live-streams):
эфиры доступны в приложении; 15 секунд просмотра дают 1 монету, дневной максимум — 250,
сброс ограничения — в 00:00 по Москве. Монеты используются на странице колеса.
Вращение колеса в нашем проекте по-прежнему исключено.

Расчёт по указанной скорости: 250 × 15 = 3750 секунд = 62 минуты 30 секунд
засчитанного просмотра. Это арифметика, не гарантия начисления при фоновом воспроизведении.

[Правила участия в трансляциях, редакция 14.04.2026](https://yandex.ru/legal/market_live/ru/)
указывают веб-адрес `/live` (п. 1.2), допускают просмотр завершённых эфиров (п. 3.3).
Наличие записей не доказывает начисление монет за них. Раздел 4 описывает отдельные акции
с промокодами; его нельзя использовать как спецификацию API монет за просмотр.

### Что технически ещё не подтверждено

- Работает ли начисление только в native-приложении или также в веб-плеере.
- Засчитываются ли записи, фон, приглушённый звук, несколько эфиров и повторный просмотр.
- Endpoint списка эфиров, остатка дневного лимита, прогресса и получения наград.
- Начисляются ли монеты автоматически во время просмотра или нужен отдельный claim.
- Какие серверные токены, идентификаторы сессии просмотра и подтверждения требуются.
- Совместима ли имеющаяся web-cookie с native API. Наличие рабочей web-сессии этого не доказывает.

Нельзя делать вывод, что 15 секунд соответствуют периоду конкретного heartbeat-запроса:
официальная справка описывает начисление, а не транспортный протокол.
Нельзя отправлять синтетическое время просмотра/события для создания прогресса.
Поддержка эфиров пока не добавлена в клиент; вымышленных resolver-имён нет.

### Полученные и недоступные свидетельства

Публичный `/live` не удалось прочитать: web fetch вернул 400, запрос из терминала — DNSError.
Поиск открытых исходников не дал подтверждённого API вознаграждений эфиров.
Cookies аккаунта не передавались веб-поиску. Перехват трафика приложения не выполнялся.
Запрошена публичная ссылка через «Поделиться» из приложения для проверки конкретного эфира.

Следующий шаг: открыть фактическую ссылку, проверить наличие web-плеера и счётчика.
Если веб-начисление существует, наблюдать обычные запросы при настоящем ручном просмотре,
сопоставив баланс до/после. Секреты и HAR не публиковать. Только после определения роли запросов
решать, есть ли отдельная безопасная операция чтения/получения уже заработанных монет.

### Обновление проверки основного ядра

Пользователь подтвердил реальным запуском: auth-check=true, баланс=7, daily.available=false,
claim-daily.state=unavailable (бонус уже получен ранее), games возвращает 11 игр и три задания
с нулевым прогрессом, levels ballz возвращает бронзу/серебро/золото с isAchieved=false.
Это подтверждает чтение перечисленных контрактов и ветку недоступного daily,
но не успешное начисление наград, не эфиры и не автономную работу на VPS.
