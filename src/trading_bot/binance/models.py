"""Immutable configuration and clock records for the Binance public adapter."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from urllib.parse import urlsplit

from trading_bot.domain import DomainValidationError, OrderSide

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


def _require_non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int")
    if value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


def _require_decimal(
    name: str,
    value: object,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise DomainValidationError(f"{name} must be finite")
    if positive and value <= 0:
        raise DomainValidationError(f"{name} must be greater than zero")
    if non_negative and value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


def _validate_symbol(symbol: str) -> None:
    if not symbol or symbol != symbol.strip().upper():
        raise DomainValidationError("symbol must be non-empty, trimmed, and uppercase")
    if not symbol.replace("_", "").isalnum():
        raise DomainValidationError("symbol may contain only letters, numbers, and underscores")


class MarketStreamKind(StrEnum):
    """Supported Binance market streams and their required routed endpoint."""

    AGGREGATE_TRADE = "aggTrade"
    MARK_PRICE = "markPrice"
    BOOK_TICKER = "bookTicker"

    @property
    def route(self) -> str:
        if self is MarketStreamKind.BOOK_TICKER:
            return "public"
        return "market"

    def stream_name(self, symbol: str) -> str:
        _validate_symbol(symbol)
        suffix = self.value
        if self is MarketStreamKind.MARK_PRICE:
            suffix = f"{suffix}@1s"
        return f"{symbol.lower()}@{suffix}"


class MarketStreamState(StrEnum):
    """Observable lifecycle state for a reconnecting public market stream."""

    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    LIVE = "LIVE"
    RECONNECTING = "RECONNECTING"
    STALE = "STALE"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class BinancePublicConfig:
    """Testnet-first endpoints and bounded retry settings for public market data."""

    rest_base_url: str = TESTNET_REST_BASE_URL
    websocket_base_url: str = TESTNET_WEBSOCKET_BASE_URL
    request_timeout_seconds: float = 10.0
    rest_max_attempts: int = 3
    rest_retry_base_seconds: float = 0.25
    websocket_open_timeout_seconds: float = 10.0
    websocket_ping_interval_seconds: float = 120.0
    websocket_ping_timeout_seconds: float = 30.0
    market_stale_after_seconds: float = 15.0
    reconnect_initial_seconds: float = 0.25
    reconnect_max_seconds: float = 10.0
    max_reconnect_attempts: int | None = None
    websocket_max_queue: int = 64

    def __post_init__(self) -> None:
        _validate_base_url("rest_base_url", self.rest_base_url, "https")
        _validate_base_url("websocket_base_url", self.websocket_base_url, "wss")
        _require_positive_finite("request_timeout_seconds", self.request_timeout_seconds)
        _require_positive_int("rest_max_attempts", self.rest_max_attempts)
        _require_positive_finite("rest_retry_base_seconds", self.rest_retry_base_seconds)
        _require_positive_finite(
            "websocket_open_timeout_seconds", self.websocket_open_timeout_seconds
        )
        _require_positive_finite(
            "websocket_ping_interval_seconds", self.websocket_ping_interval_seconds
        )
        _require_positive_finite(
            "websocket_ping_timeout_seconds", self.websocket_ping_timeout_seconds
        )
        _require_positive_finite("market_stale_after_seconds", self.market_stale_after_seconds)
        _require_positive_finite("reconnect_initial_seconds", self.reconnect_initial_seconds)
        _require_positive_finite("reconnect_max_seconds", self.reconnect_max_seconds)
        if self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise DomainValidationError(
                "reconnect_max_seconds must be greater than or equal to reconnect_initial_seconds"
            )
        if self.max_reconnect_attempts is not None:
            _require_non_negative_int("max_reconnect_attempts", self.max_reconnect_attempts)
        _require_positive_int("websocket_max_queue", self.websocket_max_queue)

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


@dataclass(frozen=True, slots=True)
class AggregateTrade:
    """Typed Binance aggregate trade with taker/aggressor direction."""

    event_time_ms: int
    trade_time_ms: int
    aggregate_trade_id: int
    symbol: str
    price: Decimal
    quantity: Decimal
    aggressor_side: OrderSide

    def __post_init__(self) -> None:
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        _require_non_negative_int("trade_time_ms", self.trade_time_ms)
        _require_non_negative_int("aggregate_trade_id", self.aggregate_trade_id)
        _validate_symbol(self.symbol)
        _require_decimal("price", self.price, positive=True)
        _require_decimal("quantity", self.quantity, positive=True)
        if not isinstance(self.aggressor_side, OrderSide):
            raise TypeError("aggressor_side must be OrderSide")


@dataclass(frozen=True, slots=True)
class MarkPrice:
    """Typed mark/index/funding update from the one-second stream."""

    event_time_ms: int
    symbol: str
    mark_price: Decimal
    index_price: Decimal
    estimated_settle_price: Decimal
    funding_rate: Decimal
    next_funding_time_ms: int

    def __post_init__(self) -> None:
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        _validate_symbol(self.symbol)
        _require_decimal("mark_price", self.mark_price, positive=True)
        _require_decimal("index_price", self.index_price, positive=True)
        _require_decimal("estimated_settle_price", self.estimated_settle_price, positive=True)
        _require_decimal("funding_rate", self.funding_rate)
        _require_non_negative_int("next_funding_time_ms", self.next_funding_time_ms)


@dataclass(frozen=True, slots=True)
class BookTicker:
    """Typed best bid/ask snapshot for one symbol."""

    event_time_ms: int
    transaction_time_ms: int
    update_id: int
    symbol: str
    bid_price: Decimal
    bid_quantity: Decimal
    ask_price: Decimal
    ask_quantity: Decimal

    def __post_init__(self) -> None:
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        _require_non_negative_int("transaction_time_ms", self.transaction_time_ms)
        _require_non_negative_int("update_id", self.update_id)
        _validate_symbol(self.symbol)
        bid_price = _require_decimal("bid_price", self.bid_price, positive=True)
        ask_price = _require_decimal("ask_price", self.ask_price, positive=True)
        _require_decimal("bid_quantity", self.bid_quantity, non_negative=True)
        _require_decimal("ask_quantity", self.ask_quantity, non_negative=True)
        if bid_price > ask_price:
            raise DomainValidationError("bid_price must not exceed ask_price")


MarketEvent = AggregateTrade | MarkPrice | BookTicker


@dataclass(frozen=True, slots=True)
class MarketStreamHealth:
    """Immutable health snapshot consumable by a future stale-feed breaker."""

    state: MarketStreamState
    stale: bool
    last_message_monotonic_ms: int | None
    reconnect_count: int
    last_error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, MarketStreamState):
            raise TypeError("state must be MarketStreamState")
        if not isinstance(self.stale, bool):
            raise TypeError("stale must be bool")
        if self.last_message_monotonic_ms is not None:
            _require_non_negative_int("last_message_monotonic_ms", self.last_message_monotonic_ms)
        _require_non_negative_int("reconnect_count", self.reconnect_count)
