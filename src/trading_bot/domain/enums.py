"""Enumerations shared by the strategy domain."""

from enum import StrEnum


class GridType(StrEnum):
    """Supported and planned grid spacing modes."""

    ARITHMETIC = "ARITHMETIC"
    GEOMETRIC = "GEOMETRIC"


class GridDirection(StrEnum):
    """Supported and planned grid position directions."""

    NEUTRAL = "NEUTRAL"
    LONG = "LONG"
    SHORT = "SHORT"


class OrderSide(StrEnum):
    """Order side assigned to an active grid level."""

    BUY = "BUY"
    SELL = "SELL"


class StrategyState(StrEnum):
    """Lifecycle states for a grid strategy."""

    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    RECOVERING = "RECOVERING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    EMERGENCY_STOP = "EMERGENCY_STOP"
