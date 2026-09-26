"""Strict parsing for authenticated USD-M Futures REST and user-stream payloads."""

import json
from collections.abc import Mapping
from decimal import Decimal

from trading_bot.binance.errors import BinanceProtocolError
from trading_bot.binance.parsing import (
    _as_boolean,
    _as_decimal,
    _as_mapping,
    _as_non_negative_decimal,
    _as_non_negative_int,
    _as_positive_decimal,
    _as_sequence,
    _as_string,
)
from trading_bot.domain import OrderSide
from trading_bot.execution import (
    AccountSnapshot,
    AccountUpdate,
    BalanceSnapshot,
    CommissionRates,
    ExchangeFill,
    ExchangeOrder,
    ExecutionType,
    IncomeRecord,
    ListenKeyExpired,
    OrderStatus,
    OrderTradeUpdate,
    PositionSnapshot,
    UserDataEvent,
    UserStreamNotice,
)

_KNOWN_NOTICE_TYPES = {
    "ACCOUNT_CONFIG_UPDATE",
    "ALGO_UPDATE",
    "CONDITIONAL_ORDER_TRIGGER_REJECT",
    "GRID_UPDATE",
    "MARGIN_CALL",
    "STRATEGY_UPDATE",
    "TRADE_LITE",
}


def _as_order_side(value: object, location: str) -> OrderSide:
    try:
        return OrderSide(_as_string(value, location))
    except ValueError as error:
        raise BinanceProtocolError(f"{location} is not a supported order side") from error


def _as_order_status(value: object, location: str) -> OrderStatus:
    try:
        status = OrderStatus(_as_string(value, location))
    except ValueError as error:
        raise BinanceProtocolError(f"{location} is not a supported order status") from error
    if not status.is_exchange_status:
        raise BinanceProtocolError(f"{location} is not an exchange order status")
    return status


def _as_execution_type(value: object, location: str) -> ExecutionType:
    try:
        return ExecutionType(_as_string(value, location))
    except ValueError as error:
        raise BinanceProtocolError(f"{location} is not a supported execution type") from error


def _optional_non_negative_int(value: object, location: str) -> int | None:
    if value is None:
        return None
    return _as_non_negative_int(value, location)


def _optional_string(value: object, location: str) -> str | None:
    if value is None:
        return None
    return _as_string(value, location)


def parse_exchange_order(payload: object) -> ExchangeOrder:
    """Parse an order response shared by submit/query/cancel/open-order endpoints."""

    item = _as_mapping(payload, "order response")
    update_time = item.get("updateTime", item.get("time"))
    return ExchangeOrder(
        symbol=_as_string(item.get("symbol"), "order.symbol"),
        client_order_id=_as_string(item.get("clientOrderId"), "order.clientOrderId"),
        exchange_order_id=_as_non_negative_int(item.get("orderId"), "order.orderId"),
        side=_as_order_side(item.get("side"), "order.side"),
        status=_as_order_status(item.get("status"), "order.status"),
        price=_as_non_negative_decimal(item.get("price"), "order.price"),
        original_quantity=_as_positive_decimal(item.get("origQty"), "order.origQty"),
        executed_quantity=_as_non_negative_decimal(item.get("executedQty"), "order.executedQty"),
        average_price=_as_non_negative_decimal(item.get("avgPrice"), "order.avgPrice"),
        reduce_only=_as_boolean(item.get("reduceOnly"), "order.reduceOnly"),
        update_time_ms=_as_non_negative_int(update_time, "order.updateTime"),
    )


def parse_exchange_orders(payload: object) -> tuple[ExchangeOrder, ...]:
    values = _as_sequence(payload, "orders response")
    return tuple(parse_exchange_order(item) for item in values)


def parse_account_trades(payload: object) -> tuple[ExchangeFill, ...]:
    values = _as_sequence(payload, "account trades response")
    fills: list[ExchangeFill] = []
    for index, raw in enumerate(values):
        item = _as_mapping(raw, f"trades[{index}]")
        fills.append(
            ExchangeFill(
                symbol=_as_string(item.get("symbol"), f"trades[{index}].symbol"),
                trade_id=_as_non_negative_int(item.get("id"), f"trades[{index}].id"),
                exchange_order_id=_as_non_negative_int(
                    item.get("orderId"), f"trades[{index}].orderId"
                ),
                side=_as_order_side(item.get("side"), f"trades[{index}].side"),
                price=_as_positive_decimal(item.get("price"), f"trades[{index}].price"),
                quantity=_as_positive_decimal(item.get("qty"), f"trades[{index}].qty"),
                commission=_as_non_negative_decimal(
                    item.get("commission"), f"trades[{index}].commission"
                ),
                commission_asset=_as_string(
                    item.get("commissionAsset"), f"trades[{index}].commissionAsset"
                ),
                realized_pnl=_as_decimal(item.get("realizedPnl"), f"trades[{index}].realizedPnl"),
                event_time_ms=_as_non_negative_int(item.get("time"), f"trades[{index}].time"),
                maker=_as_boolean(item.get("maker"), f"trades[{index}].maker"),
            )
        )
    return tuple(fills)


def parse_positions(payload: object) -> tuple[PositionSnapshot, ...]:
    values = _as_sequence(payload, "positions response")
    positions: list[PositionSnapshot] = []
    for index, raw in enumerate(values):
        item = _as_mapping(raw, f"positions[{index}]")
        positions.append(
            PositionSnapshot(
                symbol=_as_string(item.get("symbol"), f"positions[{index}].symbol"),
                position_side=_as_string(
                    item.get("positionSide"), f"positions[{index}].positionSide"
                ),
                quantity=_as_decimal(item.get("positionAmt"), f"positions[{index}].positionAmt"),
                entry_price=_as_non_negative_decimal(
                    item.get("entryPrice"), f"positions[{index}].entryPrice"
                ),
                break_even_price=_as_non_negative_decimal(
                    item.get("breakEvenPrice"), f"positions[{index}].breakEvenPrice"
                ),
                unrealized_pnl=_as_decimal(
                    item.get("unRealizedProfit"), f"positions[{index}].unRealizedProfit"
                ),
                margin_type=_as_string(item.get("marginType"), f"positions[{index}].marginType"),
                isolated_wallet=_as_non_negative_decimal(
                    item.get("isolatedWallet"), f"positions[{index}].isolatedWallet"
                ),
                update_time_ms=_as_non_negative_int(
                    item.get("updateTime"), f"positions[{index}].updateTime"
                ),
            )
        )
    return tuple(positions)


def parse_account_snapshot(payload: object) -> AccountSnapshot:
    root = _as_mapping(payload, "account response")
    raw_assets = _as_sequence(root.get("assets"), "account.assets")
    balances: list[BalanceSnapshot] = []
    observed_update_times: list[int] = []
    for index, raw in enumerate(raw_assets):
        item = _as_mapping(raw, f"account.assets[{index}]")
        observed_update_times.append(
            _as_non_negative_int(item.get("updateTime", 0), f"account.assets[{index}].updateTime")
        )
        balances.append(
            BalanceSnapshot(
                asset=_as_string(item.get("asset"), f"account.assets[{index}].asset"),
                wallet_balance=_as_decimal(
                    item.get("walletBalance"), f"account.assets[{index}].walletBalance"
                ),
                cross_wallet_balance=_as_decimal(
                    item.get("crossWalletBalance"),
                    f"account.assets[{index}].crossWalletBalance",
                ),
                balance_change=Decimal("0"),
            )
        )
    raw_positions = _as_sequence(root.get("positions", ()), "account.positions")
    for index, raw in enumerate(raw_positions):
        item = _as_mapping(raw, f"account.positions[{index}]")
        observed_update_times.append(
            _as_non_negative_int(
                item.get("updateTime", 0), f"account.positions[{index}].updateTime"
            )
        )
    root_update_time = root.get("updateTime")
    update_time_ms = (
        max(observed_update_times, default=0)
        if root_update_time is None
        else _as_non_negative_int(root_update_time, "account.updateTime")
    )
    return AccountSnapshot(
        total_wallet_balance=_as_decimal(
            root.get("totalWalletBalance"), "account.totalWalletBalance"
        ),
        total_unrealized_profit=_as_decimal(
            root.get("totalUnrealizedProfit"), "account.totalUnrealizedProfit"
        ),
        total_margin_balance=_as_decimal(
            root.get("totalMarginBalance"), "account.totalMarginBalance"
        ),
        available_balance=_as_decimal(root.get("availableBalance"), "account.availableBalance"),
        update_time_ms=update_time_ms,
        balances=tuple(balances),
    )


def parse_commission_rates(payload: object) -> CommissionRates:
    root = _as_mapping(payload, "commission response")
    return CommissionRates(
        symbol=_as_string(root.get("symbol"), "commission.symbol"),
        maker_rate=_as_non_negative_decimal(
            root.get("makerCommissionRate"), "commission.makerCommissionRate"
        ),
        taker_rate=_as_non_negative_decimal(
            root.get("takerCommissionRate"), "commission.takerCommissionRate"
        ),
    )


def parse_income_records(payload: object) -> tuple[IncomeRecord, ...]:
    values = _as_sequence(payload, "income response")
    records: list[IncomeRecord] = []
    for index, raw in enumerate(values):
        item = _as_mapping(raw, f"income[{index}]")
        records.append(
            IncomeRecord(
                symbol=_as_string(item.get("symbol"), f"income[{index}].symbol"),
                income_type=_as_string(item.get("incomeType"), f"income[{index}].incomeType"),
                transaction_id=_as_non_negative_int(item.get("tranId"), f"income[{index}].tranId"),
                asset=_as_string(item.get("asset"), f"income[{index}].asset"),
                amount=_as_decimal(item.get("income"), f"income[{index}].income"),
                event_time_ms=_as_non_negative_int(item.get("time"), f"income[{index}].time"),
            )
        )
    return tuple(records)


def parse_listen_key(payload: object) -> str:
    root = _as_mapping(payload, "listen key response")
    return _as_string(root.get("listenKey"), "listenKey")


def _parse_stream_order(root: Mapping[str, object]) -> OrderTradeUpdate:
    event_time = _as_non_negative_int(root.get("E"), "ORDER_TRADE_UPDATE.E")
    transaction_time = _as_non_negative_int(root.get("T"), "ORDER_TRADE_UPDATE.T")
    item = _as_mapping(root.get("o"), "ORDER_TRADE_UPDATE.o")
    execution_type = _as_execution_type(item.get("x"), "ORDER_TRADE_UPDATE.o.x")
    last_quantity = _as_non_negative_decimal(item.get("l"), "ORDER_TRADE_UPDATE.o.l")
    commission_asset = _optional_string(item.get("N"), "ORDER_TRADE_UPDATE.o.N")
    raw_commission = item.get("n")
    commission = (
        Decimal("0")
        if raw_commission is None
        else _as_non_negative_decimal(raw_commission, "ORDER_TRADE_UPDATE.o.n")
    )
    trade_id = _optional_non_negative_int(item.get("t"), "ORDER_TRADE_UPDATE.o.t")
    if execution_type is not ExecutionType.TRADE:
        trade_id = None
        commission_asset = None
    order = ExchangeOrder(
        symbol=_as_string(item.get("s"), "ORDER_TRADE_UPDATE.o.s"),
        client_order_id=_as_string(item.get("c"), "ORDER_TRADE_UPDATE.o.c"),
        exchange_order_id=_as_non_negative_int(item.get("i"), "ORDER_TRADE_UPDATE.o.i"),
        side=_as_order_side(item.get("S"), "ORDER_TRADE_UPDATE.o.S"),
        status=_as_order_status(item.get("X"), "ORDER_TRADE_UPDATE.o.X"),
        price=_as_non_negative_decimal(item.get("p"), "ORDER_TRADE_UPDATE.o.p"),
        original_quantity=_as_positive_decimal(item.get("q"), "ORDER_TRADE_UPDATE.o.q"),
        executed_quantity=_as_non_negative_decimal(item.get("z"), "ORDER_TRADE_UPDATE.o.z"),
        average_price=_as_non_negative_decimal(item.get("ap"), "ORDER_TRADE_UPDATE.o.ap"),
        reduce_only=_as_boolean(item.get("R"), "ORDER_TRADE_UPDATE.o.R"),
        update_time_ms=_as_non_negative_int(item.get("T"), "ORDER_TRADE_UPDATE.o.T"),
    )
    return OrderTradeUpdate(
        event_time_ms=event_time,
        transaction_time_ms=transaction_time,
        execution_type=execution_type,
        order=order,
        last_filled_quantity=last_quantity,
        last_filled_price=_as_non_negative_decimal(item.get("L"), "ORDER_TRADE_UPDATE.o.L"),
        trade_id=trade_id,
        commission=commission,
        commission_asset=commission_asset,
        realized_pnl=_as_decimal(item.get("rp"), "ORDER_TRADE_UPDATE.o.rp"),
        maker=_as_boolean(item.get("m"), "ORDER_TRADE_UPDATE.o.m"),
    )


def _parse_stream_account(root: Mapping[str, object]) -> AccountUpdate:
    event_time = _as_non_negative_int(root.get("E"), "ACCOUNT_UPDATE.E")
    transaction_time = _as_non_negative_int(root.get("T"), "ACCOUNT_UPDATE.T")
    account = _as_mapping(root.get("a"), "ACCOUNT_UPDATE.a")
    raw_balances = _as_sequence(account.get("B"), "ACCOUNT_UPDATE.a.B")
    balances: list[BalanceSnapshot] = []
    for index, raw in enumerate(raw_balances):
        item = _as_mapping(raw, f"ACCOUNT_UPDATE.a.B[{index}]")
        balances.append(
            BalanceSnapshot(
                asset=_as_string(item.get("a"), f"ACCOUNT_UPDATE.a.B[{index}].a"),
                wallet_balance=_as_decimal(item.get("wb"), f"ACCOUNT_UPDATE.a.B[{index}].wb"),
                cross_wallet_balance=_as_decimal(item.get("cw"), f"ACCOUNT_UPDATE.a.B[{index}].cw"),
                balance_change=_as_decimal(item.get("bc"), f"ACCOUNT_UPDATE.a.B[{index}].bc"),
            )
        )
    raw_positions = _as_sequence(account.get("P"), "ACCOUNT_UPDATE.a.P")
    positions: list[PositionSnapshot] = []
    for index, raw in enumerate(raw_positions):
        item = _as_mapping(raw, f"ACCOUNT_UPDATE.a.P[{index}]")
        positions.append(
            PositionSnapshot(
                symbol=_as_string(item.get("s"), f"ACCOUNT_UPDATE.a.P[{index}].s"),
                position_side=_as_string(item.get("ps"), f"ACCOUNT_UPDATE.a.P[{index}].ps"),
                quantity=_as_decimal(item.get("pa"), f"ACCOUNT_UPDATE.a.P[{index}].pa"),
                entry_price=_as_non_negative_decimal(
                    item.get("ep"), f"ACCOUNT_UPDATE.a.P[{index}].ep"
                ),
                break_even_price=_as_non_negative_decimal(
                    item.get("bep", "0"), f"ACCOUNT_UPDATE.a.P[{index}].bep"
                ),
                unrealized_pnl=_as_decimal(item.get("up"), f"ACCOUNT_UPDATE.a.P[{index}].up"),
                margin_type=_as_string(item.get("mt"), f"ACCOUNT_UPDATE.a.P[{index}].mt"),
                isolated_wallet=_as_non_negative_decimal(
                    item.get("iw"), f"ACCOUNT_UPDATE.a.P[{index}].iw"
                ),
                update_time_ms=transaction_time,
            )
        )
    return AccountUpdate(
        event_time_ms=event_time,
        transaction_time_ms=transaction_time,
        reason=_as_string(account.get("m"), "ACCOUNT_UPDATE.a.m"),
        balances=tuple(balances),
        positions=tuple(positions),
    )


def parse_user_data_message(message: str | bytes) -> UserDataEvent:
    """Parse a private stream event while rejecting unknown schemas and floats."""

    try:
        decoded = json.loads(message)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise BinanceProtocolError("user stream message is not valid JSON") from error
    root = _as_mapping(decoded, "user stream message")
    event_type = _as_string(root.get("e"), "user stream event type")
    if event_type == "ORDER_TRADE_UPDATE":
        return _parse_stream_order(root)
    if event_type == "ACCOUNT_UPDATE":
        return _parse_stream_account(root)
    if event_type == "listenKeyExpired":
        return ListenKeyExpired(
            event_time_ms=_as_non_negative_int(root.get("E"), "listenKeyExpired.E"),
            listen_key=_as_string(root.get("listenKey"), "listenKeyExpired.listenKey"),
        )
    if event_type in _KNOWN_NOTICE_TYPES:
        return UserStreamNotice(
            event_type=event_type,
            event_time_ms=_as_non_negative_int(root.get("E"), f"{event_type}.E"),
            transaction_time_ms=_optional_non_negative_int(root.get("T"), f"{event_type}.T"),
        )
    raise BinanceProtocolError(f"unsupported user stream event type {event_type}")
