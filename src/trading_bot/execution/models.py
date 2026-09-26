"""Exchange-independent execution, account, and reconciliation records."""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from trading_bot.domain import DomainValidationError, OrderSide

_CLIENT_ORDER_ID_PATTERN = re.compile(r"^[.A-Z:/a-z0-9_-]{1,36}$")
_STRATEGY_SLUG_PATTERN = re.compile(r"[^a-z0-9_-]+")


def _require_decimal(
    name: str,
    value: object,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise DomainValidationError(f"{name} must be finite")
    if positive and value <= 0:
        raise DomainValidationError(f"{name} must be greater than zero")
    if non_negative and value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


def _require_non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int")
    if value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


def _require_non_empty(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")
    if not value or value != value.strip():
        raise DomainValidationError(f"{name} must be non-empty and trimmed")
    return value


def _validate_symbol(symbol: str) -> None:
    _require_non_empty("symbol", symbol)
    if symbol != symbol.upper() or not symbol.replace("_", "").isalnum():
        raise DomainValidationError(
            "symbol must be uppercase and contain only letters, numbers, and underscores"
        )


def _validate_client_order_id(client_order_id: str) -> None:
    if not isinstance(client_order_id, str):
        raise TypeError("client_order_id must be str")
    if _CLIENT_ORDER_ID_PATTERN.fullmatch(client_order_id) is None:
        raise DomainValidationError("client_order_id must be 1-36 Binance-supported characters")


def deterministic_client_order_id(
    strategy_id: str,
    level_index: int,
    side: OrderSide,
    cycle: int,
) -> str:
    """Build a stable Binance client order ID from one logical grid order key."""

    strategy_id = _require_non_empty("strategy_id", strategy_id)
    _require_non_negative_int("level_index", level_index)
    _require_non_negative_int("cycle", cycle)
    if not isinstance(side, OrderSide):
        raise TypeError("side must be OrderSide")

    slug = _STRATEGY_SLUG_PATTERN.sub("-", strategy_id.lower()).strip("-_")
    if not slug:
        raise DomainValidationError("strategy_id must contain at least one letter or number")
    if strategy_id != slug:
        strategy_digest = sha256(strategy_id.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug}-{strategy_digest}"
    expanded = f"grid-{slug}-{level_index}-{side.value.lower()}-{cycle}"
    if len(expanded) <= 36:
        return expanded

    digest = sha256(expanded.encode("utf-8")).hexdigest()[:27]
    return f"grid-{side.value[0].lower()}-{digest}"


class OrderStatus(StrEnum):
    """Local and exchange order lifecycle states persisted by the P4 ledger."""

    PENDING_SUBMIT = "PENDING_SUBMIT"
    UNKNOWN = "UNKNOWN"
    SUBMISSION_REJECTED = "SUBMISSION_REJECTED"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"
    REJECTED = "REJECTED"

    @property
    def is_active(self) -> bool:
        return self in {
            OrderStatus.PENDING_SUBMIT,
            OrderStatus.UNKNOWN,
            OrderStatus.NEW,
            OrderStatus.PARTIALLY_FILLED,
        }

    @property
    def is_exchange_status(self) -> bool:
        return self not in {
            OrderStatus.PENDING_SUBMIT,
            OrderStatus.UNKNOWN,
            OrderStatus.SUBMISSION_REJECTED,
        }


class ExecutionType(StrEnum):
    """Binance order-event execution types represented without adapter coupling."""

    NEW = "NEW"
    CANCELED = "CANCELED"
    CALCULATED = "CALCULATED"
    EXPIRED = "EXPIRED"
    TRADE = "TRADE"
    AMENDMENT = "AMENDMENT"


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """A validated limit-order request emitted by an execution caller."""

    strategy_id: str
    level_index: int
    cycle: int
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: Decimal
    reduce_only: bool = False

    def __post_init__(self) -> None:
        _require_non_empty("strategy_id", self.strategy_id)
        _require_non_negative_int("level_index", self.level_index)
        _require_non_negative_int("cycle", self.cycle)
        _validate_symbol(self.symbol)
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be OrderSide")
        _require_decimal("price", self.price, positive=True)
        _require_decimal("quantity", self.quantity, positive=True)
        if not isinstance(self.reduce_only, bool):
            raise TypeError("reduce_only must be bool")

    @property
    def client_order_id(self) -> str:
        return deterministic_client_order_id(
            self.strategy_id,
            self.level_index,
            self.side,
            self.cycle,
        )


@dataclass(frozen=True, slots=True)
class ExchangeOrder:
    """Normalized exchange order returned by REST or reconciliation."""

    symbol: str
    client_order_id: str
    exchange_order_id: int
    side: OrderSide
    status: OrderStatus
    price: Decimal
    original_quantity: Decimal
    executed_quantity: Decimal
    average_price: Decimal
    reduce_only: bool
    update_time_ms: int

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _validate_client_order_id(self.client_order_id)
        _require_non_negative_int("exchange_order_id", self.exchange_order_id)
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be OrderSide")
        if not isinstance(self.status, OrderStatus) or not self.status.is_exchange_status:
            raise TypeError("status must be an exchange OrderStatus")
        _require_decimal("price", self.price, non_negative=True)
        original = _require_decimal("original_quantity", self.original_quantity, positive=True)
        executed = _require_decimal("executed_quantity", self.executed_quantity, non_negative=True)
        _require_decimal("average_price", self.average_price, non_negative=True)
        if executed > original:
            raise DomainValidationError("executed_quantity must not exceed original_quantity")
        if not isinstance(self.reduce_only, bool):
            raise TypeError("reduce_only must be bool")
        _require_non_negative_int("update_time_ms", self.update_time_ms)


@dataclass(frozen=True, slots=True)
class ExchangeFill:
    """Normalized account trade uniquely identified within one symbol."""

    symbol: str
    trade_id: int
    exchange_order_id: int
    side: OrderSide
    price: Decimal
    quantity: Decimal
    commission: Decimal
    commission_asset: str
    realized_pnl: Decimal
    event_time_ms: int
    maker: bool

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _require_non_negative_int("trade_id", self.trade_id)
        _require_non_negative_int("exchange_order_id", self.exchange_order_id)
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be OrderSide")
        _require_decimal("price", self.price, positive=True)
        _require_decimal("quantity", self.quantity, positive=True)
        _require_decimal("commission", self.commission, non_negative=True)
        _require_non_empty("commission_asset", self.commission_asset)
        _require_decimal("realized_pnl", self.realized_pnl)
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        if not isinstance(self.maker, bool):
            raise TypeError("maker must be bool")


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """One-way or hedge-side position state from REST or User Data Stream."""

    symbol: str
    position_side: str
    quantity: Decimal
    entry_price: Decimal
    break_even_price: Decimal
    unrealized_pnl: Decimal
    margin_type: str
    isolated_wallet: Decimal
    update_time_ms: int

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _require_non_empty("position_side", self.position_side)
        _require_decimal("quantity", self.quantity)
        _require_decimal("entry_price", self.entry_price, non_negative=True)
        _require_decimal("break_even_price", self.break_even_price, non_negative=True)
        _require_decimal("unrealized_pnl", self.unrealized_pnl)
        _require_non_empty("margin_type", self.margin_type)
        _require_decimal("isolated_wallet", self.isolated_wallet, non_negative=True)
        _require_non_negative_int("update_time_ms", self.update_time_ms)


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    """One asset balance from a private account snapshot or account update."""

    asset: str
    wallet_balance: Decimal
    cross_wallet_balance: Decimal
    balance_change: Decimal

    def __post_init__(self) -> None:
        asset = _require_non_empty("asset", self.asset)
        if asset != asset.upper() or not asset.isalnum():
            raise DomainValidationError("asset must be uppercase alphanumeric")
        _require_decimal("wallet_balance", self.wallet_balance)
        _require_decimal("cross_wallet_balance", self.cross_wallet_balance)
        _require_decimal("balance_change", self.balance_change)


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Minimal account health totals needed by later risk controls."""

    total_wallet_balance: Decimal
    total_unrealized_profit: Decimal
    total_margin_balance: Decimal
    available_balance: Decimal
    update_time_ms: int
    balances: tuple[BalanceSnapshot, ...]

    def __post_init__(self) -> None:
        _require_decimal("total_wallet_balance", self.total_wallet_balance)
        _require_decimal("total_unrealized_profit", self.total_unrealized_profit)
        _require_decimal("total_margin_balance", self.total_margin_balance)
        _require_decimal("available_balance", self.available_balance)
        _require_non_negative_int("update_time_ms", self.update_time_ms)
        if any(not isinstance(balance, BalanceSnapshot) for balance in self.balances):
            raise TypeError("balances must contain BalanceSnapshot values")


@dataclass(frozen=True, slots=True)
class CommissionRates:
    """Account-specific maker/taker commission rates for one symbol."""

    symbol: str
    maker_rate: Decimal
    taker_rate: Decimal

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        maker = _require_decimal("maker_rate", self.maker_rate, non_negative=True)
        taker = _require_decimal("taker_rate", self.taker_rate, non_negative=True)
        if maker >= 1 or taker >= 1:
            raise DomainValidationError("commission rates must be less than one")


@dataclass(frozen=True, slots=True)
class IncomeRecord:
    """Funding or other account income entry returned by Binance."""

    symbol: str
    income_type: str
    transaction_id: int
    asset: str
    amount: Decimal
    event_time_ms: int

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _require_non_empty("income_type", self.income_type)
        _require_non_negative_int("transaction_id", self.transaction_id)
        _require_non_empty("asset", self.asset)
        _require_decimal("amount", self.amount)
        _require_non_negative_int("event_time_ms", self.event_time_ms)


@dataclass(frozen=True, slots=True)
class OrderTradeUpdate:
    """Normalized real-time order update, including an optional last fill."""

    event_time_ms: int
    transaction_time_ms: int
    execution_type: ExecutionType
    order: ExchangeOrder
    last_filled_quantity: Decimal
    last_filled_price: Decimal
    trade_id: int | None
    commission: Decimal
    commission_asset: str | None
    realized_pnl: Decimal
    maker: bool

    def __post_init__(self) -> None:
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        _require_non_negative_int("transaction_time_ms", self.transaction_time_ms)
        if not isinstance(self.execution_type, ExecutionType):
            raise TypeError("execution_type must be ExecutionType")
        if not isinstance(self.order, ExchangeOrder):
            raise TypeError("order must be ExchangeOrder")
        last_quantity = _require_decimal(
            "last_filled_quantity", self.last_filled_quantity, non_negative=True
        )
        _require_decimal("last_filled_price", self.last_filled_price, non_negative=True)
        _require_decimal("commission", self.commission, non_negative=True)
        _require_decimal("realized_pnl", self.realized_pnl)
        if self.trade_id is not None:
            _require_non_negative_int("trade_id", self.trade_id)
        if self.commission_asset is not None:
            _require_non_empty("commission_asset", self.commission_asset)
        if not isinstance(self.maker, bool):
            raise TypeError("maker must be bool")
        if self.execution_type is ExecutionType.TRADE:
            if last_quantity <= 0 or self.trade_id is None or self.commission_asset is None:
                raise DomainValidationError(
                    "TRADE update requires fill quantity, trade_id, and commission_asset"
                )
        elif last_quantity != 0:
            raise DomainValidationError("non-TRADE update must not carry a last fill quantity")

    @property
    def event_id(self) -> str:
        values = (
            self.order.symbol,
            str(self.order.exchange_order_id),
            str(self.trade_id),
            self.execution_type.value,
            self.order.status.value,
            str(self.event_time_ms),
            str(self.transaction_time_ms),
            str(self.order.executed_quantity),
        )
        return f"order:{sha256('|'.join(values).encode('utf-8')).hexdigest()}"

    @property
    def fill(self) -> ExchangeFill | None:
        if self.execution_type is not ExecutionType.TRADE:
            return None
        if self.trade_id is None or self.commission_asset is None:
            raise AssertionError("validated TRADE update is missing fill fields")
        return ExchangeFill(
            symbol=self.order.symbol,
            trade_id=self.trade_id,
            exchange_order_id=self.order.exchange_order_id,
            side=self.order.side,
            price=self.last_filled_price,
            quantity=self.last_filled_quantity,
            commission=self.commission,
            commission_asset=self.commission_asset,
            realized_pnl=self.realized_pnl,
            event_time_ms=self.transaction_time_ms,
            maker=self.maker,
        )


@dataclass(frozen=True, slots=True)
class AccountUpdate:
    """Balance/position update from the authenticated user stream."""

    event_time_ms: int
    transaction_time_ms: int
    reason: str
    balances: tuple[BalanceSnapshot, ...]
    positions: tuple[PositionSnapshot, ...]

    def __post_init__(self) -> None:
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        _require_non_negative_int("transaction_time_ms", self.transaction_time_ms)
        _require_non_empty("reason", self.reason)
        if any(not isinstance(balance, BalanceSnapshot) for balance in self.balances):
            raise TypeError("balances must contain BalanceSnapshot values")
        if any(not isinstance(position, PositionSnapshot) for position in self.positions):
            raise TypeError("positions must contain PositionSnapshot values")

    @property
    def event_id(self) -> str:
        key = f"{self.reason}|{self.event_time_ms}|{self.transaction_time_ms}"
        return f"account:{sha256(key.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ListenKeyExpired:
    """Terminal event for an expired User Data Stream listen key."""

    event_time_ms: int
    listen_key: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        _require_non_empty("listen_key", self.listen_key)


@dataclass(frozen=True, slots=True)
class UserStreamNotice:
    """Known non-ledger user event retained as an auditable typed notice."""

    event_type: str
    event_time_ms: int
    transaction_time_ms: int | None

    def __post_init__(self) -> None:
        _require_non_empty("event_type", self.event_type)
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        if self.transaction_time_ms is not None:
            _require_non_negative_int("transaction_time_ms", self.transaction_time_ms)


UserDataEvent = OrderTradeUpdate | AccountUpdate | ListenKeyExpired | UserStreamNotice


@dataclass(frozen=True, slots=True)
class OrderRecord:
    """Persistent local order view with logical-grid ownership metadata."""

    client_order_id: str
    strategy_id: str | None
    level_index: int | None
    cycle: int | None
    exchange_order_id: int | None
    symbol: str
    side: OrderSide
    status: OrderStatus
    price: Decimal
    original_quantity: Decimal
    executed_quantity: Decimal
    average_price: Decimal
    reduce_only: bool
    last_event_time_ms: int


@dataclass(frozen=True, slots=True)
class LedgerApplyResult:
    """Outcome of applying one idempotent exchange event."""

    duplicate_event: bool
    order_updated: bool
    fill_inserted: bool


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Auditable differences and insert counts from one reconciliation pass."""

    symbol: str
    started_at_ms: int
    completed_at_ms: int
    orphan_client_order_ids: tuple[str, ...]
    unresolved_local_client_order_ids: tuple[str, ...]
    quantity_mismatch_client_order_ids: tuple[str, ...]
    position_mismatch_keys: tuple[str, ...]
    inserted_fills: int
    inserted_income_records: int

    @property
    def safe_to_resume(self) -> bool:
        return not (
            self.orphan_client_order_ids
            or self.unresolved_local_client_order_ids
            or self.quantity_mismatch_client_order_ids
            or self.position_mismatch_keys
        )
