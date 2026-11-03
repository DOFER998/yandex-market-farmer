"""Cached bonus screens; opening a screen never requests a reward."""

from html import escape
from typing import TYPE_CHECKING

from src.database.models import Account
from src.services.emoji import EmojiCatalog
from src.views.errors import error_text
from src.views.fragments import GAP, heading, quote, row, section
from src.views.time import telegram_time

if TYPE_CHECKING:
    from src.database.models import BonusStatus

STATES = {
    'unknown': 'Нет актуальных данных — обнови статус',
    'collecting': 'Эфир доступен для сбора',
    'limit': 'Лимит достигнут',
    'waiting_stream': 'Ожидание эфира',
    'stalled': 'Начисления приостановились',
    'disabled': 'Сбор выключен',
    'failed': 'Проверка не завершена',
    'success': 'Награды и задания проверены',
    'no_rewards': 'Доступных наград нет',
    'uncertain': 'Нужна проверка начисления',
    'blocked': 'Нужна проверка состояния',
    'reconciled': 'Состояние награды проверено',
}


def duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return (
        ' '.join(
            text
            for amount, text in (
                (hours, f'{hours} ч'),
                (minutes, f'{minutes} мин'),
                (seconds, f'{seconds} с'),
            )
            if amount
        )
        or '0 с'
    )


def bonus_text(
    account: Account,
    source: str,
    status: 'BonusStatus | None',
    icons: EmojiCatalog | None = None,
    *,
    available: bool = True,
) -> str:
    games = source == 'games'
    enabled = account.games_auto if games else account.live_auto
    rows = [
        row('Состояние', STATES.get(status.state if status else 'unknown', STATES['unknown'])),
        row('Автосбор наград', 'включён' if enabled else 'выключен'),
        row('Общий автосбор', 'включён' if account.auto_collect else 'выключен'),
    ]
    if not games and status:
        if status.balance is not None:
            value = f'{status.balance} монет'
            if status.limit is not None:
                value = f'{status.balance} / {status.limit} монет'
            rows.append(row('За эфиры', value))
        if status.balance is not None and status.limit is not None:
            rows.append(row('До лимита', f'{max(0, status.limit - status.balance)} монет'))
    blocks = [
        section(
            f'{account.display_name or account.id} · {"Игры и задания" if games else "Эфиры"}',
            rows,
            icon=source,
            icons=icons,
        )
    ]
    if status:
        # Relative Telegram time must stay outside blockquotes on iOS.
        blocks.append('<i>Проверено:</i> ' + telegram_time(status.checked_at, style='r'))
        if status.next_check_at and available and enabled and account.auto_collect:
            blocks.append(
                '<i>Следующая проверка:</i> ' + telegram_time(status.next_check_at, style='r')
            )
        if (
            not games
            and available
            and enabled
            and account.auto_collect
            and status.state == 'collecting'
            and status.remaining_seconds is not None
            and status.remaining_seconds > 0
        ):
            blocks.append(
                f'<i>Ориентир до лимита — {duration(status.remaining_seconds)} '
                'по текущим условиям API. Это приблизительная оценка без пауз и задержек; '
                'эфир может закончиться раньше.</i>'
            )
        if status.error_code:
            blocks.append(
                section(
                    'Что делать',
                    [escape(error_text(status.error_code))],
                    icon='error',
                    icons=icons,
                )
            )
    if not available:
        blocks.append(
            '<i>Автосбор этого источника отключён в конфигурации бота. '
            'Настройка автосбора сохранена.</i>'
        )
    if not account.auto_collect:
        blocks.append(
            '<i>Для автоматического сбора включи общий автосбор в настройках аккаунта. '
            'Этот переключатель меняет только выбранный источник.</i>'
        )
    if games:
        blocks.append(
            '<i>При включённой отправке результатов бот отправляет результаты уровней '
            'и события трёх проверенных недельных заданий: Jump, Hexagon и 2048. '
            'Также проверяет два бесплатных задания колеса нажатием на карточку. '
            'Результаты проверяются по состоянию сервера.</i>'
        )
    else:
        blocks.append(
            '<i>Обновление статуса проверяет эфир и счётчик монет. '
            'Автосбор работает, пока доступен эфир и не достигнут лимит.</i>'
        )
    return GAP.join(blocks)


def bonus_loading_text(source: str, icons: EmojiCatalog | None = None) -> str:
    return GAP.join(
        [
            heading(
                'Проверка игровых наград' if source == 'games' else 'Проверка эфира',
                'loading',
                icons,
            ),
            quote(
                [
                    'Проверяю и получаю доступные награды…'
                    if source == 'games'
                    else 'Обновляю статус эфира и счётчик монет…'
                ]
            ),
            '<i>Дождись результата — меню вернётся автоматически.</i>',
        ]
    )
