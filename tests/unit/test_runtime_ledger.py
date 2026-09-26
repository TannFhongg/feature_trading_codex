import asyncio

import pytest

from trading_bot.domain import (
    CommandStatus,
    ControlCommandRecord,
    RuntimeAuditEvent,
    RuntimeCommand,
    RuntimeEventType,
    StrategyState,
)
from trading_bot.persistence import LedgerConflictError, SqliteExecutionLedger


def test_control_command_and_runtime_audit_are_durable_and_idempotent() -> None:
    async def scenario() -> None:
        ledger = await SqliteExecutionLedger.open(":memory:")
        pending = ControlCommandRecord(
            command_id="cmd-1",
            command=RuntimeCommand.START,
            actor="operator",
            status=CommandStatus.PENDING,
            requested_at_ms=10,
        )
        assert await ledger.begin_control_command(pending)
        assert not await ledger.begin_control_command(pending)
        assert await ledger.complete_control_command(
            "cmd-1",
            CommandStatus.SUCCEEDED,
            StrategyState.PAUSED,
            11,
        )
        assert not await ledger.complete_control_command(
            "cmd-1",
            CommandStatus.SUCCEEDED,
            StrategyState.PAUSED,
            12,
        )
        stored = await ledger.get_control_command("cmd-1")
        assert stored is not None
        assert stored.status is CommandStatus.SUCCEEDED
        assert stored.result_state is StrategyState.PAUSED

        event = RuntimeAuditEvent(
            event_type=RuntimeEventType.STATE_TRANSITION,
            symbol="BTCUSDT",
            outcome="started",
            event_time_ms=12,
            state=StrategyState.PAUSED,
            component="orchestrator",
        )
        assert await ledger.record_runtime_event(event)
        assert not await ledger.record_runtime_event(event)
        assert await ledger.list_runtime_events() == (event,)
        await ledger.close()

    asyncio.run(scenario())


def test_command_id_cannot_be_rebound_to_another_operation() -> None:
    async def scenario() -> None:
        ledger = await SqliteExecutionLedger.open(":memory:")
        assert await ledger.begin_control_command(
            ControlCommandRecord(
                command_id="cmd-1",
                command=RuntimeCommand.START,
                actor="operator",
                status=CommandStatus.PENDING,
                requested_at_ms=1,
            )
        )
        with pytest.raises(LedgerConflictError, match="different command data"):
            await ledger.begin_control_command(
                ControlCommandRecord(
                    command_id="cmd-1",
                    command=RuntimeCommand.STOP,
                    actor="operator",
                    status=CommandStatus.PENDING,
                    requested_at_ms=2,
                )
            )
        await ledger.close()

    asyncio.run(scenario())
