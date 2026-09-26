"""Secret-safe structured logging, in-process metrics, and alert boundaries."""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from re import fullmatch
from typing import Protocol

_SENSITIVE_FRAGMENTS = (
    "api_key",
    "api_secret",
    "authorization",
    "signature",
    "secret",
    "token",
    "listen_key",
    "account",
    "client_order_id",
    "x-mbx",
)


def redact(value: object, *, key: str = "") -> object:
    """Recursively redact known credential and trading-identity fields."""

    normalized_key = key.lower().replace("-", "_")
    if any(fragment in normalized_key for fragment in _SENSITIVE_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return type(value).__name__


class StructuredJsonFormatter(logging.Formatter):
    """Render one JSON object per record without serializing exception payloads."""

    def format(self, record: logging.LogRecord) -> str:
        context = getattr(record, "context", {})
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
            "context": redact(context),
        }
        if record.exc_info is not None and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def configure_structured_logging(level: int = logging.INFO) -> None:
    """Install a single process-level structured handler idempotently."""

    root = logging.getLogger()
    if not any(isinstance(handler.formatter, StructuredJsonFormatter) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJsonFormatter())
        root.addHandler(handler)
    root.setLevel(level)


class RuntimeMetrics:
    """Small dependency-free Prometheus text collector for bounded P6 metrics."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, int | float] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        self._validate_name(name)
        if amount < 0:
            raise ValueError("counter amount must not be negative")
        self._counters[name] = self._counters.get(name, 0) + amount

    def gauge(self, name: str, value: int | float) -> None:
        self._validate_name(name)
        self._gauges[name] = value

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for name, counter_value in sorted(self._counters.items()):
            lines.append(f"trading_bot_{name}_total {counter_value}")
        for name, gauge_value in sorted(self._gauges.items()):
            lines.append(f"trading_bot_{name} {gauge_value}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _validate_name(name: str) -> None:
        if fullmatch(r"[a-z][a-z0-9_]*", name) is None:
            raise ValueError("metric name must be lowercase Prometheus-safe text")


class AlertSink(Protocol):
    """External notification boundary; implementations receive sanitized codes only."""

    async def emit(self, alert: "RuntimeAlert") -> None:
        """Deliver one alert without a raw exception or account payload."""


@dataclass(frozen=True, slots=True)
class RuntimeAlert:
    severity: str
    code: str
    component: str
    state: str


class LoggingAlertSink:
    """Default local alert sink; P7 may replace it with Telegram/Slack delivery."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("trading_bot.alert")

    async def emit(self, alert: RuntimeAlert) -> None:
        self._logger.error(
            "runtime alert",
            extra={
                "event": "runtime_alert",
                "context": {
                    "severity": alert.severity,
                    "code": alert.code,
                    "component": alert.component,
                    "state": alert.state,
                },
            },
        )
