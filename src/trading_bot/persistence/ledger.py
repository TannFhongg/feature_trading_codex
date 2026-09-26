"""Async SQLite event ledger with transactional idempotency constraints."""

import asyncio
import sqlite3
from collections.abc import Callable, Iterable
from decimal import Decimal
from pathlib import Path
from time import time_ns
from typing import ParamSpec, TypeVar

from trading_bot.domain import OrderSide
from trading_bot.execution import (
    AccountSnapshot,
    AccountUpdate,
    BalanceSnapshot,
    ExchangeFill,
    ExchangeOrder,
    IncomeRecord,
    LedgerApplyResult,
    OrderRecord,
    OrderRequest,
    OrderStatus,
    OrderTradeUpdate,
    PositionSnapshot,
    ReconciliationReport,
)
from trading_bot.persistence.errors import LedgerConflictError, LedgerError
from trading_bot.risk.models import (
    EmergencyActionRecord,
    EmergencyActionStatus,
    RiskAuditEvent,
    RiskEventType,
)

_ACTIVE_STATUSES = (
    OrderStatus.PENDING_SUBMIT,
    OrderStatus.UNKNOWN,
    OrderStatus.NEW,
    OrderStatus.PARTIALLY_FILLED,
)
_TERMINAL_EXCHANGE_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.EXPIRED,
    OrderStatus.EXPIRED_IN_MATCH,
    OrderStatus.REJECTED,
}
_P = ParamSpec("_P")
_T = TypeVar("_T")

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    strategy_id TEXT,
    level_index INTEGER,
    cycle INTEGER,
    exchange_order_id INTEGER UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    price TEXT NOT NULL,
    original_quantity TEXT NOT NULL,
    executed_quantity TEXT NOT NULL,
    average_price TEXT NOT NULL,
    reduce_only INTEGER NOT NULL CHECK (reduce_only IN (0, 1)),
    last_event_time_ms INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_logical_order
ON orders(strategy_id, level_index, side)
WHERE strategy_id IS NOT NULL
  AND status IN ('PENDING_SUBMIT', 'UNKNOWN', 'NEW', 'PARTIALLY_FILLED');

CREATE TABLE IF NOT EXISTS fills (
    symbol TEXT NOT NULL,
    trade_id INTEGER NOT NULL,
    exchange_order_id INTEGER NOT NULL,
    client_order_id TEXT,
    side TEXT NOT NULL,
    price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    commission TEXT NOT NULL,
    commission_asset TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    event_time_ms INTEGER NOT NULL,
    maker INTEGER NOT NULL CHECK (maker IN (0, 1)),
    PRIMARY KEY (symbol, trade_id),
    FOREIGN KEY (client_order_id) REFERENCES orders(client_order_id)
);

CREATE TABLE IF NOT EXISTS exchange_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_time_ms INTEGER NOT NULL,
    transaction_time_ms INTEGER NOT NULL,
    processed_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT NOT NULL,
    position_side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    entry_price TEXT NOT NULL,
    break_even_price TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL,
    margin_type TEXT NOT NULL,
    isolated_wallet TEXT NOT NULL,
    update_time_ms INTEGER NOT NULL,
    mark_price TEXT NOT NULL DEFAULT '0',
    liquidation_price TEXT NOT NULL DEFAULT '0',
    notional TEXT NOT NULL DEFAULT '0',
    initial_margin TEXT NOT NULL DEFAULT '0',
    maintenance_margin TEXT NOT NULL DEFAULT '0',
    PRIMARY KEY (symbol, position_side)
);

CREATE TABLE IF NOT EXISTS balances (
    asset TEXT PRIMARY KEY,
    wallet_balance TEXT NOT NULL,
    cross_wallet_balance TEXT NOT NULL,
    balance_change TEXT NOT NULL,
    update_time_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS account_snapshots (
    update_time_ms INTEGER PRIMARY KEY,
    total_wallet_balance TEXT NOT NULL,
    total_unrealized_profit TEXT NOT NULL,
    total_margin_balance TEXT NOT NULL,
    available_balance TEXT NOT NULL,
    total_initial_margin TEXT NOT NULL DEFAULT '0',
    total_maintenance_margin TEXT NOT NULL DEFAULT '0'
);

CREATE TABLE IF NOT EXISTS income_records (
    symbol TEXT NOT NULL,
    income_type TEXT NOT NULL,
    transaction_id INTEGER NOT NULL,
    asset TEXT NOT NULL,
    amount TEXT NOT NULL,
    event_time_ms INTEGER NOT NULL,
    PRIMARY KEY (symbol, income_type, transaction_id)
);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    safe_to_resume INTEGER NOT NULL CHECK (safe_to_resume IN (0, 1)),
    orphan_count INTEGER NOT NULL,
    unresolved_local_count INTEGER NOT NULL,
    quantity_mismatch_count INTEGER NOT NULL,
    position_mismatch_count INTEGER NOT NULL,
    inserted_fills INTEGER NOT NULL,
    inserted_income_records INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    outcome TEXT NOT NULL,
    event_time_ms INTEGER NOT NULL,
    client_order_id TEXT,
    reason_codes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emergency_actions (
    action_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    client_order_id TEXT,
    exchange_order_id INTEGER,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
"""

_SCHEMA_MIGRATIONS = {
    "positions": {
        "mark_price": "TEXT NOT NULL DEFAULT '0'",
        "liquidation_price": "TEXT NOT NULL DEFAULT '0'",
        "notional": "TEXT NOT NULL DEFAULT '0'",
        "initial_margin": "TEXT NOT NULL DEFAULT '0'",
        "maintenance_margin": "TEXT NOT NULL DEFAULT '0'",
    },
    "account_snapshots": {
        "total_initial_margin": "TEXT NOT NULL DEFAULT '0'",
        "total_maintenance_margin": "TEXT NOT NULL DEFAULT '0'",
    },
}


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _apply_schema_migrations(connection: sqlite3.Connection) -> None:
    for table, columns in _SCHEMA_MIGRATIONS.items():
        existing = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, definition in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


class SqliteExecutionLedger:
    """Single-connection ledger whose every public database boundary is async."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = asyncio.Lock()
        self._closed = False

    @classmethod
    async def open(cls, path: str | Path) -> "SqliteExecutionLedger":
        """Open a local ledger and install its schema without blocking the event loop."""

        database_path = str(path)

        def connect_and_initialize() -> sqlite3.Connection:
            connection = sqlite3.connect(
                database_path,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            if database_path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)
            _apply_schema_migrations(connection)
            return connection

        try:
            connection = await asyncio.to_thread(connect_and_initialize)
        except sqlite3.Error as error:
            raise LedgerError("failed to initialize execution ledger") from error
        return cls(connection)

    async def __aenter__(self) -> "SqliteExecutionLedger":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            await asyncio.to_thread(self._connection.close)
            self._closed = True

    async def record_order_intent(
        self,
        request: OrderRequest,
        *,
        created_at_ms: int | None = None,
    ) -> bool:
        """Durably record an intent before any network submission."""

        timestamp = _wall_clock_ms() if created_at_ms is None else created_at_ms
        return await self._call(self._record_order_intent_sync, request, timestamp)

    def _record_order_intent_sync(self, request: OrderRequest, timestamp: int) -> bool:
        self._begin()
        try:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO orders (
                    client_order_id, strategy_id, level_index, cycle, exchange_order_id,
                    symbol, side, status, price, original_quantity, executed_quantity,
                    average_price, reduce_only, last_event_time_ms, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, '0', '0', ?, ?, ?, ?)
                """,
                (
                    request.client_order_id,
                    request.strategy_id,
                    request.level_index,
                    request.cycle,
                    request.symbol,
                    request.side.value,
                    OrderStatus.PENDING_SUBMIT.value,
                    _decimal_text(request.price),
                    _decimal_text(request.quantity),
                    int(request.reduce_only),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                row = self._connection.execute(
                    "SELECT * FROM orders WHERE client_order_id = ?",
                    (request.client_order_id,),
                ).fetchone()
                if row is None:
                    raise LedgerConflictError(
                        "an active logical order already exists for strategy, level, and side"
                    )
                if not self._intent_matches_row(request, row):
                    raise LedgerConflictError(
                        "client order ID already belongs to a different order intent"
                    )
            self._commit()
            return inserted
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise LedgerConflictError(
                "an active logical order already exists for strategy, level, and side"
            ) from error
        except BaseException:
            self._rollback()
            raise

    @staticmethod
    def _intent_matches_row(request: OrderRequest, row: sqlite3.Row) -> bool:
        return (
            row["strategy_id"] == request.strategy_id
            and row["level_index"] == request.level_index
            and row["cycle"] == request.cycle
            and row["symbol"] == request.symbol
            and row["side"] == request.side.value
            and Decimal(row["price"]) == request.price
            and Decimal(row["original_quantity"]) == request.quantity
            and bool(row["reduce_only"]) is request.reduce_only
        )

    async def mark_order_status(
        self,
        client_order_id: str,
        status: OrderStatus,
        *,
        event_time_ms: int | None = None,
    ) -> bool:
        """Record a local unknown or definite submission-rejection outcome."""

        if status not in {OrderStatus.UNKNOWN, OrderStatus.SUBMISSION_REJECTED}:
            raise ValueError("mark_order_status accepts only UNKNOWN or SUBMISSION_REJECTED")
        timestamp = _wall_clock_ms() if event_time_ms is None else event_time_ms
        return await self._call(
            self._mark_order_status_sync,
            client_order_id,
            status,
            timestamp,
        )

    def _mark_order_status_sync(
        self,
        client_order_id: str,
        status: OrderStatus,
        timestamp: int,
    ) -> bool:
        current_statuses = (
            ("PENDING_SUBMIT", "UNKNOWN", "NEW", "PARTIALLY_FILLED")
            if status is OrderStatus.UNKNOWN
            else ("PENDING_SUBMIT", "UNKNOWN")
        )
        placeholders = ", ".join("?" for _ in current_statuses)
        try:
            cursor = self._connection.execute(
                f"""
                UPDATE orders
                SET status = ?, last_event_time_ms = ?, updated_at_ms = ?
                WHERE client_order_id = ? AND status IN ({placeholders})
                """,
                (
                    status.value,
                    timestamp,
                    timestamp,
                    client_order_id,
                    *current_statuses,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise LedgerConflictError("order status update violated a ledger invariant") from error
        return cursor.rowcount == 1

    async def apply_order_snapshot(self, order: ExchangeOrder) -> bool:
        return await self._call(self._apply_order_snapshot_transaction_sync, order)

    def _apply_order_snapshot_transaction_sync(self, order: ExchangeOrder) -> bool:
        self._begin()
        try:
            updated = self._apply_order_snapshot_sync(order)
            self._commit()
            return updated
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise LedgerConflictError("order snapshot violated a ledger invariant") from error
        except BaseException:
            self._rollback()
            raise

    def _apply_order_snapshot_sync(self, order: ExchangeOrder) -> bool:
        existing = self._connection.execute(
            "SELECT * FROM orders WHERE client_order_id = ?",
            (order.client_order_id,),
        ).fetchone()
        now_ms = _wall_clock_ms()
        if existing is None:
            self._connection.execute(
                """
                INSERT INTO orders (
                    client_order_id, strategy_id, level_index, cycle, exchange_order_id,
                    symbol, side, status, price, original_quantity, executed_quantity,
                    average_price, reduce_only, last_event_time_ms, created_at_ms, updated_at_ms
                ) VALUES (?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.client_order_id,
                    order.exchange_order_id,
                    order.symbol,
                    order.side.value,
                    order.status.value,
                    _decimal_text(order.price),
                    _decimal_text(order.original_quantity),
                    _decimal_text(order.executed_quantity),
                    _decimal_text(order.average_price),
                    int(order.reduce_only),
                    order.update_time_ms,
                    order.update_time_ms,
                    now_ms,
                ),
            )
            return True

        existing_order_id = existing["exchange_order_id"]
        if existing_order_id is not None and existing_order_id != order.exchange_order_id:
            raise LedgerConflictError("client order ID maps to a different exchange order")
        if existing["strategy_id"] is not None and (
            existing["symbol"] != order.symbol
            or existing["side"] != order.side.value
            or Decimal(existing["price"]) != order.price
            or Decimal(existing["original_quantity"]) != order.original_quantity
            or bool(existing["reduce_only"]) is not order.reduce_only
        ):
            raise LedgerConflictError("exchange snapshot does not match the persisted order intent")
        existing_executed = Decimal(existing["executed_quantity"])
        existing_status = OrderStatus(existing["status"])
        is_newer = (
            not existing_status.is_exchange_status
            or order.update_time_ms >= existing["last_event_time_ms"]
        )
        has_progress = order.executed_quantity > existing_executed
        would_regress_fill = (
            existing_status is OrderStatus.FILLED and order.status is not OrderStatus.FILLED
        )
        if (not is_newer and not has_progress) or would_regress_fill:
            return False
        if order.executed_quantity < existing_executed:
            return False
        next_status = order.status
        if existing_status in _TERMINAL_EXCHANGE_STATUSES:
            next_status = (
                OrderStatus.FILLED if order.status is OrderStatus.FILLED else existing_status
            )
        next_event_time = (
            order.update_time_ms
            if not existing_status.is_exchange_status
            else max(existing["last_event_time_ms"], order.update_time_ms)
        )
        self._connection.execute(
            """
            UPDATE orders SET
                exchange_order_id = ?, symbol = ?, side = ?, status = ?, price = ?,
                original_quantity = ?, executed_quantity = ?, average_price = ?,
                reduce_only = ?, last_event_time_ms = ?, updated_at_ms = ?
            WHERE client_order_id = ?
            """,
            (
                order.exchange_order_id,
                order.symbol,
                order.side.value,
                next_status.value,
                _decimal_text(order.price),
                _decimal_text(order.original_quantity),
                _decimal_text(order.executed_quantity),
                _decimal_text(order.average_price),
                int(order.reduce_only),
                next_event_time,
                now_ms,
                order.client_order_id,
            ),
        )
        return True

    async def apply_order_event(self, event: OrderTradeUpdate) -> LedgerApplyResult:
        """Atomically deduplicate an event, advance its order, and insert its fill."""

        return await self._call(self._apply_order_event_sync, event)

    def _apply_order_event_sync(self, event: OrderTradeUpdate) -> LedgerApplyResult:
        self._begin()
        try:
            event_cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO exchange_events (
                    event_id, event_type, event_time_ms, transaction_time_ms, processed_at_ms
                ) VALUES (?, 'ORDER_TRADE_UPDATE', ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_time_ms,
                    event.transaction_time_ms,
                    _wall_clock_ms(),
                ),
            )
            if event_cursor.rowcount == 0:
                self._commit()
                return LedgerApplyResult(True, False, False)
            order_updated = self._apply_order_snapshot_sync(event.order)
            fill = event.fill
            fill_inserted = False if fill is None else self._record_fill_sync(fill)
            self._commit()
            return LedgerApplyResult(False, order_updated, fill_inserted)
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise LedgerConflictError("order event violated a ledger invariant") from error
        except BaseException:
            self._rollback()
            raise

    async def record_fill(self, fill: ExchangeFill) -> bool:
        return await self._call(self._record_fill_transaction_sync, fill)

    def _record_fill_transaction_sync(self, fill: ExchangeFill) -> bool:
        self._begin()
        try:
            inserted = self._record_fill_sync(fill)
            self._commit()
            return inserted
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise LedgerConflictError("fill violated a ledger invariant") from error
        except BaseException:
            self._rollback()
            raise

    def _record_fill_sync(self, fill: ExchangeFill) -> bool:
        order = self._connection.execute(
            "SELECT client_order_id FROM orders WHERE exchange_order_id = ?",
            (fill.exchange_order_id,),
        ).fetchone()
        client_order_id = None if order is None else order["client_order_id"]
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO fills (
                symbol, trade_id, exchange_order_id, client_order_id, side, price,
                quantity, commission, commission_asset, realized_pnl, event_time_ms, maker
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.symbol,
                fill.trade_id,
                fill.exchange_order_id,
                client_order_id,
                fill.side.value,
                _decimal_text(fill.price),
                _decimal_text(fill.quantity),
                _decimal_text(fill.commission),
                fill.commission_asset,
                _decimal_text(fill.realized_pnl),
                fill.event_time_ms,
                int(fill.maker),
            ),
        )
        if cursor.rowcount == 1:
            return True
        existing = self._connection.execute(
            "SELECT * FROM fills WHERE symbol = ? AND trade_id = ?",
            (fill.symbol, fill.trade_id),
        ).fetchone()
        if existing is None or not self._fill_matches_row(fill, existing):
            raise LedgerConflictError("trade ID already belongs to a different fill")
        if existing["client_order_id"] is None and client_order_id is not None:
            self._connection.execute(
                "UPDATE fills SET client_order_id = ? WHERE symbol = ? AND trade_id = ?",
                (client_order_id, fill.symbol, fill.trade_id),
            )
        return False

    @staticmethod
    def _fill_matches_row(fill: ExchangeFill, row: sqlite3.Row) -> bool:
        return (
            row["exchange_order_id"] == fill.exchange_order_id
            and row["side"] == fill.side.value
            and Decimal(row["price"]) == fill.price
            and Decimal(row["quantity"]) == fill.quantity
            and Decimal(row["commission"]) == fill.commission
            and row["commission_asset"] == fill.commission_asset
            and Decimal(row["realized_pnl"]) == fill.realized_pnl
            and row["event_time_ms"] == fill.event_time_ms
            and bool(row["maker"]) is fill.maker
        )

    async def apply_account_update(self, event: AccountUpdate) -> bool:
        return await self._call(self._apply_account_update_sync, event)

    def _apply_account_update_sync(self, event: AccountUpdate) -> bool:
        self._begin()
        try:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO exchange_events (
                    event_id, event_type, event_time_ms, transaction_time_ms, processed_at_ms
                ) VALUES (?, 'ACCOUNT_UPDATE', ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_time_ms,
                    event.transaction_time_ms,
                    _wall_clock_ms(),
                ),
            )
            if cursor.rowcount == 0:
                self._commit()
                return False
            for balance in event.balances:
                self._upsert_balance_sync(balance, event.transaction_time_ms)
            for position in event.positions:
                self._upsert_position_sync(position)
            self._commit()
            return True
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise LedgerConflictError("account event violated a ledger invariant") from error
        except BaseException:
            self._rollback()
            raise

    async def record_account_snapshot(self, snapshot: AccountSnapshot) -> bool:
        return await self._call(self._record_account_snapshot_sync, snapshot)

    def _record_account_snapshot_sync(self, snapshot: AccountSnapshot) -> bool:
        self._begin()
        try:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO account_snapshots (
                    update_time_ms, total_wallet_balance, total_unrealized_profit,
                    total_margin_balance, available_balance, total_initial_margin,
                    total_maintenance_margin
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.update_time_ms,
                    _decimal_text(snapshot.total_wallet_balance),
                    _decimal_text(snapshot.total_unrealized_profit),
                    _decimal_text(snapshot.total_margin_balance),
                    _decimal_text(snapshot.available_balance),
                    _decimal_text(snapshot.total_initial_margin),
                    _decimal_text(snapshot.total_maintenance_margin),
                ),
            )
            for balance in snapshot.balances:
                self._upsert_balance_sync(balance, snapshot.update_time_ms)
            self._commit()
            return cursor.rowcount == 1
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise LedgerConflictError("account snapshot violated a ledger invariant") from error
        except BaseException:
            self._rollback()
            raise

    def _upsert_balance_sync(self, balance: BalanceSnapshot, update_time_ms: int) -> None:
        self._connection.execute(
            """
            INSERT INTO balances (
                asset, wallet_balance, cross_wallet_balance, balance_change, update_time_ms
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(asset) DO UPDATE SET
                wallet_balance = excluded.wallet_balance,
                cross_wallet_balance = excluded.cross_wallet_balance,
                balance_change = excluded.balance_change,
                update_time_ms = excluded.update_time_ms
            WHERE excluded.update_time_ms >= balances.update_time_ms
            """,
            (
                balance.asset,
                _decimal_text(balance.wallet_balance),
                _decimal_text(balance.cross_wallet_balance),
                _decimal_text(balance.balance_change),
                update_time_ms,
            ),
        )

    async def record_positions(self, positions: Iterable[PositionSnapshot]) -> None:
        await self._call(self._record_positions_sync, tuple(positions))

    def _record_positions_sync(self, positions: tuple[PositionSnapshot, ...]) -> None:
        self._begin()
        try:
            for position in positions:
                self._upsert_position_sync(position)
            self._commit()
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise LedgerConflictError("position snapshot violated a ledger invariant") from error
        except BaseException:
            self._rollback()
            raise

    async def list_positions(self, symbol: str) -> tuple[PositionSnapshot, ...]:
        return await self._call(self._list_positions_sync, symbol)

    def _list_positions_sync(self, symbol: str) -> tuple[PositionSnapshot, ...]:
        rows = self._connection.execute(
            """
            SELECT * FROM positions
            WHERE symbol = ?
            ORDER BY position_side
            """,
            (symbol,),
        ).fetchall()
        return tuple(
            PositionSnapshot(
                symbol=row["symbol"],
                position_side=row["position_side"],
                quantity=Decimal(row["quantity"]),
                entry_price=Decimal(row["entry_price"]),
                break_even_price=Decimal(row["break_even_price"]),
                unrealized_pnl=Decimal(row["unrealized_pnl"]),
                margin_type=row["margin_type"],
                isolated_wallet=Decimal(row["isolated_wallet"]),
                update_time_ms=row["update_time_ms"],
                mark_price=Decimal(row["mark_price"]),
                liquidation_price=Decimal(row["liquidation_price"]),
                notional=Decimal(row["notional"]),
                initial_margin=Decimal(row["initial_margin"]),
                maintenance_margin=Decimal(row["maintenance_margin"]),
            )
            for row in rows
        )

    def _upsert_position_sync(self, position: PositionSnapshot) -> None:
        self._connection.execute(
            """
            INSERT INTO positions (
                symbol, position_side, quantity, entry_price, break_even_price,
                unrealized_pnl, margin_type, isolated_wallet, update_time_ms, mark_price,
                liquidation_price, notional, initial_margin, maintenance_margin
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, position_side) DO UPDATE SET
                quantity = excluded.quantity,
                entry_price = excluded.entry_price,
                break_even_price = excluded.break_even_price,
                unrealized_pnl = excluded.unrealized_pnl,
                margin_type = excluded.margin_type,
                isolated_wallet = excluded.isolated_wallet,
                update_time_ms = excluded.update_time_ms,
                mark_price = excluded.mark_price,
                liquidation_price = excluded.liquidation_price,
                notional = excluded.notional,
                initial_margin = excluded.initial_margin,
                maintenance_margin = excluded.maintenance_margin
            WHERE excluded.update_time_ms >= positions.update_time_ms
            """,
            (
                position.symbol,
                position.position_side,
                _decimal_text(position.quantity),
                _decimal_text(position.entry_price),
                _decimal_text(position.break_even_price),
                _decimal_text(position.unrealized_pnl),
                position.margin_type,
                _decimal_text(position.isolated_wallet),
                position.update_time_ms,
                _decimal_text(position.mark_price),
                _decimal_text(position.liquidation_price),
                _decimal_text(position.notional),
                _decimal_text(position.initial_margin),
                _decimal_text(position.maintenance_margin),
            ),
        )

    async def record_income_records(self, records: Iterable[IncomeRecord]) -> int:
        return await self._call(self._record_income_records_sync, tuple(records))

    def _record_income_records_sync(self, records: tuple[IncomeRecord, ...]) -> int:
        self._begin()
        inserted = 0
        try:
            for record in records:
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO income_records (
                        symbol, income_type, transaction_id, asset, amount, event_time_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.symbol,
                        record.income_type,
                        record.transaction_id,
                        record.asset,
                        _decimal_text(record.amount),
                        record.event_time_ms,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                    continue
                existing = self._connection.execute(
                    """
                    SELECT * FROM income_records
                    WHERE symbol = ? AND income_type = ? AND transaction_id = ?
                    """,
                    (record.symbol, record.income_type, record.transaction_id),
                ).fetchone()
                if existing is None or not self._income_matches_row(record, existing):
                    raise LedgerConflictError(
                        "income transaction ID already belongs to a different record"
                    )
            self._commit()
            return inserted
        except sqlite3.IntegrityError as error:
            self._rollback()
            raise LedgerConflictError("income record violated a ledger invariant") from error
        except BaseException:
            self._rollback()
            raise

    @staticmethod
    def _income_matches_row(record: IncomeRecord, row: sqlite3.Row) -> bool:
        return (
            row["asset"] == record.asset
            and Decimal(row["amount"]) == record.amount
            and row["event_time_ms"] == record.event_time_ms
        )

    async def get_order(self, client_order_id: str) -> OrderRecord | None:
        return await self._call(self._get_order_sync, client_order_id)

    def _get_order_sync(self, client_order_id: str) -> OrderRecord | None:
        row = self._connection.execute(
            "SELECT * FROM orders WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
        return None if row is None else self._order_from_row(row)

    async def list_active_orders(self, symbol: str) -> tuple[OrderRecord, ...]:
        return await self._call(self._list_active_orders_sync, symbol)

    def _list_active_orders_sync(self, symbol: str) -> tuple[OrderRecord, ...]:
        placeholders = ", ".join("?" for _ in _ACTIVE_STATUSES)
        rows = self._connection.execute(
            f"""
            SELECT * FROM orders
            WHERE symbol = ? AND status IN ({placeholders})
            ORDER BY client_order_id
            """,
            (symbol, *(status.value for status in _ACTIVE_STATUSES)),
        ).fetchall()
        return tuple(self._order_from_row(row) for row in rows)

    @staticmethod
    def _order_from_row(row: sqlite3.Row) -> OrderRecord:
        return OrderRecord(
            client_order_id=row["client_order_id"],
            strategy_id=row["strategy_id"],
            level_index=row["level_index"],
            cycle=row["cycle"],
            exchange_order_id=row["exchange_order_id"],
            symbol=row["symbol"],
            side=OrderSide(row["side"]),
            status=OrderStatus(row["status"]),
            price=Decimal(row["price"]),
            original_quantity=Decimal(row["original_quantity"]),
            executed_quantity=Decimal(row["executed_quantity"]),
            average_price=Decimal(row["average_price"]),
            reduce_only=bool(row["reduce_only"]),
            last_event_time_ms=row["last_event_time_ms"],
        )

    async def list_fills(self, symbol: str) -> tuple[ExchangeFill, ...]:
        return await self._call(self._list_fills_sync, symbol)

    def _list_fills_sync(self, symbol: str) -> tuple[ExchangeFill, ...]:
        rows = self._connection.execute(
            "SELECT * FROM fills WHERE symbol = ? ORDER BY trade_id",
            (symbol,),
        ).fetchall()
        return tuple(
            ExchangeFill(
                symbol=row["symbol"],
                trade_id=row["trade_id"],
                exchange_order_id=row["exchange_order_id"],
                side=OrderSide(row["side"]),
                price=Decimal(row["price"]),
                quantity=Decimal(row["quantity"]),
                commission=Decimal(row["commission"]),
                commission_asset=row["commission_asset"],
                realized_pnl=Decimal(row["realized_pnl"]),
                event_time_ms=row["event_time_ms"],
                maker=bool(row["maker"]),
            )
            for row in rows
        )

    async def record_reconciliation_report(self, report: ReconciliationReport) -> None:
        await self._call(self._record_reconciliation_report_sync, report)

    def _record_reconciliation_report_sync(self, report: ReconciliationReport) -> None:
        self._connection.execute(
            """
            INSERT INTO reconciliation_runs (
                started_at_ms, completed_at_ms, symbol, safe_to_resume, orphan_count,
                unresolved_local_count, quantity_mismatch_count, position_mismatch_count,
                inserted_fills, inserted_income_records
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.started_at_ms,
                report.completed_at_ms,
                report.symbol,
                int(report.safe_to_resume),
                len(report.orphan_client_order_ids),
                len(report.unresolved_local_client_order_ids),
                len(report.quantity_mismatch_client_order_ids),
                len(report.position_mismatch_keys),
                report.inserted_fills,
                report.inserted_income_records,
            ),
        )

    async def record_risk_event(self, event: RiskAuditEvent) -> bool:
        return await self._call(self._record_risk_event_sync, event)

    def _record_risk_event_sync(self, event: RiskAuditEvent) -> bool:
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO risk_events (
                event_id, event_type, symbol, outcome, event_time_ms,
                client_order_id, reason_codes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type.value,
                event.symbol,
                event.outcome,
                event.event_time_ms,
                event.client_order_id,
                ",".join(event.reason_codes),
            ),
        )
        return cursor.rowcount == 1

    async def list_risk_events(self) -> tuple[RiskAuditEvent, ...]:
        return await self._call(self._list_risk_events_sync)

    def _list_risk_events_sync(self) -> tuple[RiskAuditEvent, ...]:
        rows = self._connection.execute(
            "SELECT * FROM risk_events ORDER BY event_time_ms, event_id"
        ).fetchall()
        return tuple(
            RiskAuditEvent(
                event_type=RiskEventType(row["event_type"]),
                symbol=row["symbol"],
                outcome=row["outcome"],
                event_time_ms=row["event_time_ms"],
                client_order_id=row["client_order_id"],
                reason_codes=tuple(filter(None, str(row["reason_codes"]).split(","))),
            )
            for row in rows
        )

    async def begin_emergency_action(
        self,
        action_id: str,
        symbol: str,
        created_at_ms: int,
    ) -> bool:
        EmergencyActionRecord(
            action_id=action_id,
            symbol=symbol,
            status=EmergencyActionStatus.PENDING,
            client_order_id=None,
            exchange_order_id=None,
            created_at_ms=created_at_ms,
            updated_at_ms=created_at_ms,
        )
        return await self._call(
            self._begin_emergency_action_sync,
            action_id,
            symbol,
            created_at_ms,
        )

    def _begin_emergency_action_sync(
        self,
        action_id: str,
        symbol: str,
        created_at_ms: int,
    ) -> bool:
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO emergency_actions (
                action_id, symbol, status, client_order_id, exchange_order_id,
                created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                action_id,
                symbol,
                EmergencyActionStatus.PENDING.value,
                created_at_ms,
                created_at_ms,
            ),
        )
        if cursor.rowcount == 1:
            return True
        row = self._connection.execute(
            "SELECT symbol FROM emergency_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if row is None or row["symbol"] != symbol:
            raise LedgerConflictError("emergency action ID belongs to a different symbol")
        return False

    async def complete_emergency_action(
        self,
        action_id: str,
        status: EmergencyActionStatus,
        *,
        client_order_id: str | None,
        exchange_order_id: int | None,
        updated_at_ms: int,
    ) -> None:
        if not isinstance(status, EmergencyActionStatus):
            raise TypeError("status must be EmergencyActionStatus")
        await self._call(
            self._complete_emergency_action_sync,
            action_id,
            status,
            client_order_id,
            exchange_order_id,
            updated_at_ms,
        )

    def _complete_emergency_action_sync(
        self,
        action_id: str,
        status: EmergencyActionStatus,
        client_order_id: str | None,
        exchange_order_id: int | None,
        updated_at_ms: int,
    ) -> None:
        row = self._connection.execute(
            "SELECT * FROM emergency_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if row is None:
            raise LedgerConflictError("emergency action must be persisted before completion")
        if status is EmergencyActionStatus.PENDING:
            raise LedgerConflictError("emergency completion status must be terminal or unknown")
        if updated_at_ms < int(row["created_at_ms"]):
            raise LedgerConflictError("emergency completion cannot precede action creation")
        if updated_at_ms < int(row["updated_at_ms"]):
            raise LedgerConflictError("emergency action update time cannot move backwards")
        current_status = EmergencyActionStatus(row["status"])
        if current_status in {
            EmergencyActionStatus.NO_POSITION,
            EmergencyActionStatus.SUBMITTED,
        }:
            if (
                current_status is status
                and row["client_order_id"] == client_order_id
                and row["exchange_order_id"] == exchange_order_id
            ):
                return
            raise LedgerConflictError("completed emergency action cannot change outcome")
        if current_status is EmergencyActionStatus.UNKNOWN:
            if status is EmergencyActionStatus.UNKNOWN:
                if (
                    row["client_order_id"] == client_order_id
                    and row["exchange_order_id"] == exchange_order_id
                ):
                    return
                raise LedgerConflictError("unknown emergency action metadata cannot change")
            if row["client_order_id"] not in {None, client_order_id}:
                raise LedgerConflictError("reconciled emergency action changed client order ID")
        self._connection.execute(
            """
            UPDATE emergency_actions
            SET status = ?, client_order_id = ?, exchange_order_id = ?, updated_at_ms = ?
            WHERE action_id = ?
            """,
            (
                status.value,
                client_order_id,
                exchange_order_id,
                updated_at_ms,
                action_id,
            ),
        )

    async def get_emergency_action(
        self,
        action_id: str,
    ) -> EmergencyActionRecord | None:
        return await self._call(self._get_emergency_action_sync, action_id)

    def _get_emergency_action_sync(self, action_id: str) -> EmergencyActionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM emergency_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        return EmergencyActionRecord(
            action_id=row["action_id"],
            symbol=row["symbol"],
            status=EmergencyActionStatus(row["status"]),
            client_order_id=row["client_order_id"],
            exchange_order_id=row["exchange_order_id"],
            created_at_ms=row["created_at_ms"],
            updated_at_ms=row["updated_at_ms"],
        )

    async def count_exchange_events(self) -> int:
        return await self._call(self._count_exchange_events_sync)

    def _count_exchange_events_sync(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS total FROM exchange_events").fetchone()
        if row is None:
            raise LedgerError("failed to count exchange events")
        return int(row["total"])

    async def _call(
        self,
        function: Callable[_P, _T],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _T:
        if self._closed:
            raise LedgerError("execution ledger is closed")
        async with self._lock:
            try:
                return await asyncio.to_thread(function, *args, **kwargs)
            except (LedgerError, ValueError, TypeError):
                raise
            except sqlite3.Error as error:
                raise LedgerError("execution ledger operation failed") from error

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._connection.execute("COMMIT")

    def _rollback(self) -> None:
        self._connection.execute("ROLLBACK")
