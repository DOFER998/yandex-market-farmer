from datetime import timedelta
from html import escape

from src.common.time import now
from src.database.models import Account, FarmRun, Operation
from src.services.accounts import AccountHistory
from src.services.emoji import EmojiCatalog
from src.services.ports import RunStatistics
from src.views.errors import error_text
from src.views.fragments import GAP, heading, quote, row, section
from src.views.time import telegram_time

AUTH = {
    'ready': 'Подключён',
    'unknown': 'Вход не проверен',
    'expired': 'Нужен вход',
    'blocked': 'Яндекс запросил проверку',
}
STATES = {
    'success': 'Готово',
    'unavailable': 'Ежедневная награда пока недоступна',
    'no_rewards': 'Доступных игровых наград нет',
    'partial': 'Не все результаты подтверждены',
    'failed': 'Операция не завершена',
    'uncertain': 'Нужна проверка начисления',
    'interrupted': 'Операция прервана',
    'blocked': 'Нужна проверка состояния',
    'skipped': 'Сбор не требовался',
    'running': 'Выполняется',
}


def account_name(account: Account) -> str:
    return escape(account.display_name or account.id)


def home_text(accounts: list[Account], icons: EmojiCatalog | None = None) -> str:
    if not accounts:
        return GAP.join(
            [
                heading('Аккаунты Маркета', 'home', icons),
                quote(['Пока нет подключённых аккаунтов.']),
                '<i>Нажми «Добавить аккаунт», чтобы начать.</i>',
            ]
        )
    return GAP.join(
        [
            section(
                'Аккаунты Маркета',
                [
                    row('Аккаунтов', str(len(accounts))),
                    row('Автосбор включён', str(sum(a.auto_collect for a in accounts))),
                ],
                icon='home',
                icons=icons,
            ),
            '<i>Выбери аккаунт для просмотра баланса и сбора наград.</i>',
        ]
    )


def run_rows(run: FarmRun) -> list[str]:
    if run.balance_after is not None:
        rows = [row('Баланс', f'{run.balance_after} монет')]
    elif run.balance_before is not None:
        rows = [row('Последний известный баланс', f'{run.balance_before} монет')]
    else:
        rows = [row('Баланс', 'пока недоступен')]
    if run.balance_before is not None and run.balance_after is not None and run.trigger != 'read':
        rows.append(row('Изменение', f'{run.balance_after - run.balance_before:+d}'))
    return rows


def operation_rows(run: FarmRun, operations: list[Operation]) -> list[str]:
    rows = []
    labels = {
        'auth': 'Не удалось проверить вход',
        'balance_before': 'Не удалось прочитать баланс перед сбором',
        'daily': 'Не удалось проверить или получить ежедневную награду',
        'daily_status': 'Не удалось проверить состояние после начисления',
        'games': 'Не удалось прочитать список игровых наград',
        'balance_after': 'Не удалось обновить баланс после сбора',
    }
    for operation in operations:
        if operation.run_id != run.id:
            continue
        if operation.kind == 'daily' and operation.state == 'unavailable':
            rows.append('Ежедневная награда сейчас недоступна. Запрос начисления не отправлялся.')
        elif operation.state in ('failed', 'uncertain', 'unconfirmed'):
            rows.append(escape(labels.get(operation.kind, 'Не удалось проверить игровую награду')))
    return list(dict.fromkeys(rows))


def format_run(
    run: FarmRun, icons: EmojiCatalog | None = None, *, operations: list[Operation] | None = None
) -> str:
    blocks = [
        section(
            STATES.get(run.state, 'Состояние уточняется'),
            [
                row('Тип награды', 'игровая' if run.trigger == 'games' else 'ежедневная')
                if run.trigger != 'read'
                else row('Действие', 'проверка баланса'),
                *run_rows(run),
                *operation_rows(run, operations or []),
            ],
            icon='balance' if run.trigger == 'read' else 'collect',
            icons=icons,
        )
    ]
    if run.state == 'unavailable' and not operations:
        blocks.append('Запрос начисления не отправлялся. Дождись следующей награды.')
    if run.error_code:
        blocks.append(
            section('Что делать', [escape(error_text(run.error_code))], icon='connect', icons=icons)
        )
    elif run.state == 'partial':
        blocks.append(
            '<i>Часть результатов не подтверждена. Проверь историю перед повторным сбором.</i>'
        )
    return GAP.join(blocks)


def status_text(
    account: Account, latest: FarmRun | None = None, icons: EmojiCatalog | None = None
) -> str:
    balance = 'нет актуальных данных'
    updated = ''
    if latest:
        coins = latest.balance_after if latest.balance_after is not None else latest.balance_before
        if coins is not None:
            balance = f'{coins} монет'
            updated = f'<i>Баланс обновлён:</i> {telegram_time(latest.started_at, style="r")}'
    blocks = [
        section(
            account.display_name or account.id,
            [
                row('Баланс', balance),
                row('Вход', AUTH.get(account.auth_state, 'Нужна проверка')),
                row('Автосбор', 'включён' if account.auto_collect else 'выключен'),
            ],
            icon='account',
            icons=icons,
        )
    ]
    if updated:
        blocks.append(updated)
    blocks.append(reward_section(account, 'Ежедневная награда', icons))
    if account.needs_review:
        blocks.append('<i>Нужна проверка после незавершённой операции. Нажми «Проверить вход».</i>')
    return GAP.join(blocks)


def reward_timing(account: Account, *, include_relative: bool = True) -> str:
    if account.daily_available:
        reward = 'Ежедневная награда: доступна'
    elif account.daily_reward_at:
        reward = f'Следующая награда: {telegram_time(account.daily_reward_at, style="dT")}'
    else:
        reward = 'Время следующей награды ещё не получено'
    if account.auto_collect:
        follows_deadline = account.daily_reward_at is not None and timedelta(
            seconds=30
        ) <= account.next_run_at - account.daily_reward_at <= timedelta(seconds=90)
        available_soon = account.daily_available and account.next_run_at <= now() + timedelta(
            seconds=90
        )
        label = (
            'Запланированный сбор' if follows_deadline or available_soon else 'Повторная проверка'
        )
        reward += f'\n{label}: {telegram_time(account.next_run_at, style="dT")}'
        if include_relative:
            reward += f' ({telegram_time(account.next_run_at, style="r")})'
    return reward


def reward_section(account: Account, title: str, icons: EmojiCatalog | None = None) -> str:
    text = section(
        title,
        reward_timing(account, include_relative=False).split('\n'),
        icon='history',
        icons=icons,
    )
    if account.auto_collect:
        text += '\n\nЗапуск: ' + telegram_time(account.next_run_at, style='r')
    return text


def settings_text(account: Account, icons: EmojiCatalog | None = None) -> str:
    return GAP.join(
        [
            heading(f'{account.display_name or account.id} · Настройки', 'settings', icons),
            quote(
                [
                    row('Автосбор', 'включён' if account.auto_collect else 'выключен'),
                    row('Уведомления', 'включены' if account.notifications else 'выключены'),
                ]
            ),
            reward_section(account, 'Расписание', icons),
            '<i>Сбор через 30–90 секунд после открытия награды. '
            'Без таймера — повторная проверка не раньше чем через 6 часов.</i>',
        ]
    )


def connection_text(account: Account, icons: EmojiCatalog | None = None) -> str:
    return GAP.join(
        [
            section(
                f'{account.display_name or account.id} · Подключение',
                [row('Состояние', AUTH.get(account.auth_state, 'Нужна проверка'))],
                icon='connect',
                icons=icons,
            ),
            '<i>Нажми «Войти через QR» и подтверди вход в приложении Яндекса. '
            'Пароль и cookies отправлять не нужно.</i>',
            section(
                'Запасной вход через локальный браузер',
                [
                    '<b>1. Открой терминал проекта</b>',
                    '<b>2. Войди в Яндекс</b>',
                    '<code>make login</code>',
                    '<b>3. Подключи сессию</b>',
                    f'<code>make account-session ACCOUNT={escape(account.id)}</code>',
                    '<b>4. Нажми «Проверить вход»</b>',
                    'После проверки включи автосбор в настройках.',
                ],
                icon='connect',
                icons=icons,
                expandable=True,
            ),
        ]
    )


def history_text(history: AccountHistory, icons: EmojiCatalog | None = None) -> str:
    blocks = [heading('История', 'history', icons)]
    if not history.runs:
        return GAP.join([*blocks, '<i>Операций пока нет. Начни с обновления баланса.</i>'])
    for run in history.runs[:5]:
        rows = [
            row(
                'Операция',
                'проверка баланса'
                if run.trigger == 'read'
                else ('игровые награды' if run.trigger == 'games' else 'ежедневная награда'),
            ),
            row('Результат', STATES.get(run.state, 'Состояние уточняется')),
            *run_rows(run),
            *operation_rows(run, history.operations),
        ]
        if run.error_code:
            rows.append(escape(error_text(run.error_code)))
        blocks.append(telegram_time(run.started_at) + '\n' + quote(rows, expandable=True))
    return GAP.join(blocks)


def statistics_text(
    account_id: str, statistics: RunStatistics, icons: EmojiCatalog | None = None
) -> str:
    return section(
        f'{account_id} · Статистика',
        [
            row('Запусков сбора', str(statistics.collections)),
            row('Ошибок и неподтверждённых результатов', str(statistics.failures)),
        ],
        icon='stats',
        icons=icons,
    )


def loading_text(collecting: bool, icons: EmojiCatalog | None = None) -> str:
    return GAP.join(
        [
            heading('Сбор наград' if collecting else 'Проверка аккаунта', 'loading', icons),
            quote(['Собираю доступные награды…' if collecting else 'Проверяю вход и баланс…']),
            '<i>Кнопки временно недоступны. Дождись результата — меню вернётся автоматически.</i>',
        ]
    )


def delete_text(account: Account, icons: EmojiCatalog) -> str:
    return section(
        f'Удалить аккаунт «{account.display_name or account.id}»?',
        [
            'Будут удалены подключение, сохранённая сессия и история этого аккаунта в боте.',
            'Автосбор будет остановлен. Сам аккаунт Яндекса и его монеты останутся.',
        ],
        icon='error',
        icons=icons,
    )
