from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from src.common.utils.errors import error_details


def test_telegram_error_logs_describe_cause_without_payload():
    method = EditMessageText(chat_id=123, message_id=456, text='secret-login-link')
    exc = TelegramBadRequest(method=method, message="can't parse entities: secret-token")
    details = error_details(exc)
    assert 'method=EditMessageText' in details
    assert 'reason=invalid_markup' in details
    assert 'secret' not in details
    assert '123' not in details
