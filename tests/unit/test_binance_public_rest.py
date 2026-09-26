import asyncio
from collections.abc import Iterator
from decimal import Decimal

import pytest

from trading_bot.binance import (
    TESTNET_REST_BASE_URL,
    TESTNET_WEBSOCKET_BASE_URL,
    BinanceHttpError,
    BinanceProtocolError,
    BinancePublicConfig,
    BinancePublicRestClient,
    BinanceTransportError,
    parse_server_time,
    parse_symbol_rules,
)
from trading_bot.domain import DomainValidationError


def exchange_info_payload(**symbol_overrides: object) -> dict[str, object]:
    symbol: dict[str, object] = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "contractType": "PERPETUAL",
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
            {"filterType": "MAX_NUM_ORDERS", "limit": 200},
        ],
    }
    symbol.update(symbol_overrides)
    return {"timezone": "UTC", "symbols": [symbol]}


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.paths: list[str] = []
        self.closed = False

    async def get_json(self, path: str) -> object:
        self.paths.append(path)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


def clock(values: list[int]) -> tuple[Iterator[int], object]:
    iterator = iter(values)
    return iterator, lambda: next(iterator)


def test_public_config_defaults_to_testnet() -> None:
    config = BinancePublicConfig()

    assert config.rest_base_url == TESTNET_REST_BASE_URL
    assert config.websocket_base_url == TESTNET_WEBSOCKET_BASE_URL


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rest_base_url", "http://demo-fapi.binance.com"),
        ("websocket_base_url", "wss://demo-fstream.binance.com/"),
        ("request_timeout_seconds", 0),
        ("rest_max_attempts", 0),
    ],
)
def test_public_config_rejects_unsafe_or_unbounded_values(field: str, value: object) -> None:
    with pytest.raises(DomainValidationError):
        BinancePublicConfig(**{field: value})  # type: ignore[arg-type]


def test_parse_server_time_rejects_float_and_boolean() -> None:
    assert parse_server_time({"serverTime": 1_700_000_000_000}) == 1_700_000_000_000

    with pytest.raises(BinanceProtocolError, match="serverTime"):
        parse_server_time({"serverTime": 1.5})
    with pytest.raises(BinanceProtocolError, match="serverTime"):
        parse_server_time({"serverTime": True})


def test_parse_symbol_rules_uses_filters_not_precision_fields() -> None:
    payload = exchange_info_payload(pricePrecision=99, quantityPrecision=99)

    rules = parse_symbol_rules(payload, "BTCUSDT")

    assert rules.tick_size == Decimal("0.10")
    assert rules.step_size == Decimal("0.001")
    assert rules.min_qty == Decimal("0.001")
    assert rules.min_notional == Decimal("5")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": "SETTLING"}, "TRADING"),
        ({"contractType": "CURRENT_QUARTER"}, "perpetual"),
        ({"marginAsset": "USDC"}, "USDT-margined"),
        (
            {"filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.1"}]},
            "LOT_SIZE",
        ),
    ],
)
def test_parse_symbol_rules_rejects_unsupported_or_incomplete_symbol(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(BinanceProtocolError, match=message):
        parse_symbol_rules(exchange_info_payload(**overrides), "BTCUSDT")


def test_rest_client_fetches_rules_through_async_transport() -> None:
    transport = FakeTransport([exchange_info_payload()])
    client = BinancePublicRestClient(transport=transport)

    rules = asyncio.run(client.fetch_symbol_rules("BTCUSDT"))

    assert rules.symbol == "BTCUSDT"
    assert transport.paths == ["/fapi/v1/exchangeInfo"]


def test_time_sync_selects_lowest_round_trip_sample() -> None:
    transport = FakeTransport(
        [
            {"serverTime": 1_060},
            {"serverTime": 2_030},
            {"serverTime": 3_050},
        ]
    )
    _, fake_clock = clock([1_000, 1_100, 2_000, 2_040, 3_000, 3_080])
    client = BinancePublicRestClient(transport=transport, clock_ms=fake_clock)  # type: ignore[arg-type]

    result = asyncio.run(client.synchronize_time(samples=3))

    assert result.round_trip_ms == 40
    assert result.local_midpoint_ms == 2_020
    assert result.offset_ms == 10
    assert result.apply(5_000) == 5_010


def test_time_sync_rejects_backwards_local_clock() -> None:
    transport = FakeTransport([{"serverTime": 1_000}])
    _, fake_clock = clock([1_000, 999])
    client = BinancePublicRestClient(transport=transport, clock_ms=fake_clock)  # type: ignore[arg-type]

    with pytest.raises(BinanceTransportError, match="backwards"):
        asyncio.run(client.synchronize_time(samples=1))


def test_rest_client_retries_retryable_get_with_exponential_backoff() -> None:
    transport = FakeTransport(
        [
            BinanceHttpError(503, "unavailable"),
            BinanceTransportError("connection reset"),
            {"serverTime": 123},
        ]
    )
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    client = BinancePublicRestClient(transport=transport, sleeper=record_sleep)

    assert asyncio.run(client.server_time_ms()) == 123
    assert delays == [0.25, 0.5]


def test_rest_client_honors_retry_after_but_never_retries_http_418() -> None:
    retry_transport = FakeTransport(
        [BinanceHttpError(429, "limited", retry_after_seconds=2.0), {"serverTime": 123}]
    )
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    retry_client = BinancePublicRestClient(transport=retry_transport, sleeper=record_sleep)
    assert asyncio.run(retry_client.server_time_ms()) == 123
    assert delays == [2.0]

    banned_transport = FakeTransport([BinanceHttpError(418, "banned")])
    banned_client = BinancePublicRestClient(transport=banned_transport)
    with pytest.raises(BinanceHttpError) as error:
        asyncio.run(banned_client.server_time_ms())
    assert error.value.status_code == 418
    assert len(banned_transport.paths) == 1


def test_rest_client_raises_last_transport_error_after_bounded_attempts() -> None:
    transport = FakeTransport(
        [
            BinanceTransportError("first"),
            BinanceTransportError("second"),
            BinanceTransportError("final"),
        ]
    )
    client = BinancePublicRestClient(transport=transport, sleeper=_no_sleep)

    with pytest.raises(BinanceTransportError, match="final"):
        asyncio.run(client.server_time_ms())
    assert len(transport.paths) == 3


def test_injected_transport_lifecycle_remains_caller_owned() -> None:
    transport = FakeTransport([])
    client = BinancePublicRestClient(transport=transport)

    asyncio.run(client.close())

    assert not transport.closed


async def _no_sleep(_: float) -> None:
    return None
