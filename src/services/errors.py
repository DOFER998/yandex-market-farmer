from src.market.errors import MarketError


class AccountNotFound(MarketError):
    """The account was deleted or the callback references an unknown account."""
