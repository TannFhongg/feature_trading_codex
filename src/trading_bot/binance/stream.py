"""Reconnectable public WebSocket market streams with stale-feed detection."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from time import monotonic_ns
from typing import Protocol

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from trading_bot.binance.errors import (
    BinanceAdapterError,
    BinanceProtocolError,
    BinanceStaleStreamError,
    BinanceTransportError,
)
from trading_bot.binance.models import (
    AggregateTrade,
    BinancePublicConfig,
    BookTicker,
    MarketEvent,
    MarketStreamHealth,
    MarketStreamKind,
    MarketStreamState,
    MarkPrice,
)
from trading_bot.binance.parsing import parse_market_message
from trading_bot.domain import DomainValidationError

RawMessage = str | bytes
MonotonicClock = Callable[[], int]
Sleeper = Callable[[float], Awaitable[None]]


class MessageSource(Protocol):
    """Injectable source representing one WebSocket connection attempt."""

    def messages(self, url: str) -> AsyncGenerator[RawMessage, None]:
        """Yield raw messages until the connection ends or fails."""


class WebsocketsMessageSource:
    """websockets-backed source with automatic ping/pong and bounded buffering."""

    def __init__(self, config: BinancePublicConfig) -> None:
        self._config = config

    async def messages(self, url: str) -> AsyncGenerator[RawMessage, None]:
        try:
            async with connect(
                url,
                open_timeout=self._config.websocket_open_timeout_seconds,
                ping_interval=self._config.websocket_ping_interval_seconds,
                ping_timeout=self._config.websocket_ping_timeout_seconds,
                close_timeout=5,
                max_queue=self._config.websocket_max_queue,
                user_agent_header="feature-trading-codex/0.1",
            ) as websocket:
                async for message in websocket:
                    yield message
        except (OSError, TimeoutError, WebSocketException) as error:
            raise BinanceTransportError(
                f"Binance WebSocket connection failed: {type(error).__name__}"
            ) from error


def _monotonic_ms() -> int:
    return monotonic_ns() // 1_000_000


def _event_kind(event: MarketEvent) -> MarketStreamKind:
    if isinstance(event, AggregateTrade):
        return MarketStreamKind.AGGREGATE_TRADE
    if isinstance(event, MarkPrice):
        return MarketStreamKind.MARK_PRICE
    if isinstance(event, BookTicker):
        return MarketStreamKind.BOOK_TICKER
    raise TypeError(f"unsupported market event {type(event).__name__}")


class BinanceMarketStream:
    """Consume one routed Binance stream group and reconnect after failures."""

    def __init__(
        self,
        symbol: str,
        kinds: tuple[MarketStreamKind, ...],
        config: BinancePublicConfig | None = None,
        *,
        message_source: MessageSource | None = None,
        monotonic_ms: MonotonicClock = _monotonic_ms,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._config = config or BinancePublicConfig()
        if not symbol or symbol != symbol.strip().upper():
            raise DomainValidationError("symbol must be non-empty, trimmed, and uppercase")
        if not kinds:
            raise DomainValidationError("at least one market stream kind is required")
        if any(not isinstance(kind, MarketStreamKind) for kind in kinds):
            raise TypeError("kinds must contain only MarketStreamKind values")
        if len(set(kinds)) != len(kinds):
            raise DomainValidationError("market stream kinds must be unique")
        routes = {kind.route for kind in kinds}
        if len(routes) != 1:
            raise DomainValidationError(
                "public and market streams require separate Binance connections"
            )

        self._symbol = symbol
        self._kinds = kinds
        self._route = routes.pop()
        self._message_source = message_source or WebsocketsMessageSource(self._config)
        self._monotonic_ms = monotonic_ms
        self._sleeper = sleeper
        self._state = MarketStreamState.IDLE
        self._last_message_monotonic_ms: int | None = None
        self._last_event_time_by_kind: dict[MarketStreamKind, int] = {}
        self._reconnect_count = 0
        self._last_error: str | None = None
        self._running = False

    @property
    def url(self) -> str:
        """Return the combined-stream URL using Binance's required routed path."""

        stream_names = "/".join(kind.stream_name(self._symbol) for kind in self._kinds)
        return f"{self._config.websocket_base_url}/{self._route}/stream?streams={stream_names}"

    def health(self) -> MarketStreamHealth:
        """Return current health without performing I/O."""

        now_ms = self._monotonic_ms()
        stale_after_ms = int(self._config.market_stale_after_seconds * 1_000)
        elapsed_stale = (
            self._last_message_monotonic_ms is None
            or now_ms - self._last_message_monotonic_ms > stale_after_ms
        )
        return MarketStreamHealth(
            state=self._state,
            stale=self._state is not MarketStreamState.LIVE or elapsed_stale,
            last_message_monotonic_ms=self._last_message_monotonic_ms,
            reconnect_count=self._reconnect_count,
            last_error=self._last_error,
        )

    async def events(self) -> AsyncIterator[MarketEvent]:
        """Yield typed events, reconnecting with bounded exponential backoff."""

        if self._running:
            raise DomainValidationError("market stream is already being consumed")
        self._running = True
        failures = 0
        try:
            while True:
                self._state = (
                    MarketStreamState.CONNECTING
                    if self._reconnect_count == 0
                    else MarketStreamState.RECONNECTING
                )
                source = self._message_source.messages(self.url)
                received_valid_event = False
                failure: BinanceAdapterError
                try:
                    while True:
                        try:
                            raw_message = await asyncio.wait_for(
                                anext(source),
                                timeout=self._config.market_stale_after_seconds,
                            )
                        except StopAsyncIteration as error:
                            raise BinanceTransportError(
                                "Binance WebSocket closed without a close reason"
                            ) from error
                        event = parse_market_message(raw_message, expected_symbol=self._symbol)
                        kind = _event_kind(event)
                        if kind not in self._kinds:
                            raise BinanceProtocolError(
                                f"received unsubscribed {kind.value} market event"
                            )
                        previous_event_time = self._last_event_time_by_kind.get(kind)
                        if (
                            previous_event_time is not None
                            and event.event_time_ms < previous_event_time
                        ):
                            raise BinanceProtocolError(f"{kind.value} event time moved backwards")
                        self._last_event_time_by_kind[kind] = event.event_time_ms
                        self._last_message_monotonic_ms = self._monotonic_ms()
                        self._state = MarketStreamState.LIVE
                        self._last_error = None
                        received_valid_event = True
                        failures = 0
                        yield event
                except TimeoutError:
                    self._state = MarketStreamState.STALE
                    failure = BinanceStaleStreamError(
                        "Binance market stream exceeded the stale-data threshold"
                    )
                except BinanceAdapterError as error:
                    failure = error
                finally:
                    await source.aclose()

                failures = 1 if received_valid_event else failures + 1
                self._reconnect_count += 1
                self._last_error = str(failure)
                max_attempts = self._config.max_reconnect_attempts
                if max_attempts is not None and failures > max_attempts:
                    self._state = MarketStreamState.FAILED
                    raise BinanceTransportError(
                        "Binance market stream exhausted reconnect attempts"
                    ) from failure

                self._state = MarketStreamState.RECONNECTING
                delay = min(
                    self._config.reconnect_initial_seconds * (2 ** (failures - 1)),
                    self._config.reconnect_max_seconds,
                )
                await self._sleeper(delay)
        finally:
            self._running = False
            if self._state is not MarketStreamState.FAILED:
                self._state = MarketStreamState.STOPPED
