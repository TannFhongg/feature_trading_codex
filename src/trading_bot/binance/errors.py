"""Errors raised by the Binance public adapter."""


class BinanceAdapterError(RuntimeError):
    """Base error for public Binance adapter failures."""


class BinanceProtocolError(BinanceAdapterError):
    """Raised when Binance returns a payload that violates the expected contract."""


class BinanceTransportError(BinanceAdapterError):
    """Raised when a network transport cannot complete a request."""


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
