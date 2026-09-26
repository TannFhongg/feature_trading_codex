"""Read-only exchange reconciliation against the persistent local ledger."""

from collections.abc import Callable, Iterable
from time import time_ns
from typing import Protocol

from trading_bot.execution.models import (
    AccountSnapshot,
    ExchangeFill,
    ExchangeOrder,
    IncomeRecord,
    OrderRecord,
    PositionSnapshot,
    ReconciliationReport,
)


def _wall_clock_ms() -> int:
    return time_ns() // 1_000_000


class ReconciliationGateway(Protocol):
    """Private exchange reads required by one reconciliation pass."""

    async def list_open_orders(self, symbol: str) -> tuple[ExchangeOrder, ...]:
        """Return the current exchange open-order set for one symbol."""

    async def query_order(self, symbol: str, client_order_id: str) -> ExchangeOrder | None:
        """Resolve one local order absent from the open-order set."""

    async def list_account_trades(
        self,
        symbol: str,
        *,
        from_id: int | None = None,
        limit: int = 1_000,
    ) -> tuple[ExchangeFill, ...]:
        """Return recent account trades for missing-fill repair."""

    async def fetch_positions(self, symbol: str) -> tuple[PositionSnapshot, ...]:
        """Return current positions for one symbol."""

    async def fetch_account(self) -> AccountSnapshot:
        """Return current account totals and balances."""

    async def list_income(
        self,
        symbol: str,
        *,
        income_type: str = "FUNDING_FEE",
        limit: int = 1_000,
    ) -> tuple[IncomeRecord, ...]:
        """Return recent funding income records."""


class ReconciliationLedger(Protocol):
    """Ledger reads and idempotent writes used during reconciliation."""

    async def list_active_orders(self, symbol: str) -> tuple[OrderRecord, ...]:
        """Return active local orders."""

    async def apply_order_snapshot(self, order: ExchangeOrder) -> bool:
        """Apply an exchange order snapshot."""

    async def record_fill(self, fill: ExchangeFill) -> bool:
        """Insert one fill if it is new."""

    async def record_positions(self, positions: Iterable[PositionSnapshot]) -> None:
        """Upsert current positions."""

    async def list_positions(self, symbol: str) -> tuple[PositionSnapshot, ...]:
        """Return locally persisted positions for drift comparison."""

    async def record_account_snapshot(self, snapshot: AccountSnapshot) -> bool:
        """Insert current account totals and upsert balances."""

    async def record_income_records(self, records: Iterable[IncomeRecord]) -> int:
        """Insert unseen income records."""

    async def record_reconciliation_report(self, report: ReconciliationReport) -> None:
        """Persist summary evidence for this pass."""


class ExecutionReconciler:
    """Repair missing ledger observations without submitting or canceling orders."""

    def __init__(
        self,
        gateway: ReconciliationGateway,
        ledger: ReconciliationLedger,
        *,
        clock_ms: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        self._gateway = gateway
        self._ledger = ledger
        self._clock_ms = clock_ms

    async def reconcile(self, symbol: str) -> ReconciliationReport:
        """Compare local and remote state, then persist all safe idempotent repairs."""

        started_at_ms = self._clock_ms()
        local_orders = await self._ledger.list_active_orders(symbol)
        remote_open_orders = await self._gateway.list_open_orders(symbol)
        local_by_client_id = {order.client_order_id: order for order in local_orders}
        remote_by_client_id = {order.client_order_id: order for order in remote_open_orders}
        owned_local_client_ids = {
            order.client_order_id for order in local_orders if order.strategy_id is not None
        }

        orphan_ids = tuple(sorted(set(remote_by_client_id).difference(owned_local_client_ids)))
        quantity_mismatches: list[str] = []
        for client_order_id in sorted(set(local_by_client_id).intersection(remote_by_client_id)):
            local = local_by_client_id[client_order_id]
            remote = remote_by_client_id[client_order_id]
            if (
                local.original_quantity != remote.original_quantity
                or local.executed_quantity > remote.executed_quantity
            ):
                quantity_mismatches.append(client_order_id)
            await self._ledger.apply_order_snapshot(remote)

        for client_order_id in orphan_ids:
            await self._ledger.apply_order_snapshot(remote_by_client_id[client_order_id])

        unresolved_local: list[str] = []
        for client_order_id in sorted(set(local_by_client_id).difference(remote_by_client_id)):
            resolved = await self._gateway.query_order(symbol, client_order_id)
            if resolved is None:
                unresolved_local.append(client_order_id)
                continue
            await self._ledger.apply_order_snapshot(resolved)
            if resolved.status.is_active:
                unresolved_local.append(client_order_id)

        trades = await self._gateway.list_account_trades(symbol, limit=1_000)
        inserted_fills = 0
        for fill in trades:
            inserted_fills += int(await self._ledger.record_fill(fill))

        local_positions = await self._ledger.list_positions(symbol)
        positions = await self._gateway.fetch_positions(symbol)
        local_positions_by_key = {
            (position.symbol, position.position_side): position for position in local_positions
        }
        remote_positions_by_key = {
            (position.symbol, position.position_side): position for position in positions
        }
        position_mismatch_values: list[str] = []
        for position_symbol, position_side in sorted(
            set(local_positions_by_key).union(remote_positions_by_key)
        ):
            key = (position_symbol, position_side)
            local_position = local_positions_by_key.get(key)
            remote_position = remote_positions_by_key.get(key)
            if (
                local_position is None
                or remote_position is None
                or local_position.quantity != remote_position.quantity
                or local_position.entry_price != remote_position.entry_price
            ):
                position_mismatch_values.append(f"{position_symbol}:{position_side}")
        position_mismatches = tuple(position_mismatch_values)
        await self._ledger.record_positions(positions)
        account = await self._gateway.fetch_account()
        await self._ledger.record_account_snapshot(account)
        income = await self._gateway.list_income(
            symbol,
            income_type="FUNDING_FEE",
            limit=1_000,
        )
        inserted_income = await self._ledger.record_income_records(income)

        completed_at_ms = self._clock_ms()
        report = ReconciliationReport(
            symbol=symbol,
            started_at_ms=started_at_ms,
            completed_at_ms=completed_at_ms,
            orphan_client_order_ids=orphan_ids,
            unresolved_local_client_order_ids=tuple(unresolved_local),
            quantity_mismatch_client_order_ids=tuple(quantity_mismatches),
            position_mismatch_keys=position_mismatches,
            inserted_fills=inserted_fills,
            inserted_income_records=inserted_income,
        )
        await self._ledger.record_reconciliation_report(report)
        return report
