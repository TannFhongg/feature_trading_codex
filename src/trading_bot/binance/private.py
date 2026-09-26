"""Authenticated Binance USD-M Futures REST execution adapter."""

import asyncio
import hmac
from collections.abc import Awaitable, Callable
from decimal import Decimal
from hashlib import sha256
from time import time_ns
from typing import Protocol, cast
from urllib.parse import urlencode

import httpx

from trading_bot.binance.errors import (
    BinanceAmbiguousOrderError,
    BinanceApiError,
    BinanceHttpError,
    BinanceOrderSubmissionDisabledError,
    BinanceTransportError,
)
from trading_bot.binance.models import BinancePrivateConfig, TimeSync
from trading_bot.binance.private_parsing import (
    parse_account_snapshot,
    parse_account_trades,
    parse_cancel_all_orders,
    parse_commission_rates,
    parse_exchange_order,
    parse_exchange_orders,
    parse_income_records,
    parse_listen_key,
    parse_positions,
)
from trading_bot.execution import (
    AccountSnapshot,
    CommissionRates,
    EmergencyCloseRequest,
    ExchangeFill,
    ExchangeOrder,
    IncomeRecord,
    OrderRequest,
    PositionSnapshot,
)

Clock = Callable[[], int]
Sleeper = Callable[[float], Awaitable[None]]
RequestParams = tuple[tuple[str, str], ...]


class PrivateJsonTransport(Protocol):
    """Injectable authenticated transport used by deterministic tests."""

    async def request_json(
        self,
        method: str,
        path: str,
        params: RequestParams,
        headers: dict[str, str],
    ) -> object:
        """Send one request and return decoded JSON."""

    async def close(self) -> None:
        """Release transport resources."""


class HttpxPrivateJsonTransport:
    """HTTPX transport with bounded timeout and secret-safe error conversion."""

    def __init__(self, config: BinancePrivateConfig) -> None:
        self._client = httpx.AsyncClient(
            base_url=config.rest_base_url,
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": "feature-trading-codex/0.1"},
        )

    async def request_json(
        self,
        method: str,
        path: str,
        params: RequestParams,
        headers: dict[str, str],
    ) -> object:
        try:
            response = await self._client.request(method, path, params=params, headers=headers)
        except httpx.HTTPError as error:
            raise BinanceTransportError(
                f"Binance private REST request failed: {type(error).__name__}"
            ) from error

        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        try:
            payload = cast(object, response.json())
        except ValueError as error:
            raise BinanceTransportError(
                "Binance private REST response is not valid JSON"
            ) from error
        if not 200 <= response.status_code < 300:
            error_code: int | None = None
            if isinstance(payload, dict):
                raw_code = payload.get("code")
                if isinstance(raw_code, int) and not isinstance(raw_code, bool):
                    error_code = raw_code
            raise BinanceApiError(
                response.status_code,
                error_code,
                retry_after_seconds=retry_after,
            )
        return payload

    async def close(self) -> None:
        await self._client.aclose()


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


def _decimal_parameter(value: object) -> str:
    if not isinstance(value, Decimal):
        raise TypeError("financial request parameters must be Decimal")
    return format(value, "f")


class BinancePrivateRestClient:
    """Signed Testnet-first adapter with query-before-retry order semantics."""

    def __init__(
        self,
        config: BinancePrivateConfig,
        *,
        transport: PrivateJsonTransport | None = None,
        time_sync: TimeSync | None = None,
        clock_ms: Clock = _wall_clock_ms,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._config = config
        self._transport = transport or HttpxPrivateJsonTransport(config)
        self._owns_transport = transport is None
        self._time_offset_ms = 0 if time_sync is None else time_sync.offset_ms
        self._clock_ms = clock_ms
        self._sleeper = sleeper

    async def __aenter__(self) -> "BinancePrivateRestClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_transport:
            await self._transport.close()

    def update_time_sync(self, time_sync: TimeSync) -> None:
        if not isinstance(time_sync, TimeSync):
            raise TypeError("time_sync must be TimeSync")
        self._time_offset_ms = time_sync.offset_ms

    async def submit_order(self, request: OrderRequest) -> ExchangeOrder:
        """Submit a GTC limit order, resolving every ambiguous result before retry."""

        if not self._config.order_submission_enabled:
            raise BinanceOrderSubmissionDisabledError(
                "order submission is disabled; enable it explicitly for Testnet or "
                "approved live use"
            )
        params = (
            ("symbol", request.symbol),
            ("side", request.side.value),
            ("positionSide", "BOTH"),
            ("type", "LIMIT"),
            ("timeInForce", "GTC"),
            ("quantity", _decimal_parameter(request.quantity)),
            ("price", _decimal_parameter(request.price)),
            ("reduceOnly", str(request.reduce_only).lower()),
            ("newClientOrderId", request.client_order_id),
            ("newOrderRespType", "RESULT"),
        )
        for attempt in range(1, self._config.ambiguous_request_max_attempts + 1):
            try:
                payload = await self._signed_request("POST", "/fapi/v1/order", params)
                return parse_exchange_order(payload)
            except (BinanceTransportError, BinanceHttpError) as error:
                if not _is_ambiguous(error):
                    raise
                resolved = await self._query_after_ambiguous(
                    request.symbol, request.client_order_id
                )
                if resolved is not None:
                    return resolved
                if attempt == self._config.ambiguous_request_max_attempts:
                    raise BinanceAmbiguousOrderError(
                        request.client_order_id, "submission"
                    ) from error
                await self._sleeper(self._config.ambiguous_retry_delay_seconds)
        raise AssertionError("ambiguous submit loop exhausted")

    async def submit_reduce_only_market(
        self,
        request: EmergencyCloseRequest,
    ) -> ExchangeOrder:
        """Submit a deterministic one-way MARKET close, resolving ambiguity before retry."""

        if not self._config.order_submission_enabled:
            raise BinanceOrderSubmissionDisabledError(
                "order submission is disabled; enable it explicitly for Testnet or "
                "approved live use"
            )
        params = (
            ("symbol", request.symbol),
            ("side", request.side.value),
            ("positionSide", "BOTH"),
            ("type", "MARKET"),
            ("quantity", _decimal_parameter(request.quantity)),
            ("reduceOnly", "true"),
            ("newClientOrderId", request.client_order_id),
            ("newOrderRespType", "RESULT"),
        )
        for attempt in range(1, self._config.ambiguous_request_max_attempts + 1):
            try:
                payload = await self._signed_request("POST", "/fapi/v1/order", params)
                return parse_exchange_order(payload)
            except (BinanceTransportError, BinanceHttpError) as error:
                if not _is_ambiguous(error):
                    raise
                resolved = await self._query_after_ambiguous(
                    request.symbol, request.client_order_id
                )
                if resolved is not None:
                    return resolved
                if attempt == self._config.ambiguous_request_max_attempts:
                    raise BinanceAmbiguousOrderError(
                        request.client_order_id, "emergency close"
                    ) from error
                await self._sleeper(self._config.ambiguous_retry_delay_seconds)
        raise AssertionError("ambiguous emergency close loop exhausted")

    async def cancel_all_open_orders(self, symbol: str) -> None:
        """Cancel all standard orders and verify ambiguous outcomes by listing open orders."""

        params = (("symbol", symbol),)
        for attempt in range(1, self._config.ambiguous_request_max_attempts + 1):
            try:
                payload = await self._signed_request("DELETE", "/fapi/v1/allOpenOrders", params)
                parse_cancel_all_orders(payload)
                return
            except (BinanceTransportError, BinanceHttpError) as error:
                if not _is_ambiguous(error):
                    raise
                if not await self.list_open_orders(symbol):
                    return
                if attempt == self._config.ambiguous_request_max_attempts:
                    raise BinanceAmbiguousOrderError(
                        f"all-open-orders:{symbol}", "cancel-all"
                    ) from error
                await self._sleeper(self._config.ambiguous_retry_delay_seconds)
        raise AssertionError("ambiguous cancel-all loop exhausted")

    async def cancel_order(
        self,
        symbol: str,
        client_order_id: str,
    ) -> ExchangeOrder | None:
        """Cancel by client ID; query the order before any retry after ambiguity."""

        params = (("symbol", symbol), ("origClientOrderId", client_order_id))
        for attempt in range(1, self._config.ambiguous_request_max_attempts + 1):
            try:
                payload = await self._signed_request("DELETE", "/fapi/v1/order", params)
                return parse_exchange_order(payload)
            except BinanceApiError as error:
                if error.error_code in {-2011, -2013}:
                    return await self.query_order(symbol, client_order_id)
                if not _is_ambiguous(error):
                    raise
                resolved = await self._query_after_ambiguous(symbol, client_order_id)
            except (BinanceTransportError, BinanceHttpError) as error:
                if not _is_ambiguous(error):
                    raise
                resolved = await self._query_after_ambiguous(symbol, client_order_id)
            if resolved is None or not resolved.status.is_active:
                return resolved
            if attempt == self._config.ambiguous_request_max_attempts:
                raise BinanceAmbiguousOrderError(client_order_id, "cancellation")
            await self._sleeper(self._config.ambiguous_retry_delay_seconds)
        raise AssertionError("ambiguous cancel loop exhausted")

    async def query_order(
        self,
        symbol: str,
        client_order_id: str,
    ) -> ExchangeOrder | None:
        try:
            payload = await self._signed_request(
                "GET",
                "/fapi/v1/order",
                (("symbol", symbol), ("origClientOrderId", client_order_id)),
            )
        except BinanceApiError as error:
            if error.error_code == -2013:
                return None
            raise
        return parse_exchange_order(payload)

    async def list_open_orders(self, symbol: str) -> tuple[ExchangeOrder, ...]:
        payload = await self._signed_request("GET", "/fapi/v1/openOrders", (("symbol", symbol),))
        return parse_exchange_orders(payload)

    async def list_account_trades(
        self,
        symbol: str,
        *,
        from_id: int | None = None,
        limit: int = 1_000,
    ) -> tuple[ExchangeFill, ...]:
        params: list[tuple[str, str]] = [("symbol", symbol), ("limit", str(limit))]
        if from_id is not None:
            params.append(("fromId", str(from_id)))
        payload = await self._signed_request("GET", "/fapi/v1/userTrades", tuple(params))
        return parse_account_trades(payload)

    async def fetch_positions(self, symbol: str) -> tuple[PositionSnapshot, ...]:
        payload = await self._signed_request("GET", "/fapi/v3/positionRisk", (("symbol", symbol),))
        return parse_positions(payload)

    async def fetch_account(self) -> AccountSnapshot:
        payload = await self._signed_request("GET", "/fapi/v3/account", ())
        return parse_account_snapshot(payload)

    async def fetch_commission_rates(self, symbol: str) -> CommissionRates:
        payload = await self._signed_request(
            "GET", "/fapi/v1/commissionRate", (("symbol", symbol),)
        )
        return parse_commission_rates(payload)

    async def list_income(
        self,
        symbol: str,
        *,
        income_type: str = "FUNDING_FEE",
        limit: int = 1_000,
    ) -> tuple[IncomeRecord, ...]:
        payload = await self._signed_request(
            "GET",
            "/fapi/v1/income",
            (("symbol", symbol), ("incomeType", income_type), ("limit", str(limit))),
        )
        return parse_income_records(payload)

    async def start_user_stream(self) -> str:
        payload = await self._api_key_request("POST", "/fapi/v1/listenKey")
        return parse_listen_key(payload)

    async def keepalive_user_stream(self) -> str:
        payload = await self._api_key_request("PUT", "/fapi/v1/listenKey")
        return parse_listen_key(payload)

    async def close_user_stream(self) -> None:
        await self._api_key_request("DELETE", "/fapi/v1/listenKey")

    async def _query_after_ambiguous(
        self,
        symbol: str,
        client_order_id: str,
    ) -> ExchangeOrder | None:
        try:
            return await self.query_order(symbol, client_order_id)
        except BinanceTransportError as error:
            raise BinanceAmbiguousOrderError(client_order_id, "reconciliation query") from error

    async def _api_key_request(self, method: str, path: str) -> object:
        return await self._transport.request_json(
            method,
            path,
            (),
            {"X-MBX-APIKEY": self._config.credentials.api_key},
        )

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: RequestParams,
    ) -> object:
        timestamp = self._clock_ms() + self._time_offset_ms
        if timestamp < 0:
            raise BinanceTransportError("synchronized timestamp must not be negative")
        unsigned = (
            *params,
            ("recvWindow", str(self._config.recv_window_ms)),
            ("timestamp", str(timestamp)),
        )
        signature = hmac.new(
            self._config.credentials.api_secret.encode("utf-8"),
            urlencode(unsigned).encode("utf-8"),
            sha256,
        ).hexdigest()
        return await self._transport.request_json(
            method,
            path,
            (*unsigned, ("signature", signature)),
            {"X-MBX-APIKEY": self._config.credentials.api_key},
        )


def _is_ambiguous(error: BaseException) -> bool:
    if isinstance(error, BinanceHttpError):
        return error.status_code == 503
    return isinstance(error, BinanceTransportError)
