import asyncio
import hmac
from collections.abc import Iterator
from decimal import Decimal
from hashlib import sha256
from urllib.parse import urlencode

import pytest

from trading_bot.binance import (
    MAINNET_REST_BASE_URL,
    BinanceAmbiguousOrderError,
    BinanceApiError,
    BinanceCredentials,
    BinanceOrderSubmissionDisabledError,
    BinancePrivateConfig,
    BinancePrivateRestClient,
    BinanceTransportError,
    RequestParams,
    TimeSync,
)
from trading_bot.domain import DomainValidationError, OrderSide
from trading_bot.execution import EmergencyCloseRequest, OrderRequest, OrderStatus


def order_request(**overrides: object) -> OrderRequest:
    values: dict[str, object] = {
        "strategy_id": "neutral-btc",
        "level_index": 1,
        "cycle": 2,
        "symbol": "BTCUSDT",
        "side": OrderSide.BUY,
        "price": Decimal("65000.10"),
        "quantity": Decimal("0.002"),
    }
    values.update(overrides)
    return OrderRequest(**values)  # type: ignore[arg-type]


def order_payload(
    *,
    status: str = "NEW",
    executed_qty: str = "0",
    client_order_id: str | None = None,
    update_time: int = 1_000,
) -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "clientOrderId": client_order_id or order_request().client_order_id,
        "orderId": 42,
        "side": "BUY",
        "status": status,
        "price": "65000.10",
        "origQty": "0.002",
        "executedQty": executed_qty,
        "avgPrice": "0" if executed_qty == "0" else "65000.10",
        "reduceOnly": False,
        "updateTime": update_time,
    }


class ScriptedPrivateTransport:
    def __init__(self, responses: list[object]) -> None:
        self._responses: Iterator[object] = iter(responses)
        self.calls: list[tuple[str, str, RequestParams, dict[str, str]]] = []
        self.closed = False

    async def request_json(
        self,
        method: str,
        path: str,
        params: RequestParams,
        headers: dict[str, str],
    ) -> object:
        self.calls.append((method, path, params, headers))
        response = next(self._responses)
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


async def no_sleep(_: float) -> None:
    return None


def private_config(**overrides: object) -> BinancePrivateConfig:
    values: dict[str, object] = {
        "credentials": BinanceCredentials("test-key", "test-secret"),
        "order_submission_enabled": True,
    }
    values.update(overrides)
    return BinancePrivateConfig(**values)  # type: ignore[arg-type]


def test_private_config_is_testnet_first_and_redacts_credentials() -> None:
    credentials = BinanceCredentials("test-key", "test-secret")
    config = BinancePrivateConfig(credentials)

    assert "demo-fapi" in config.rest_base_url
    assert not config.order_submission_enabled
    assert "test-key" not in repr(credentials)
    assert "test-secret" not in repr(credentials)
    assert "test-key" not in repr(config)


def test_mainnet_submission_requires_separate_live_opt_in() -> None:
    credentials = BinanceCredentials("key", "secret")

    read_only = BinancePrivateConfig.mainnet(credentials)
    assert read_only.rest_base_url == MAINNET_REST_BASE_URL
    assert not read_only.order_submission_enabled

    with pytest.raises(DomainValidationError, match="live_trading_enabled"):
        BinancePrivateConfig.mainnet(credentials, order_submission_enabled=True)

    enabled = BinancePrivateConfig.mainnet(
        credentials,
        order_submission_enabled=True,
        live_trading_enabled=True,
    )
    assert enabled.live_trading_enabled


def test_order_submission_is_locked_by_default() -> None:
    transport = ScriptedPrivateTransport([])
    client = BinancePrivateRestClient(
        BinancePrivateConfig(BinanceCredentials("key", "secret")),
        transport=transport,
    )

    with pytest.raises(BinanceOrderSubmissionDisabledError):
        asyncio.run(client.submit_order(order_request()))
    assert transport.calls == []


def test_signed_query_uses_time_offset_recv_window_and_encoded_hmac() -> None:
    transport = ScriptedPrivateTransport([order_payload()])
    config = private_config()
    client = BinancePrivateRestClient(
        config,
        transport=transport,
        time_sync=TimeSync(2_005, 2_000, 5, 10),
        clock_ms=lambda: 10_000,
    )

    order = asyncio.run(client.query_order("BTCUSDT", order_request().client_order_id))

    assert order is not None
    assert order.status is OrderStatus.NEW
    method, path, params, headers = transport.calls[0]
    assert (method, path) == ("GET", "/fapi/v1/order")
    assert headers == {"X-MBX-APIKEY": "test-key"}
    assert ("timestamp", "10005") in params
    assert ("recvWindow", "5000") in params
    unsigned = params[:-1]
    expected = hmac.new(b"test-secret", urlencode(unsigned).encode("utf-8"), sha256).hexdigest()
    assert params[-1] == ("signature", expected)


def test_submit_timeout_queries_before_returning_confirmed_order() -> None:
    request = order_request()
    transport = ScriptedPrivateTransport(
        [BinanceTransportError("timeout"), order_payload(client_order_id=request.client_order_id)]
    )
    client = BinancePrivateRestClient(
        private_config(), transport=transport, clock_ms=lambda: 1_000, sleeper=no_sleep
    )

    order = asyncio.run(client.submit_order(request))

    assert order.client_order_id == request.client_order_id
    assert [call[:2] for call in transport.calls] == [
        ("POST", "/fapi/v1/order"),
        ("GET", "/fapi/v1/order"),
    ]


def test_submit_retries_same_id_only_after_query_confirms_absence() -> None:
    request = order_request()
    transport = ScriptedPrivateTransport(
        [
            BinanceApiError(503, -1000),
            BinanceApiError(400, -2013),
            order_payload(client_order_id=request.client_order_id),
        ]
    )
    client = BinancePrivateRestClient(
        private_config(), transport=transport, clock_ms=lambda: 1_000, sleeper=no_sleep
    )

    asyncio.run(client.submit_order(request))

    assert [call[0] for call in transport.calls] == ["POST", "GET", "POST"]
    submitted_ids = [
        dict(call[2])["newClientOrderId"] for call in transport.calls if call[0] == "POST"
    ]
    assert submitted_ids == [request.client_order_id, request.client_order_id]


def test_submit_never_retries_when_reconciliation_query_fails() -> None:
    transport = ScriptedPrivateTransport(
        [BinanceTransportError("timeout"), BinanceTransportError("query timeout")]
    )
    client = BinancePrivateRestClient(
        private_config(), transport=transport, clock_ms=lambda: 1_000, sleeper=no_sleep
    )

    with pytest.raises(BinanceAmbiguousOrderError, match="reconciliation query"):
        asyncio.run(client.submit_order(order_request()))
    assert [call[0] for call in transport.calls] == ["POST", "GET"]


def test_submit_raises_unknown_after_bounded_query_before_retry_attempts() -> None:
    responses: list[object] = [
        BinanceTransportError("timeout one"),
        BinanceApiError(400, -2013),
        BinanceTransportError("timeout two"),
        BinanceApiError(400, -2013),
    ]
    transport = ScriptedPrivateTransport(responses)
    client = BinancePrivateRestClient(
        private_config(), transport=transport, clock_ms=lambda: 1_000, sleeper=no_sleep
    )

    with pytest.raises(BinanceAmbiguousOrderError, match="submission"):
        asyncio.run(client.submit_order(order_request()))
    assert [call[0] for call in transport.calls] == ["POST", "GET", "POST", "GET"]


def test_cancel_timeout_queries_and_retries_only_while_order_is_active() -> None:
    request = order_request()
    active = order_payload(client_order_id=request.client_order_id)
    canceled = order_payload(
        status="CANCELED", client_order_id=request.client_order_id, update_time=1_001
    )
    transport = ScriptedPrivateTransport([BinanceTransportError("timeout"), active, canceled])
    client = BinancePrivateRestClient(
        private_config(), transport=transport, clock_ms=lambda: 1_000, sleeper=no_sleep
    )

    order = asyncio.run(client.cancel_order("BTCUSDT", request.client_order_id))

    assert order is not None and order.status is OrderStatus.CANCELED
    assert [call[0] for call in transport.calls] == ["DELETE", "GET", "DELETE"]


def test_user_stream_listen_key_calls_use_api_key_without_signature() -> None:
    transport = ScriptedPrivateTransport([{"listenKey": "listen-1"}, {"listenKey": "listen-1"}, {}])
    client = BinancePrivateRestClient(private_config(), transport=transport)

    async def scenario() -> tuple[str, str]:
        started = await client.start_user_stream()
        kept_alive = await client.keepalive_user_stream()
        await client.close_user_stream()
        return started, kept_alive

    assert asyncio.run(scenario()) == ("listen-1", "listen-1")
    assert [call[0] for call in transport.calls] == ["POST", "PUT", "DELETE"]
    assert all(call[2] == () for call in transport.calls)
    assert all(call[3] == {"X-MBX-APIKEY": "test-key"} for call in transport.calls)


def test_private_read_contract_parses_orders_trades_account_fees_and_income() -> None:
    request = order_request()
    transport = ScriptedPrivateTransport(
        [
            [order_payload(client_order_id=request.client_order_id)],
            [
                {
                    "symbol": "BTCUSDT",
                    "id": 7,
                    "orderId": 42,
                    "side": "BUY",
                    "price": "65000.10",
                    "qty": "0.001",
                    "commission": "0.026",
                    "commissionAsset": "USDT",
                    "realizedPnl": "0.01",
                    "time": 1_001,
                    "maker": True,
                }
            ],
            [
                {
                    "symbol": "BTCUSDT",
                    "positionSide": "BOTH",
                    "positionAmt": "0.001",
                    "entryPrice": "65000.10",
                    "breakEvenPrice": "65026.10",
                    "unRealizedProfit": "1.5",
                    "isolatedWallet": "100",
                    "markPrice": "66000",
                    "liquidationPrice": "50000",
                    "notional": "66",
                    "initialMargin": "3.3",
                    "maintMargin": "0.4",
                    "updateTime": 1_002,
                }
            ],
            {
                "totalWalletBalance": "1000",
                "totalUnrealizedProfit": "1.5",
                "totalMarginBalance": "1001.5",
                "availableBalance": "800",
                "totalInitialMargin": "3.3",
                "totalMaintMargin": "0.4",
                "assets": [
                    {
                        "asset": "USDT",
                        "walletBalance": "1000",
                        "crossWalletBalance": "900",
                        "updateTime": 1_003,
                    }
                ],
                "positions": [{"symbol": "BTCUSDT", "updateTime": 1_004}],
            },
            {
                "symbol": "BTCUSDT",
                "makerCommissionRate": "0.0002",
                "takerCommissionRate": "0.0005",
            },
            [
                {
                    "symbol": "BTCUSDT",
                    "incomeType": "FUNDING_FEE",
                    "tranId": 9,
                    "asset": "USDT",
                    "income": "-0.10",
                    "time": 1_005,
                }
            ],
        ]
    )
    client = BinancePrivateRestClient(private_config(), transport=transport, clock_ms=lambda: 2_000)

    async def scenario() -> None:
        orders = await client.list_open_orders("BTCUSDT")
        trades = await client.list_account_trades("BTCUSDT", from_id=7, limit=100)
        positions = await client.fetch_positions("BTCUSDT")
        account = await client.fetch_account()
        commission = await client.fetch_commission_rates("BTCUSDT")
        income = await client.list_income("BTCUSDT", limit=100)

        assert orders[0].status is OrderStatus.NEW
        assert trades[0].quantity == Decimal("0.001")
        assert positions[0].quantity == Decimal("0.001")
        assert positions[0].margin_type == "unknown"
        assert positions[0].liquidation_price == Decimal("50000")
        assert positions[0].maintenance_margin == Decimal("0.4")
        assert account.update_time_ms == 1_004
        assert account.available_balance == Decimal("800")
        assert account.total_initial_margin == Decimal("3.3")
        assert account.total_maintenance_margin == Decimal("0.4")
        assert commission.maker_rate == Decimal("0.0002")
        assert income[0].amount == Decimal("-0.10")

    asyncio.run(scenario())

    assert [call[1] for call in transport.calls] == [
        "/fapi/v1/openOrders",
        "/fapi/v1/userTrades",
        "/fapi/v3/positionRisk",
        "/fapi/v3/account",
        "/fapi/v1/commissionRate",
        "/fapi/v1/income",
    ]
    assert dict(transport.calls[1][2])["fromId"] == "7"


def test_emergency_market_close_is_reduce_only_and_query_before_retry() -> None:
    request = EmergencyCloseRequest(
        action_id="incident-1",
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        quantity=Decimal("0.002"),
    )
    confirmed = {
        **order_payload(client_order_id=request.client_order_id),
        "side": "SELL",
        "price": "0",
        "avgPrice": "65000",
        "origQty": "0.002",
        "executedQty": "0.002",
        "status": "FILLED",
        "reduceOnly": True,
    }
    transport = ScriptedPrivateTransport([BinanceTransportError("timeout"), confirmed])
    client = BinancePrivateRestClient(
        private_config(), transport=transport, clock_ms=lambda: 1_000, sleeper=no_sleep
    )

    order = asyncio.run(client.submit_reduce_only_market(request))

    assert order.status is OrderStatus.FILLED
    assert [call[:2] for call in transport.calls] == [
        ("POST", "/fapi/v1/order"),
        ("GET", "/fapi/v1/order"),
    ]
    submitted = dict(transport.calls[0][2])
    assert submitted["type"] == "MARKET"
    assert submitted["positionSide"] == "BOTH"
    assert submitted["reduceOnly"] == "true"
    assert submitted["newClientOrderId"] == request.client_order_id
    assert "price" not in submitted
    assert "timeInForce" not in submitted


def test_cancel_all_uses_signed_endpoint_and_resolves_timeout_from_open_orders() -> None:
    transport = ScriptedPrivateTransport([BinanceTransportError("timeout"), []])
    client = BinancePrivateRestClient(
        private_config(), transport=transport, clock_ms=lambda: 1_000, sleeper=no_sleep
    )

    asyncio.run(client.cancel_all_open_orders("BTCUSDT"))

    assert [call[:2] for call in transport.calls] == [
        ("DELETE", "/fapi/v1/allOpenOrders"),
        ("GET", "/fapi/v1/openOrders"),
    ]
