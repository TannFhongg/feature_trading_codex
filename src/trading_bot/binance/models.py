"""Immutable configuration and clock records for the Binance public adapter."""

from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlsplit

from trading_bot.domain import DomainValidationError

TESTNET_REST_BASE_URL = "https://demo-fapi.binance.com"
TESTNET_WEBSOCKET_BASE_URL = "wss://demo-fstream.binance.com"
MAINNET_REST_BASE_URL = "https://fapi.binance.com"
MAINNET_WEBSOCKET_BASE_URL = "wss://fstream.binance.com"


def _validate_base_url(name: str, value: str, scheme: str) -> None:
    parsed = urlsplit(value)
    if (
        not value
        or value != value.strip()
        or value.endswith("/")
        or parsed.scheme != scheme
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise DomainValidationError(
            f"{name} must be an origin URL using {scheme} with no trailing slash"
        )


def _require_positive_finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be int or float")
    converted = float(value)
    if not isfinite(converted) or converted <= 0:
        raise DomainValidationError(f"{name} must be finite and greater than zero")
    return converted


def _require_positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int")
    if value <= 0:
        raise DomainValidationError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class BinancePublicConfig:
    """Testnet-first endpoints and bounded retry settings for public market data."""

    rest_base_url: str = TESTNET_REST_BASE_URL
    websocket_base_url: str = TESTNET_WEBSOCKET_BASE_URL
    request_timeout_seconds: float = 10.0
    rest_max_attempts: int = 3
    rest_retry_base_seconds: float = 0.25

    def __post_init__(self) -> None:
        _validate_base_url("rest_base_url", self.rest_base_url, "https")
        _validate_base_url("websocket_base_url", self.websocket_base_url, "wss")
        _require_positive_finite("request_timeout_seconds", self.request_timeout_seconds)
        _require_positive_int("rest_max_attempts", self.rest_max_attempts)
        _require_positive_finite("rest_retry_base_seconds", self.rest_retry_base_seconds)

    @classmethod
    def mainnet(cls, **overrides: object) -> "BinancePublicConfig":
        """Build an explicit read-only mainnet public-data configuration."""

        values: dict[str, object] = {
            "rest_base_url": MAINNET_REST_BASE_URL,
            "websocket_base_url": MAINNET_WEBSOCKET_BASE_URL,
        }
        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class TimeSync:
    """One selected server-time sample using the lowest observed round trip."""

    server_time_ms: int
    local_midpoint_ms: int
    offset_ms: int
    round_trip_ms: int

    def __post_init__(self) -> None:
        for name, value in (
            ("server_time_ms", self.server_time_ms),
            ("local_midpoint_ms", self.local_midpoint_ms),
            ("offset_ms", self.offset_ms),
            ("round_trip_ms", self.round_trip_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be int")
        if self.server_time_ms < 0 or self.local_midpoint_ms < 0 or self.round_trip_ms < 0:
            raise DomainValidationError("time synchronization values must not be negative")

    def apply(self, local_time_ms: int) -> int:
        """Convert a local wall-clock millisecond value to estimated server time."""

        if isinstance(local_time_ms, bool) or not isinstance(local_time_ms, int):
            raise TypeError("local_time_ms must be int")
        if local_time_ms < 0:
            raise DomainValidationError("local_time_ms must not be negative")
        return local_time_ms + self.offset_ms
