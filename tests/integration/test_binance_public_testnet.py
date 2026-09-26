import asyncio
import os

import pytest

from trading_bot.binance import (
    AggregateTrade,
    BinanceMarketStream,
    BinancePublicRestClient,
    BookTicker,
    MarketEvent,
    MarketStreamKind,
    MarkPrice,
    TimeSync,
)
from trading_bot.domain import SymbolRules

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_BINANCE_TESTNET") != "1",
        reason="set RUN_BINANCE_TESTNET=1 to run public Testnet smoke tests",
    ),
]


async def _next_testnet_event(kind: MarketStreamKind) -> MarketEvent:
    stream = BinanceMarketStream("BTCUSDT", (kind,))
    events = stream.events()
    try:
        async with asyncio.timeout(30):
            return await anext(events)
    finally:
        await events.aclose()


def test_testnet_rest_time_sync_and_exchange_rules() -> None:
    async def scenario() -> tuple[TimeSync, SymbolRules]:
        async with BinancePublicRestClient() as client:
            synchronization = await client.synchronize_time(samples=3)
            rules = await client.fetch_symbol_rules("BTCUSDT")
            return synchronization, rules

    synchronization, rules = asyncio.run(scenario())

    assert synchronization.round_trip_ms < 10_000
    assert abs(synchronization.offset_ms) < 10_000
    assert rules.symbol == "BTCUSDT"
    assert rules.tick_size > 0
    assert rules.step_size > 0
    assert rules.min_qty > 0
    assert rules.min_notional > 0


def test_testnet_market_route_receives_mark_price_and_aggregate_trade() -> None:
    async def scenario() -> tuple[MarketEvent, MarketEvent]:
        mark, trade = await asyncio.gather(
            _next_testnet_event(MarketStreamKind.MARK_PRICE),
            _next_testnet_event(MarketStreamKind.AGGREGATE_TRADE),
        )
        return mark, trade

    mark, trade = asyncio.run(scenario())

    assert isinstance(mark, MarkPrice)
    assert mark.symbol == "BTCUSDT"
    assert isinstance(trade, AggregateTrade)
    assert trade.symbol == "BTCUSDT"


def test_testnet_public_route_receives_best_bid_and_ask() -> None:
    event = asyncio.run(_next_testnet_event(MarketStreamKind.BOOK_TICKER))

    assert isinstance(event, BookTicker)
    assert event.symbol == "BTCUSDT"
    assert event.bid_price <= event.ask_price
