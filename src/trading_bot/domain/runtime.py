"""Dependency-free application lifecycle and audit records."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from re import fullmatch

from trading_bot.domain.enums import StrategyState
from trading_bot.domain.errors import DomainValidationError


def _require_non_empty(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")
    if not value or value != value.strip():
        raise DomainValidationError(f"{name} must be non-empty and trimmed")
    return value


def _require_non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int")
    if value < 0:
        raise DomainValidationError(f"{name} must not be negative")
    return value


def _validate_symbol(symbol: str) -> None:
    _require_non_empty("symbol", symbol)
    if symbol != symbol.upper() or not symbol.replace("_", "").isalnum():
        raise DomainValidationError(
            "symbol must be uppercase and contain only letters, numbers, and underscores"
        )


def _validate_identifier(name: str, value: str) -> None:
    _require_non_empty(name, value)
    if len(value) > 64 or fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value) is None:
        raise DomainValidationError(f"{name} must be at most 64 safe identifier characters")


class RuntimeCommand(StrEnum):
    """Operator commands accepted by the P6 state machine."""

    STATUS = "status"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    EMERGENCY_STOP = "emergency-stop"


class CommandStatus(StrEnum):
    """Durable command processing states."""

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RuntimeEventType(StrEnum):
    """Secret-free runtime event categories retained for incident evidence."""

    STATE_TRANSITION = "STATE_TRANSITION"
    TASK_FAILURE = "TASK_FAILURE"
    BACKPRESSURE = "BACKPRESSURE"
    RECONCILIATION = "RECONCILIATION"
    SHUTDOWN = "SHUTDOWN"


class PausePolicy(StrEnum):
    """P6 pause behavior for existing grid orders."""

    CANCEL_OPEN_ORDERS_KEEP_POSITION = "CANCEL_OPEN_ORDERS_KEEP_POSITION"


class StopPolicy(StrEnum):
    """P6 normal-stop behavior; flattening is reserved for emergency stop."""

    CANCEL_OPEN_ORDERS_KEEP_POSITION = "CANCEL_OPEN_ORDERS_KEEP_POSITION"


@dataclass(frozen=True, slots=True)
class ControlCommandRecord:
    """Durable idempotency and audit record for one authenticated command."""

    command_id: str
    command: RuntimeCommand
    actor: str
    status: CommandStatus
    requested_at_ms: int
    completed_at_ms: int | None = None
    result_state: StrategyState | None = None
    detail_code: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier("command_id", self.command_id)
        if not isinstance(self.command, RuntimeCommand):
            raise TypeError("command must be RuntimeCommand")
        _validate_identifier("actor", self.actor)
        if not isinstance(self.status, CommandStatus):
            raise TypeError("status must be CommandStatus")
        _require_non_negative_int("requested_at_ms", self.requested_at_ms)
        if self.completed_at_ms is not None:
            _require_non_negative_int("completed_at_ms", self.completed_at_ms)
            if self.completed_at_ms < self.requested_at_ms:
                raise DomainValidationError("completed_at_ms must not precede requested_at_ms")
        if self.result_state is not None and not isinstance(self.result_state, StrategyState):
            raise TypeError("result_state must be StrategyState when supplied")
        if self.detail_code is not None:
            _validate_identifier("detail_code", self.detail_code)
        if self.status is CommandStatus.PENDING:
            if self.completed_at_ms is not None or self.result_state is not None:
                raise DomainValidationError("pending command must not have a completed result")
        elif self.completed_at_ms is None or self.result_state is None:
            raise DomainValidationError("completed command requires time and result_state")


@dataclass(frozen=True, slots=True)
class RuntimeAuditEvent:
    """Sanitized durable evidence for lifecycle transitions and task failures."""

    event_type: RuntimeEventType
    symbol: str
    outcome: str
    event_time_ms: int
    state: StrategyState
    component: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, RuntimeEventType):
            raise TypeError("event_type must be RuntimeEventType")
        _validate_symbol(self.symbol)
        _validate_identifier("outcome", self.outcome)
        _require_non_negative_int("event_time_ms", self.event_time_ms)
        if not isinstance(self.state, StrategyState):
            raise TypeError("state must be StrategyState")
        if self.component is not None:
            _validate_identifier("component", self.component)
        if self.reason_code is not None:
            _validate_identifier("reason_code", self.reason_code)

    @property
    def event_id(self) -> str:
        values = (
            self.event_type.value,
            self.symbol,
            self.outcome,
            str(self.event_time_ms),
            self.state.value,
            self.component or "",
            self.reason_code or "",
        )
        return f"runtime:{sha256('|'.join(values).encode()).hexdigest()}"
