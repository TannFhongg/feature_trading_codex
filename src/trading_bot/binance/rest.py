"""Asynchronous public REST client for Binance USD-M Futures."""

import asyncio
from collections.abc import Awaitable, Callable
from time import time_ns
from typing import Protocol, cast

import httpx

from trading_bot.binance.errors import BinanceHttpError, BinanceTransportError
from trading_bot.binance.models import BinancePublicConfig, TimeSync
from trading_bot.binance.parsing import parse_server_time, parse_symbol_rules
from trading_bot.domain import DomainValidationError, SymbolRules

Clock = Callable[[], int]
Sleeper = Callable[[float], Awaitable[None]]


class JsonTransport(Protocol):
    """Minimal injectable transport used by deterministic adapter tests."""

    async def get_json(self, path: str) -> object:
        """Return a decoded JSON value for one relative REST path."""

    async def close(self) -> None:
        """Release transport resources."""


class HttpxJsonTransport:
    """HTTPX implementation with bounded timeouts and sanitized errors."""

    def __init__(self, config: BinancePublicConfig) -> None:
        self._client = httpx.AsyncClient(
            base_url=config.rest_base_url,
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": "feature-trading-codex/0.1"},
        )

    async def get_json(self, path: str) -> object:
        try:
            response = await self._client.get(path)
        except httpx.HTTPError as error:
            raise BinanceTransportError(
                f"Binance REST request failed: {type(error).__name__}"
            ) from error

        if response.status_code != 200:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise BinanceHttpError(
                response.status_code,
                "non-success response",
                retry_after_seconds=retry_after,
            )
        try:
            return cast(object, response.json())
        except ValueError as error:
            raise BinanceTransportError("Binance REST response is not valid JSON") from error

    async def close(self) -> None:
        await self._client.aclose()


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return parsed


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


class BinancePublicRestClient:
    """Fetch public exchange rules and estimate Binance server-clock offset."""

    def __init__(
        self,
        config: BinancePublicConfig | None = None,
        *,
        transport: JsonTransport | None = None,
        clock_ms: Clock = _wall_clock_ms,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._config = config or BinancePublicConfig()
        self._transport = transport or HttpxJsonTransport(self._config)
        self._owns_transport = transport is None
        self._clock_ms = clock_ms
        self._sleeper = sleeper

    async def __aenter__(self) -> "BinancePublicRestClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the internally owned transport, if any."""

        if self._owns_transport:
            await self._transport.close()

    async def server_time_ms(self) -> int:
        """Return Binance server time from the dedicated time endpoint."""

        payload = await self._get_json_with_retry("/fapi/v1/time")
        return parse_server_time(payload)

    async def synchronize_time(self, *, samples: int = 3) -> TimeSync:
        """Select the lowest-latency clock-offset sample using an NTP-style midpoint."""

        if isinstance(samples, bool) or not isinstance(samples, int):
            raise TypeError("samples must be int")
        if samples <= 0:
            raise DomainValidationError("samples must be greater than zero")

        observations: list[TimeSync] = []
        for _ in range(samples):
            started_ms = self._clock_ms()
            server_time_ms = await self.server_time_ms()
            finished_ms = self._clock_ms()
            if finished_ms < started_ms:
                raise BinanceTransportError("local wall clock moved backwards during time sync")
            round_trip_ms = finished_ms - started_ms
            midpoint_ms = started_ms + round_trip_ms // 2
            observations.append(
                TimeSync(
                    server_time_ms=server_time_ms,
                    local_midpoint_ms=midpoint_ms,
                    offset_ms=server_time_ms - midpoint_ms,
                    round_trip_ms=round_trip_ms,
                )
            )
        return min(observations, key=lambda observation: observation.round_trip_ms)

    async def fetch_symbol_rules(self, symbol: str) -> SymbolRules:
        """Fetch current filters for one active USDT perpetual symbol."""

        payload = await self._get_json_with_retry("/fapi/v1/exchangeInfo")
        return parse_symbol_rules(payload, symbol)

    async def _get_json_with_retry(self, path: str) -> object:
        for attempt in range(1, self._config.rest_max_attempts + 1):
            try:
                return await self._transport.get_json(path)
            except BinanceHttpError as error:
                retryable = error.status_code in {408, 429} or error.status_code >= 500
                if not retryable or error.status_code == 418:
                    raise
                delay = error.retry_after_seconds
                if delay is None:
                    delay = self._config.rest_retry_base_seconds * (2 ** (attempt - 1))
                if attempt == self._config.rest_max_attempts:
                    raise
            except BinanceTransportError:
                delay = self._config.rest_retry_base_seconds * (2 ** (attempt - 1))
                if attempt == self._config.rest_max_attempts:
                    raise
            await self._sleeper(delay)

        raise AssertionError("REST retry loop exhausted without returning or raising")
