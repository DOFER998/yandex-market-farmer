from html import escape

from src.services.emoji import EmojiCatalog
from src.services.qr_login import LoginAttempt
from src.views.errors import error_text
from src.views.fragments import GAP, heading, quote, row, section


def login_loading(icons: EmojiCatalog | None = None) -> str:
    return section('Вход в Яндекс', ['Готовлю QR-код…'], icon='loading', icons=icons)


def login_instructions(icons: EmojiCatalog | None = None) -> str:
    return GAP.join(
        [
            section(
                'Вход в Яндекс',
                [
                    '1. Отсканируй QR-код приложением Яндекса.',
                    '2. Подтверди вход в свой аккаунт.',
                    '3. Вернись сюда и сохрани подключение.',
                ],
                icon='connect',
                icons=icons,
            ),
            '<i>На этом телефоне используй кнопку «Открыть Яндекс».</i>',
            '<i>Ожидаю подтверждение до 5 минут. Пароль боту не нужен.</i>',
        ]
    )


def login_expired(icons: EmojiCatalog | None = None) -> str:
    return GAP.join(
        [
            section(
                'Время входа истекло',
                ['Это подключение уже нельзя сохранить.'],
                icon='history',
                icons=icons,
            ),
            '<i>Нажми «Войти через QR», чтобы получить новый код.</i>',
        ]
    )


def login_saved(icons: EmojiCatalog | None = None) -> str:
    return section(
        'Подключение сохранено',
        ['Автосбор выключен. Его можно включить в настройках аккаунта.'],
        icon='confirm',
        icons=icons,
    )


def login_result(attempt: LoginAttempt, icons: EmojiCatalog | None = None) -> str:
    if attempt.error:
        return section(
            'Вход не завершён', [escape(error_text(attempt.error))], icon='error', icons=icons
        )
    return GAP.join(
        [
            section(
                'Вход подтверждён',
                [
                    row('Аккаунт Яндекса', escape(attempt.login)),
                    row('Баланс', f'{attempt.balance} монет'),
                ],
                icon='confirm',
                icons=icons,
            ),
            heading('Сохранить подключение?', 'connect', icons),
            quote(
                [
                    'Новая сессия заменит прежнее подключение выбранного аккаунта.',
                    'Автосбор останется выключенным.',
                ]
            ),
        ]
    )
