"""Diagnostic metadata without exception payloads, tokens, message text or locals."""

import traceback

from aiogram.exceptions import TelegramBadRequest


def error_details(exc: Exception) -> str:
    fields = [f'error={type(exc).__name__}']
    method = getattr(exc, 'method', None)
    if method is not None:
        fields.append(f'method={type(method).__name__}')
    retry = getattr(exc, 'retry_after', None)
    if isinstance(retry, (int, float)):
        fields.append(f'retry_after={retry}')
    if isinstance(exc, TelegramBadRequest):
        reason = exc.message.lower()
        cases = {
            'message is not modified': 'not_modified',
            'message to edit not found': 'message_missing',
            "message can't be deleted": 'message_not_deletable',
            'there is no text': 'photo_has_no_text',
            'query is too old': 'callback_expired',
            "can't parse entities": 'invalid_markup',
            'emoji': 'emoji_rejected',
            'premium': 'premium_rejected',
        }
        fields.append(
            'reason='
            + next((code for text, code in cases.items() if text in reason), 'other_bad_request')
        )
    frames = traceback.extract_tb(exc.__traceback__)
    # Source locations only: no exception messages, source snippets or local values.
    own = [frame for frame in frames if '/src/' in frame.filename]
    if own:
        frame = own[-1]
        fields.append(
            f'source=src/{frame.filename.rsplit("/src/", 1)[1]}:{frame.lineno}:{frame.name}'
        )
    return ' '.join(fields)
