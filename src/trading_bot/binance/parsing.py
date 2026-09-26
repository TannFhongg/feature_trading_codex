"""Strict parsing for public USD-M Futures REST payloads."""

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from trading_bot.binance.errors import BinanceProtocolError
from trading_bot.binance.models import (
    AggregateTrade,
    BookTicker,
    MarketEvent,
    MarketStreamKind,
    MarkPrice,
)
from trading_bot.domain import OrderSide, SymbolRules


def _as_mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BinanceProtocolError(f"{location} must be a JSON object")
    return value


def _as_sequence(value: object, location: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise BinanceProtocolError(f"{location} must be a JSON array")
    return value


def _as_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise BinanceProtocolError(f"{location} must be a non-empty string")
    return value


def _as_non_negative_int(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BinanceProtocolError(f"{location} must be a non-negative integer")
    return value


def _as_positive_decimal(value: object, location: str) -> Decimal:
    parsed = _as_decimal(value, location)
    if parsed <= 0:
        raise BinanceProtocolError(f"{location} must be greater than zero")
    return parsed


def _as_non_negative_decimal(value: object, location: str) -> Decimal:
    parsed = _as_decimal(value, location)
    if parsed < 0:
        raise BinanceProtocolError(f"{location} must not be negative")
    return parsed


def _as_decimal(value: object, location: str) -> Decimal:
    if not isinstance(value, str):
        raise BinanceProtocolError(f"{location} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise BinanceProtocolError(f"{location} is not a valid decimal") from error
    if not parsed.is_finite():
        raise BinanceProtocolError(f"{location} must be finite")
    return parsed


def _as_boolean(value: object, location: str) -> bool:
    if not isinstance(value, bool):
        raise BinanceProtocolError(f"{location} must be a boolean")
    return value


def parse_server_time(payload: object) -> int:
    """Parse ``GET /fapi/v1/time`` without accepting booleans or floats."""

    root = _as_mapping(payload, "server time response")
    return _as_non_negative_int(root.get("serverTime"), "serverTime")


def parse_symbol_rules(payload: object, symbol: str) -> SymbolRules:
    """Extract active USDT perpetual filters required by the grid domain."""

    if not symbol or symbol != symbol.strip().upper():
        raise BinanceProtocolError("symbol must be non-empty, trimmed, and uppercase")

    root = _as_mapping(payload, "exchangeInfo response")
    symbols = _as_sequence(root.get("symbols"), "symbols")
    matching: Mapping[str, object] | None = None
    for index, raw_symbol in enumerate(symbols):
        item = _as_mapping(raw_symbol, f"symbols[{index}]")
        if item.get("symbol") == symbol:
            matching = item
            break
    if matching is None:
        raise BinanceProtocolError(f"symbol {symbol} is absent from exchangeInfo")

    status = _as_string(matching.get("status"), f"{symbol}.status")
    contract_type = _as_string(matching.get("contractType"), f"{symbol}.contractType")
    quote_asset = _as_string(matching.get("quoteAsset"), f"{symbol}.quoteAsset")
    margin_asset = _as_string(matching.get("marginAsset"), f"{symbol}.marginAsset")
    if status != "TRADING":
        raise BinanceProtocolError(f"symbol {symbol} is not in TRADING status")
    if contract_type != "PERPETUAL":
        raise BinanceProtocolError(f"symbol {symbol} is not a perpetual contract")
    if quote_asset != "USDT" or margin_asset != "USDT":
        raise BinanceProtocolError(f"symbol {symbol} is not a USDT-margined contract")

    raw_filters = _as_sequence(matching.get("filters"), f"{symbol}.filters")
    filters: dict[str, Mapping[str, object]] = {}
    for index, raw_filter in enumerate(raw_filters):
        item = _as_mapping(raw_filter, f"{symbol}.filters[{index}]")
        filter_type = _as_string(item.get("filterType"), f"{symbol}.filters[{index}].filterType")
        filters[filter_type] = item

    try:
        price_filter = filters["PRICE_FILTER"]
        lot_size_filter = filters["LOT_SIZE"]
        notional_filter = filters["MIN_NOTIONAL"]
    except KeyError as error:
        raise BinanceProtocolError(
            f"symbol {symbol} is missing required filter {error.args[0]}"
        ) from error

    return SymbolRules(
        symbol=symbol,
        tick_size=_as_positive_decimal(price_filter.get("tickSize"), "PRICE_FILTER.tickSize"),
        step_size=_as_positive_decimal(lot_size_filter.get("stepSize"), "LOT_SIZE.stepSize"),
        min_qty=_as_positive_decimal(lot_size_filter.get("minQty"), "LOT_SIZE.minQty"),
        min_notional=_as_positive_decimal(notional_filter.get("notional"), "MIN_NOTIONAL.notional"),
    )


def parse_market_message(
    message: str | bytes,
    *,
    expected_symbol: str | None = None,
    expected_kind: MarketStreamKind | None = None,
) -> MarketEvent:
    """Parse one raw or combined WebSocket message into an immutable event."""

    try:
        decoded = json.loads(message)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise BinanceProtocolError("market stream message is not valid JSON") from error

    root = _as_mapping(decoded, "market stream message")
    payload: Mapping[str, object]
    if "data" in root:
        _as_string(root.get("stream"), "stream")
        payload = _as_mapping(root.get("data"), "data")
    else:
        payload = root

    event_type = _as_string(payload.get("e"), "event type")
    event_symbol = _as_string(payload.get("s"), "symbol")
    if expected_symbol is not None and event_symbol != expected_symbol:
        raise BinanceProtocolError(f"received symbol {event_symbol}, expected {expected_symbol}")

    event: MarketEvent
    actual_kind: MarketStreamKind
    if event_type == "aggTrade":
        buyer_is_maker = _as_boolean(payload.get("m"), "aggTrade.m")
        event = AggregateTrade(
            event_time_ms=_as_non_negative_int(payload.get("E"), "aggTrade.E"),
            trade_time_ms=_as_non_negative_int(payload.get("T"), "aggTrade.T"),
            aggregate_trade_id=_as_non_negative_int(payload.get("a"), "aggTrade.a"),
            symbol=event_symbol,
            price=_as_positive_decimal(payload.get("p"), "aggTrade.p"),
            quantity=_as_positive_decimal(payload.get("q"), "aggTrade.q"),
            aggressor_side=OrderSide.SELL if buyer_is_maker else OrderSide.BUY,
        )
        actual_kind = MarketStreamKind.AGGREGATE_TRADE
    elif event_type == "markPriceUpdate":
        event = MarkPrice(
            event_time_ms=_as_non_negative_int(payload.get("E"), "markPriceUpdate.E"),
            symbol=event_symbol,
            mark_price=_as_positive_decimal(payload.get("p"), "markPriceUpdate.p"),
            index_price=_as_positive_decimal(payload.get("i"), "markPriceUpdate.i"),
            estimated_settle_price=_as_positive_decimal(payload.get("P"), "markPriceUpdate.P"),
            funding_rate=_as_decimal(payload.get("r"), "markPriceUpdate.r"),
            next_funding_time_ms=_as_non_negative_int(payload.get("T"), "markPriceUpdate.T"),
        )
        actual_kind = MarketStreamKind.MARK_PRICE
    elif event_type == "bookTicker":
        event = BookTicker(
            event_time_ms=_as_non_negative_int(payload.get("E"), "bookTicker.E"),
            transaction_time_ms=_as_non_negative_int(payload.get("T"), "bookTicker.T"),
            update_id=_as_non_negative_int(payload.get("u"), "bookTicker.u"),
            symbol=event_symbol,
            bid_price=_as_positive_decimal(payload.get("b"), "bookTicker.b"),
            bid_quantity=_as_non_negative_decimal(payload.get("B"), "bookTicker.B"),
            ask_price=_as_positive_decimal(payload.get("a"), "bookTicker.a"),
            ask_quantity=_as_non_negative_decimal(payload.get("A"), "bookTicker.A"),
        )
        actual_kind = MarketStreamKind.BOOK_TICKER
    else:
        raise BinanceProtocolError(f"unsupported market event type {event_type}")

    if expected_kind is not None and actual_kind is not expected_kind:
        raise BinanceProtocolError(
            f"received {actual_kind.value} event, expected {expected_kind.value}"
        )
    return event
