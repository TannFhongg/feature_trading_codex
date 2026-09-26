"""Reconnectable authenticated User Data Stream with listen-key keepalive."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import suppress
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
    BinancePrivateConfig,
    UserStreamHealth,
    UserStreamState,
)
from trading_bot.binance.private_parsing import parse_user_data_message
from trading_bot.domain import DomainValidationError
from trading_bot.execution import (
    AccountUpdate,
    ListenKeyExpired,
    OrderTradeUpdate,
    UserDataEvent,
    UserStreamNotice,
)

RawMessage = str | bytes
MonotonicClock = Callable[[], int]
Sleeper = Callable[[float], Awaitable[None]]


class UserStreamRestClient(Protocol):
    """Listen-key operations required by the reconnecting stream."""

    async def start_user_stream(self) -> str:
        """Create or renew an active listen key."""

    async def keepalive_user_stream(self) -> str:
        """Extend the current listen key."""

    async def close_user_stream(self) -> None:
        """Invalidate the active listen key."""


class UserMessageSource(Protocol):
    """Injectable source representing one authenticated WebSocket connection."""

    def messages(self, url: str) -> AsyncGenerator[RawMessage, None]:
        """Yield private stream frames until the connection closes."""


class PrivateWebsocketsMessageSource:
    """websockets-backed private source with bounded buffering and ping/pong."""

    def __init__(self, config: BinancePrivateConfig) -> None:
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
                f"Binance private WebSocket connection failed: {type(error).__name__}"
            ) from error


def _monotonic_ms() -> int:
    return monotonic_ns() // 1_000_000


def _event_kind(event: UserDataEvent) -> str:
    if isinstance(event, OrderTradeUpdate):
        return "ORDER_TRADE_UPDATE"
    if isinstance(event, AccountUpdate):
        return "ACCOUNT_UPDATE"
    if isinstance(event, ListenKeyExpired):
        return "listenKeyExpired"
    if isinstance(event, UserStreamNotice):
        return event.event_type
    raise TypeError(f"unsupported user event {type(event).__name__}")


def _event_time(event: UserDataEvent) -> int:
    return event.event_time_ms


class BinanceUserDataStream:
    """Consume private events and recreate listen keys after any connection failure."""

    def __init__(
        self,
        rest_client: UserStreamRestClient,
        config: BinancePrivateConfig,
        *,
        message_source: UserMessageSource | None = None,
        monotonic_ms: MonotonicClock = _monotonic_ms,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._rest_client = rest_client
        self._config = config
        self._message_source = message_source or PrivateWebsocketsMessageSource(config)
        self._monotonic_ms = monotonic_ms
        self._sleeper = sleeper
        self._state = UserStreamState.IDLE
        self._listen_key_active = False
        self._last_event_monotonic_ms: int | None = None
        self._reconnect_count = 0
        self._last_error: str | None = None
        self._running = False

    def health(self) -> UserStreamHealth:
        return UserStreamHealth(
            state=self._state,
            listen_key_active=self._listen_key_active,
            last_event_monotonic_ms=self._last_event_monotonic_ms,
            reconnect_count=self._reconnect_count,
            last_error=self._last_error,
        )

    def _url(self, listen_key: str) -> str:
        return f"{self._config.websocket_base_url}/private/ws/{listen_key}"

    async def events(self) -> AsyncIterator[UserDataEvent]:
        """Yield typed events while maintaining and rotating the active listen key."""

        if self._running:
            raise DomainValidationError("user stream is already being consumed")
        self._running = True
        failures = 0
        try:
            while True:
                self._state = (
                    UserStreamState.CONNECTING
                    if self._reconnect_count == 0
                    else UserStreamState.RECONNECTING
                )
                source: AsyncGenerator[RawMessage, None] | None = None
                keepalive_task: asyncio.Task[None] | None = None
                received_valid_event = False
                failure: BinanceAdapterError
                try:
                    listen_key = await self._rest_client.start_user_stream()
                    self._listen_key_active = True
                    source = self._message_source.messages(self._url(listen_key))
                    keepalive_task = asyncio.create_task(self._keepalive(listen_key))
                    event_times: dict[str, int] = {}
                    while True:
                        next_message = asyncio.create_task(anext(source))
                        done, _ = await asyncio.wait(
                            {next_message, keepalive_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if keepalive_task in done:
                            next_message.cancel()
                            with suppress(asyncio.CancelledError):
                                await next_message
                            keepalive_task.result()
                            raise AssertionError("keepalive task ended without an exception")
                        try:
                            raw_message = next_message.result()
                        except StopAsyncIteration as error:
                            raise BinanceTransportError(
                                "Binance User Data Stream closed without a close reason"
                            ) from error
                        event = parse_user_data_message(raw_message)
                        kind = _event_kind(event)
                        current_event_time = _event_time(event)
                        previous_event_time = event_times.get(kind)
                        if (
                            previous_event_time is not None
                            and current_event_time < previous_event_time
                        ):
                            raise BinanceProtocolError(f"{kind} event time moved backwards")
                        event_times[kind] = current_event_time
                        self._last_event_monotonic_ms = self._monotonic_ms()
                        self._state = UserStreamState.LIVE
                        self._last_error = None
                        received_valid_event = True
                        failures = 0
                        yield event
                        if isinstance(event, ListenKeyExpired):
                            raise BinanceStaleStreamError(
                                "Binance User Data Stream listen key expired"
                            )
                except BinanceAdapterError as error:
                    failure = error
                finally:
                    if keepalive_task is not None:
                        keepalive_task.cancel()
                        with suppress(asyncio.CancelledError, BinanceAdapterError):
                            await keepalive_task
                    if source is not None:
                        await source.aclose()
                    if self._listen_key_active:
                        with suppress(BinanceAdapterError):
                            await self._rest_client.close_user_stream()
                    self._listen_key_active = False

                failures = 1 if received_valid_event else failures + 1
                self._reconnect_count += 1
                self._last_error = str(failure)
                max_attempts = self._config.max_reconnect_attempts
                if max_attempts is not None and failures > max_attempts:
                    self._state = UserStreamState.FAILED
                    raise BinanceTransportError(
                        "Binance User Data Stream exhausted reconnect attempts"
                    ) from failure
                self._state = UserStreamState.RECONNECTING
                delay = min(
                    self._config.reconnect_initial_seconds * (2 ** (failures - 1)),
                    self._config.reconnect_max_seconds,
                )
                await self._sleeper(delay)
        finally:
            self._running = False
            self._listen_key_active = False
            if self._state is not UserStreamState.FAILED:
                self._state = UserStreamState.STOPPED

    async def _keepalive(self, expected_listen_key: str) -> None:
        while True:
            await self._sleeper(self._config.user_stream_keepalive_seconds)
            actual_listen_key = await self._rest_client.keepalive_user_stream()
            if actual_listen_key != expected_listen_key:
                raise BinanceProtocolError("Binance keepalive returned a different listen key")
