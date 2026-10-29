import re

from src.market.errors import MarketError


def validate_id(value: str) -> str:
    if value.startswith('__') or not re.fullmatch(r'[a-z0-9_-]{1,32}', value):
        raise MarketError('Account ID: 1-32 lowercase letters, digits, underscore or hyphen')
    return value
