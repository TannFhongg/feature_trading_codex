import asyncio
import json
from collections.abc import AsyncGenerator, Iterator
from decimal import Decimal

import pytest

from trading_bot.binance import (
    BinanceCredentials,
    BinancePrivateConfig,
    BinanceProtocolError,
    BinanceTransportError,
    BinanceUserDataStream,
    UserStreamState,
    parse_user_data_message,
)
from trading_bot.execution import (
    AccountUpdate,
    ExecutionType,
    ListenKeyExpired,
    OrderStatus,
    OrderTradeUpdate,
    UserStreamNotice,
)


def order_update(
    *,
    event_time: int = 100,
    transaction_time: int = 99,
    execution_type: str = "NEW",
    status: str = "NEW",
    last_quantity: str = "0",
    cumulative_quantity: str = "0",
) -> str:
    is_trade = execution_type == "TRADE"
    return json.dumps(
        {
            "e": "ORDER_TRADE_UPDATE",
            "E": event_time,
            "T": transaction_time,
            "o": {
                "s": "BTCUSDT",
                "c": "grid-neutral-btc-1-buy-2",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "q": "0.002",
                "p": "65000.10",
                "ap": "65000.10" if cumulative_quantity != "0" else "0",
                "x": execution_type,
                "X": status,
                "i": 42,
                "l": last_quantity,
                "z": cumulative_quantity,
                "L": "65000.10" if is_trade else "0",
                "N": "USDT" if is_trade else None,
                "n": "0.026" if is_trade else "0",
                "T": transaction_time,
                "t": 777 if is_trade else 0,
                "rp": "0.01" if is_trade else "0",
                "m": True,
                "R": False,
            },
        }
    )


def account_update(*, event_time: int = 101) -> str:
    return json.dumps(
        {
            "e": "ACCOUNT_UPDATE",
            "E": event_time,
            "T": event_time - 1,
            "a": {
                "m": "ORDER",
                "B": [{"a": "USDT", "wb": "1000.5", "cw": "900.5", "bc": "0"}],
                "P": [
                    {
                        "s": "BTCUSDT",
                        "pa": "0.001",
                        "ep": "65000.10",
                        "bep": "65026.10",
                        "cr": "0",
                        "up": "1.5",
                        "mt": "isolated",
                        "iw": "100",
                        "ps": "BOTH",
                    }
                ],
            },
        }
    )


class ScriptedUserRestClient:
    def __init__(self, listen_keys: list[str]) -> None:
        self._listen_keys: Iterator[str] = iter(listen_keys)
        self.started = 0
        self.kept_alive = 0
        self.closed = 0
        self.current_key = ""

    async def start_user_stream(self) -> str:
        self.started += 1
        self.current_key = next(self._listen_keys)
        return self.current_key

    async def keepalive_user_stream(self) -> str:
        self.kept_alive += 1
        return self.current_key

    async def close_user_stream(self) -> None:
        self.closed += 1


class ScriptedUserSource:
    def __init__(self, connections: list[list[str | bytes | Exception]]) -> None:
        self._connections = iter(connections)
        self.urls: list[str] = []

    async def messages(self, url: str) -> AsyncGenerator[str | bytes, None]:
        self.urls.append(url)
        for item in next(self._connections):
            if isinstance(item, Exception):
                raise item
            yield item


async def selective_sleep(delay: float) -> None:
    if delay >= 60:
        await asyncio.Event().wait()


def stream_config(**overrides: object) -> BinancePrivateConfig:
    values: dict[str, object] = {
        "credentials": BinanceCredentials("key", "secret"),
        "reconnect_initial_seconds": 0.001,
        "reconnect_max_seconds": 0.001,
    }
    values.update(overrides)
    return BinancePrivateConfig(**values)  # type: ignore[arg-type]


def test_parse_trade_update_preserves_decimal_fill_and_lifecycle() -> None:
    event = parse_user_data_message(
        order_update(
            execution_type="TRADE",
            status="PARTIALLY_FILLED",
            last_quantity="0.001",
            cumulative_quantity="0.001",
        )
    )

    assert isinstance(event, OrderTradeUpdate)
    assert event.execution_type is ExecutionType.TRADE
    assert event.order.status is OrderStatus.PARTIALLY_FILLED
    assert event.order.executed_quantity == Decimal("0.001")
    assert event.commission == Decimal("0.026")
    assert event.fill is not None
    assert event.fill.trade_id == 777
    assert event.fill.maker


def test_parse_account_update_preserves_signed_position_and_balances() -> None:
    event = parse_user_data_message(account_update())

    assert isinstance(event, AccountUpdate)
    assert event.reason == "ORDER"
    assert event.balances[0].wallet_balance == Decimal("1000.5")
    assert event.positions[0].quantity == Decimal("0.001")
    assert event.positions[0].unrealized_pnl == Decimal("1.5")


def test_user_parser_rejects_financial_float_and_unknown_event() -> None:
    payload = json.loads(order_update())
    payload["o"]["p"] = 65000.1

    with pytest.raises(BinanceProtocolError, match="decimal string"):
        parse_user_data_message(json.dumps(payload))
    with pytest.raises(BinanceProtocolError, match="unsupported"):
        parse_user_data_message(json.dumps({"e": "FUTURE_UNKNOWN", "E": 1}))


def test_known_non_ledger_event_is_typed_notice() -> None:
    event = parse_user_data_message(json.dumps({"e": "MARGIN_CALL", "E": 123, "T": 122, "cw": "1"}))

    assert isinstance(event, UserStreamNotice)
    assert event.event_type == "MARGIN_CALL"


def test_user_stream_uses_private_route_and_recreates_listen_key() -> None:
    rest = ScriptedUserRestClient(["listen-one", "listen-two"])
    source = ScriptedUserSource([[order_update()], [account_update()]])
    stream = BinanceUserDataStream(
        rest,
        stream_config(),
        message_source=source,
        monotonic_ms=lambda: 500,
        sleeper=selective_sleep,
    )

    async def consume() -> tuple[object, object]:
        events = stream.events()
        first = await anext(events)
        second = await anext(events)
        await events.aclose()
        return first, second

    first, second = asyncio.run(consume())

    assert isinstance(first, OrderTradeUpdate)
    assert isinstance(second, AccountUpdate)
    assert source.urls[0].endswith("/private/ws/listen-one")
    assert source.urls[1].endswith("/private/ws/listen-two")
    assert rest.started == 2
    assert rest.closed == 2
    assert stream.health().state is UserStreamState.STOPPED
    assert stream.health().reconnect_count == 1
    assert stream.health().last_event_monotonic_ms == 500


def test_user_stream_rejects_out_of_order_event_on_same_connection() -> None:
    rest = ScriptedUserRestClient(["listen-one"])
    source = ScriptedUserSource([[order_update(event_time=200), order_update(event_time=199)]])
    stream = BinanceUserDataStream(
        rest,
        stream_config(max_reconnect_attempts=0),
        message_source=source,
        sleeper=selective_sleep,
    )

    async def consume() -> None:
        events = stream.events()
        await anext(events)
        await anext(events)

    with pytest.raises(BinanceTransportError, match="exhausted") as error:
        asyncio.run(consume())
    assert isinstance(error.value.__cause__, BinanceProtocolError)
    assert "moved backwards" in str(error.value.__cause__)


def test_expired_listen_key_event_forces_reconnect() -> None:
    rest = ScriptedUserRestClient(["listen-one"])
    expired = json.dumps({"e": "listenKeyExpired", "E": 200, "listenKey": "listen-one"})
    source = ScriptedUserSource([[expired]])
    stream = BinanceUserDataStream(
        rest,
        stream_config(max_reconnect_attempts=0),
        message_source=source,
        sleeper=selective_sleep,
    )

    async def consume() -> object:
        events = stream.events()
        first = await anext(events)
        with pytest.raises(BinanceTransportError, match="exhausted"):
            await anext(events)
        return first

    event = asyncio.run(consume())

    assert isinstance(event, ListenKeyExpired)
    assert not stream.health().listen_key_active
