import asyncio
import json
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest

from trading_bot.binance import (
    AggregateTrade,
    BinanceMarketStream,
    BinanceProtocolError,
    BinancePublicConfig,
    BinanceTransportError,
    BookTicker,
    MarketStreamKind,
    MarketStreamState,
    MarkPrice,
    parse_market_message,
)
from trading_bot.domain import DomainValidationError, OrderSide


def combined(stream: str, data: dict[str, object]) -> str:
    return json.dumps({"stream": stream, "data": data})


def aggregate_trade(*, event_time: int = 100, buyer_is_maker: bool = True) -> str:
    return combined(
        "btcusdt@aggTrade",
        {
            "e": "aggTrade",
            "E": event_time,
            "a": 123,
            "s": "BTCUSDT",
            "p": "65000.10",
            "q": "0.002",
            "T": event_time - 1,
            "m": buyer_is_maker,
        },
    )


def mark_price(*, event_time: int = 101) -> str:
    return combined(
        "btcusdt@markPrice@1s",
        {
            "e": "markPriceUpdate",
            "E": event_time,
            "s": "BTCUSDT",
            "p": "65001.20",
            "i": "65000.90",
            "P": "64950.00",
            "r": "-0.00010000",
            "T": 10_000,
        },
    )


def book_ticker(*, event_time: int = 102) -> str:
    return combined(
        "btcusdt@bookTicker",
        {
            "e": "bookTicker",
            "u": 456,
            "E": event_time,
            "T": event_time - 1,
            "s": "BTCUSDT",
            "b": "65000.10",
            "B": "1.2",
            "a": "65000.20",
            "A": "0.8",
        },
    )


class ScriptedMessageSource:
    def __init__(self, connections: list[list[str | bytes | Exception]]) -> None:
        self._connections = iter(connections)
        self.urls: list[str] = []

    async def messages(self, url: str) -> AsyncGenerator[str | bytes, None]:
        self.urls.append(url)
        for item in next(self._connections):
            if isinstance(item, Exception):
                raise item
            yield item


class StallingMessageSource:
    async def messages(self, url: str) -> AsyncGenerator[str | bytes, None]:
        del url
        await asyncio.Event().wait()
        if False:
            yield ""


async def no_sleep(_: float) -> None:
    return None


def test_parse_aggregate_trade_preserves_decimal_and_aggressor_side() -> None:
    sell = parse_market_message(
        aggregate_trade(buyer_is_maker=True),
        expected_symbol="BTCUSDT",
        expected_kind=MarketStreamKind.AGGREGATE_TRADE,
    )
    buy = parse_market_message(aggregate_trade(buyer_is_maker=False))

    assert isinstance(sell, AggregateTrade)
    assert sell.price == Decimal("65000.10")
    assert sell.quantity == Decimal("0.002")
    assert sell.aggressor_side is OrderSide.SELL
    assert isinstance(buy, AggregateTrade)
    assert buy.aggressor_side is OrderSide.BUY


def test_parse_mark_price_keeps_funding_as_signed_decimal() -> None:
    event = parse_market_message(mark_price())

    assert isinstance(event, MarkPrice)
    assert event.mark_price == Decimal("65001.20")
    assert event.funding_rate == Decimal("-0.00010000")


def test_parse_book_ticker_exposes_best_bid_and_ask() -> None:
    event = parse_market_message(book_ticker())

    assert isinstance(event, BookTicker)
    assert event.bid_price == Decimal("65000.10")
    assert event.ask_price == Decimal("65000.20")
    assert event.update_id == 456


def test_market_parser_rejects_wrong_symbol_unknown_event_and_float_price() -> None:
    with pytest.raises(BinanceProtocolError, match="expected ETHUSDT"):
        parse_market_message(aggregate_trade(), expected_symbol="ETHUSDT")

    unknown = json.dumps({"e": "mystery", "s": "BTCUSDT"})
    with pytest.raises(BinanceProtocolError, match="unsupported"):
        parse_market_message(unknown)

    payload = json.loads(aggregate_trade())
    payload["data"]["p"] = 65000.1
    with pytest.raises(BinanceProtocolError, match="decimal string"):
        parse_market_message(json.dumps(payload))


def test_market_stream_builds_current_routed_urls_and_separates_routes() -> None:
    market = BinanceMarketStream(
        "BTCUSDT",
        (MarketStreamKind.AGGREGATE_TRADE, MarketStreamKind.MARK_PRICE),
    )
    public = BinanceMarketStream("BTCUSDT", (MarketStreamKind.BOOK_TICKER,))

    assert market.url.endswith("/market/stream?streams=btcusdt@aggTrade/btcusdt@markPrice@1s")
    assert public.url.endswith("/public/stream?streams=btcusdt@bookTicker")

    with pytest.raises(DomainValidationError, match="separate"):
        BinanceMarketStream(
            "BTCUSDT",
            (MarketStreamKind.AGGREGATE_TRADE, MarketStreamKind.BOOK_TICKER),
        )


def test_stream_reconnects_after_disconnect_and_reports_health() -> None:
    source = ScriptedMessageSource([[aggregate_trade()], [mark_price()]])
    stream = BinanceMarketStream(
        "BTCUSDT",
        (MarketStreamKind.AGGREGATE_TRADE, MarketStreamKind.MARK_PRICE),
        message_source=source,
        monotonic_ms=lambda: 1_000,
        sleeper=no_sleep,
    )

    async def consume() -> tuple[object, object]:
        events = stream.events()
        first = await anext(events)
        second = await anext(events)
        await events.aclose()
        return first, second

    first, second = asyncio.run(consume())

    assert isinstance(first, AggregateTrade)
    assert isinstance(second, MarkPrice)
    assert len(source.urls) == 2
    assert stream.health().state is MarketStreamState.STOPPED
    assert stream.health().reconnect_count == 1


def test_stream_detects_stale_connection_and_bounds_reconnects() -> None:
    config = BinancePublicConfig(
        market_stale_after_seconds=0.01,
        max_reconnect_attempts=0,
    )
    stream = BinanceMarketStream(
        "BTCUSDT",
        (MarketStreamKind.AGGREGATE_TRADE,),
        config,
        message_source=StallingMessageSource(),
        sleeper=no_sleep,
    )

    async def consume() -> None:
        await anext(stream.events())

    with pytest.raises(BinanceTransportError, match="exhausted"):
        asyncio.run(consume())
    health = stream.health()
    assert health.state is MarketStreamState.FAILED
    assert health.stale
    assert health.reconnect_count == 1
    assert health.last_error is not None
    assert "stale-data" in health.last_error


def test_stream_rejects_out_of_order_events_per_kind() -> None:
    source = ScriptedMessageSource(
        [[aggregate_trade(event_time=200), aggregate_trade(event_time=199)]]
    )
    config = BinancePublicConfig(max_reconnect_attempts=0)
    stream = BinanceMarketStream(
        "BTCUSDT",
        (MarketStreamKind.AGGREGATE_TRADE,),
        config,
        message_source=source,
        sleeper=no_sleep,
    )

    async def consume() -> None:
        events = stream.events()
        await anext(events)
        await anext(events)

    with pytest.raises(BinanceTransportError, match="exhausted") as error:
        asyncio.run(consume())
    assert isinstance(error.value.__cause__, BinanceProtocolError)
    assert "moved backwards" in str(error.value.__cause__)
