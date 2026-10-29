"""Public errors intentionally never contain response bodies or credentials."""

from enum import StrEnum


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
        super().__init__(f'Rate limited; wait at least {retry_after:.0f} seconds')


class ClaimsDisabled(MarketError):
    pass


class ClaimFailureReason(StrEnum):
    UNKNOWN = 'unknown'
    NETWORK = 'network_error'
    HTTP = 'http_error'
    INVALID_JSON = 'invalid_json'
    API_ERROR = 'api_error'
    INVALID_OBJECT = 'invalid_object'


class UncertainClaim(MarketError):
    def __init__(
        self,
        message: str,
        *,
        reason: ClaimFailureReason = ClaimFailureReason.UNKNOWN,
        http_status: int | None = None,
    ):
        self.reason = ClaimFailureReason(reason)
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )
        super().__init__(message)
