from html import escape

from src.services.qr_login import LoginAttempt
from src.views.errors import error_text


def login_result(attempt: LoginAttempt) -> str:
    if attempt.error:
        return '<b>Вход не завершён</b>\n\n' + escape(error_text(attempt.error))
    return (
        '<b>Вход подтверждён Яндексом</b>\n\n'
        f'Аккаунт Яндекса: <b>{escape(attempt.login)}</b>\n'
        f'Баланс: <b>{attempt.balance} монет</b>\n\n'
        'Сохранить эту сессию для выбранного аккаунта бота? '
        'Предыдущее подключение будет заменено. Автосбор останется выключенным.'
    )
