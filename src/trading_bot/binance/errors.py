"""Sanitized errors raised by Binance public and private adapters."""

from trading_bot.execution.errors import AmbiguousExecutionError, ExecutionAdapterError


class BinanceAdapterError(ExecutionAdapterError):
    """Base error for public Binance adapter failures."""


class BinanceProtocolError(BinanceAdapterError):
    """Raised when Binance returns a payload that violates the expected contract."""


class BinanceTransportError(BinanceAdapterError):
    """Raised when a network transport cannot complete a request."""


class BinanceStaleStreamError(BinanceTransportError):
    """Raised when no market message arrives inside the configured stale window."""


class BinanceHttpError(BinanceTransportError):
    """HTTP failure carrying retry metadata without exposing response secrets."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(f"Binance HTTP {status_code}: {message}")
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class BinanceApiError(BinanceHttpError):
    """Structured Binance API error without echoing payloads or credentials."""

    def __init__(
        self,
        status_code: int,
        error_code: int | None,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(
            status_code,
            "API request rejected",
            retry_after_seconds=retry_after_seconds,
        )
        self.error_code = error_code


class BinanceAmbiguousOrderError(BinanceAdapterError, AmbiguousExecutionError):
    """Raised when a mutating request cannot be resolved safely by querying."""

    def __init__(self, client_order_id: str, operation: str) -> None:
        super().__init__(
            f"Binance {operation} outcome remains unknown for client order {client_order_id}"
        )
        self.client_order_id = client_order_id
        self.operation = operation


class BinanceOrderSubmissionDisabledError(BinanceAdapterError):
    """Raised when submission is attempted without the explicit safety opt-in."""
