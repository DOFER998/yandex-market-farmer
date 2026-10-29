from datetime import UTC, datetime
from typing import Literal


def telegram_time(value: datetime, *, style: Literal['dt', 'dT', 'r'] = 'dt') -> str:
    """Telegram localizes the entity; the inner text is a readable UTC fallback."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Telegram time requires an aware datetime')
    fallback = value.astimezone(UTC).strftime('%d.%m.%Y %H:%M:%S UTC')
    return f'<tg-time unix="{int(value.timestamp())}" format="{style}">{fallback}</tg-time>'
