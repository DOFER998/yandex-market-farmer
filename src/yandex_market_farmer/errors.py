"""Public errors intentionally never contain response bodies or credentials."""


class MarketError(Exception):
    pass


class SessionError(MarketError):
    pass


class AuthRequired(MarketError):
    pass


class AccessDenied(MarketError):
    pass


class ProtocolError(MarketError):
    pass


class TransportError(MarketError):
    pass


class RateLimited(MarketError):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limited; wait at least {retry_after:.0f} seconds")


class ClaimsDisabled(MarketError):
    pass


class UncertainClaim(MarketError):
    pass
