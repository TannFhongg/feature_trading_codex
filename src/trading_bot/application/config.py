"""Strict, Testnet-first configuration for the P6 composition root."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from ipaddress import ip_address
from math import isfinite
from os import environ
from pathlib import Path

from trading_bot.domain import (
    DomainValidationError,
    GridConfig,
    PausePolicy,
    StopPolicy,
    SymbolRules,
)
from trading_bot.risk import RiskLimits


@dataclass(frozen=True, slots=True)
class SecretValue:
    """In-memory secret wrapper whose representation never reveals its value."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("secret value must be str")
        if not self.value or self.value != self.value.strip():
            raise DomainValidationError("secret value must be non-empty and trimmed")
        if self.value.startswith("<") and self.value.endswith(">"):
            raise DomainValidationError("placeholder secret values are not valid runtime secrets")


def _default_grid() -> GridConfig:
    return GridConfig(
        symbol="BTCUSDT",
        lower_price=Decimal("50000"),
        upper_price=Decimal("70000"),
        reference_price=Decimal("60000"),
        grid_count=4,
        quantity_per_order=Decimal("0.001"),
    )


def _default_rules() -> SymbolRules:
    return SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
    )


def _default_limits() -> RiskLimits:
    return RiskLimits(
        symbol="BTCUSDT",
        max_abs_position_quantity=Decimal("0.01"),
        max_position_notional=Decimal("5000"),
        max_open_orders=20,
        max_daily_loss=Decimal("100"),
        max_drawdown=Decimal("200"),
        max_abs_funding_rate=Decimal("0.01"),
        min_liquidation_distance_ratio=Decimal("0.05"),
        max_maintenance_margin_ratio=Decimal("0.5"),
        max_market_data_age_ms=15_000,
        max_user_data_age_ms=15_000,
        soft_lower_price=Decimal("49000"),
        soft_upper_price=Decimal("71000"),
        hard_lower_price=Decimal("45000"),
        hard_upper_price=Decimal("75000"),
        max_leverage=1,
    )


def _validate_control_host(value: str) -> None:
    if value == "localhost":
        return
    try:
        parsed = ip_address(value)
    except ValueError as error:
        raise DomainValidationError(
            "control_host must be localhost or a literal loopback/private IP address"
        ) from error
    if parsed.is_unspecified or not (parsed.is_loopback or parsed.is_private):
        raise DomainValidationError("control_host must not expose the API on a public interface")


@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    """Complete immutable runtime configuration; all trading defaults fail closed."""

    control_token: SecretValue = field(repr=False)
    strategy_id: str = "neutral-grid"
    environment: str = "testnet"
    dry_run: bool = True
    order_submission_enabled: bool = False
    live_trading_enabled: bool = False
    api_key: SecretValue | None = field(default=None, repr=False)
    api_secret: SecretValue | None = field(default=None, repr=False)
    ledger_path: Path = Path("data/trading_bot.db")
    control_host: str = "127.0.0.1"
    control_port: int = 8080
    queue_capacity: int = 256
    reconciliation_interval_seconds: float = 60.0
    pause_policy: PausePolicy = PausePolicy.CANCEL_OPEN_ORDERS_KEEP_POSITION
    stop_policy: StopPolicy = StopPolicy.CANCEL_OPEN_ORDERS_KEEP_POSITION
    grid: GridConfig = field(default_factory=_default_grid)
    dry_run_rules: SymbolRules = field(default_factory=_default_rules)
    risk_limits: RiskLimits = field(default_factory=_default_limits)

    def __post_init__(self) -> None:
        if not isinstance(self.control_token, SecretValue):
            raise TypeError("control_token must be SecretValue")
        if len(self.control_token.value) < 32:
            raise DomainValidationError("control token must contain at least 32 characters")
        if not self.strategy_id or self.strategy_id != self.strategy_id.strip():
            raise DomainValidationError("strategy_id must be non-empty and trimmed")
        if self.environment not in {"testnet", "mainnet"}:
            raise DomainValidationError("environment must be testnet or mainnet")
        for name, value in (
            ("dry_run", self.dry_run),
            ("order_submission_enabled", self.order_submission_enabled),
            ("live_trading_enabled", self.live_trading_enabled),
        ):
            if not isinstance(value, bool):
                raise TypeError(f"{name} must be bool")
        if self.dry_run and (self.order_submission_enabled or self.live_trading_enabled):
            raise DomainValidationError("dry-run cannot enable exchange submission or live trading")
        if (
            self.environment == "mainnet"
            and self.order_submission_enabled
            and not self.live_trading_enabled
        ):
            raise DomainValidationError(
                "mainnet submission requires the separate live-trading opt-in"
            )
        if self.environment != "mainnet" and self.live_trading_enabled:
            raise DomainValidationError("live_trading_enabled is valid only for mainnet")
        if not self.dry_run and (self.api_key is None or self.api_secret is None):
            raise DomainValidationError("non-dry-run mode requires Binance credentials")
        if (self.api_key is None) != (self.api_secret is None):
            raise DomainValidationError("Binance API key and secret must be supplied together")
        if not isinstance(self.ledger_path, Path):
            raise TypeError("ledger_path must be Path")
        _validate_control_host(self.control_host)
        if isinstance(self.control_port, bool) or not isinstance(self.control_port, int):
            raise TypeError("control_port must be int")
        if not 1 <= self.control_port <= 65_535:
            raise DomainValidationError("control_port must be between 1 and 65535")
        if isinstance(self.queue_capacity, bool) or not isinstance(self.queue_capacity, int):
            raise TypeError("queue_capacity must be int")
        if self.queue_capacity <= 0:
            raise DomainValidationError("queue_capacity must be greater than zero")
        if (
            isinstance(self.reconciliation_interval_seconds, bool)
            or not isinstance(self.reconciliation_interval_seconds, (int, float))
            or not isfinite(float(self.reconciliation_interval_seconds))
            or self.reconciliation_interval_seconds <= 0
        ):
            raise DomainValidationError("reconciliation_interval_seconds must be a positive number")
        if self.grid.symbol != self.risk_limits.symbol:
            raise DomainValidationError("grid and risk policy symbols must match")
        if self.dry_run and self.grid.symbol != self.dry_run_rules.symbol:
            raise DomainValidationError("dry-run symbol rules must match the grid symbol")
        if not isinstance(self.pause_policy, PausePolicy):
            raise TypeError("pause_policy must be PausePolicy")
        if not isinstance(self.stop_policy, StopPolicy):
            raise TypeError("stop_policy must be StopPolicy")

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "ApplicationConfig":
        """Load explicit environment values without reading a dotenv file or mutating globals."""

        source = environ if values is None else values
        token = _required(source, "TRADING_BOT_CONTROL_TOKEN")
        symbol = source.get("TRADING_BOT_SYMBOL", "BTCUSDT")
        reference = _decimal(source, "TRADING_BOT_REFERENCE_PRICE", "60000")
        lower = _decimal(source, "TRADING_BOT_LOWER_PRICE", "50000")
        upper = _decimal(source, "TRADING_BOT_UPPER_PRICE", "70000")
        grid = GridConfig(
            symbol=symbol,
            lower_price=lower,
            upper_price=upper,
            reference_price=reference,
            grid_count=_integer(source, "TRADING_BOT_GRID_COUNT", 4),
            quantity_per_order=_decimal(source, "TRADING_BOT_QUANTITY", "0.001"),
        )
        rules = SymbolRules(
            symbol=symbol,
            tick_size=_decimal(source, "TRADING_BOT_DRY_TICK_SIZE", "0.1"),
            step_size=_decimal(source, "TRADING_BOT_DRY_STEP_SIZE", "0.001"),
            min_qty=_decimal(source, "TRADING_BOT_DRY_MIN_QTY", "0.001"),
            min_notional=_decimal(source, "TRADING_BOT_DRY_MIN_NOTIONAL", "5"),
        )
        limits = RiskLimits(
            symbol=symbol,
            max_abs_position_quantity=_decimal(source, "TRADING_BOT_MAX_POSITION_QUANTITY", "0.01"),
            max_position_notional=_decimal(source, "TRADING_BOT_MAX_POSITION_NOTIONAL", "5000"),
            max_open_orders=_integer(source, "TRADING_BOT_MAX_OPEN_ORDERS", 20),
            max_daily_loss=_decimal(source, "TRADING_BOT_MAX_DAILY_LOSS", "100"),
            max_drawdown=_decimal(source, "TRADING_BOT_MAX_DRAWDOWN", "200"),
            max_abs_funding_rate=_decimal(source, "TRADING_BOT_MAX_ABS_FUNDING_RATE", "0.01"),
            min_liquidation_distance_ratio=_decimal(
                source, "TRADING_BOT_MIN_LIQUIDATION_DISTANCE", "0.05"
            ),
            max_maintenance_margin_ratio=_decimal(
                source, "TRADING_BOT_MAX_MAINTENANCE_MARGIN_RATIO", "0.5"
            ),
            max_market_data_age_ms=_integer(source, "TRADING_BOT_MAX_MARKET_AGE_MS", 15_000),
            max_user_data_age_ms=_integer(source, "TRADING_BOT_MAX_USER_AGE_MS", 15_000),
            soft_lower_price=_decimal(
                source, "TRADING_BOT_SOFT_LOWER_PRICE", str(lower * Decimal("0.98"))
            ),
            soft_upper_price=_decimal(
                source, "TRADING_BOT_SOFT_UPPER_PRICE", str(upper * Decimal("1.02"))
            ),
            hard_lower_price=_decimal(
                source, "TRADING_BOT_HARD_LOWER_PRICE", str(lower * Decimal("0.9"))
            ),
            hard_upper_price=_decimal(
                source, "TRADING_BOT_HARD_UPPER_PRICE", str(upper * Decimal("1.1"))
            ),
            max_leverage=_integer(source, "TRADING_BOT_MAX_LEVERAGE", 1),
        )
        key = source.get("BINANCE_API_KEY")
        secret = source.get("BINANCE_API_SECRET")
        return cls(
            control_token=SecretValue(token),
            strategy_id=source.get("TRADING_BOT_STRATEGY_ID", "neutral-grid"),
            environment=source.get("TRADING_ENV", "testnet").lower(),
            dry_run=_boolean(source, "TRADING_BOT_DRY_RUN", True),
            order_submission_enabled=_boolean(source, "ORDER_SUBMISSION_ENABLED", False),
            live_trading_enabled=_boolean(source, "LIVE_TRADING_ENABLED", False),
            api_key=None if key is None else SecretValue(key),
            api_secret=None if secret is None else SecretValue(secret),
            ledger_path=Path(source.get("TRADING_BOT_LEDGER_PATH", "data/trading_bot.db")),
            control_host=source.get("TRADING_BOT_CONTROL_HOST", "127.0.0.1"),
            control_port=_integer(source, "TRADING_BOT_CONTROL_PORT", 8080),
            queue_capacity=_integer(source, "TRADING_BOT_QUEUE_CAPACITY", 256),
            reconciliation_interval_seconds=_number(
                source, "TRADING_BOT_RECONCILIATION_INTERVAL_SECONDS", 60.0
            ),
            grid=grid,
            dry_run_rules=rules,
            risk_limits=limits,
        )


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name)
    if value is None:
        raise DomainValidationError(f"missing required environment variable {name}")
    return value


def _boolean(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None:
        return default
    normalized = raw.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise DomainValidationError(f"{name} must be true or false")


def _integer(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise DomainValidationError(f"{name} must be an integer") from error


def _number(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise DomainValidationError(f"{name} must be a number") from error


def _decimal(values: Mapping[str, str], name: str, default: str) -> Decimal:
    raw = values.get(name, default)
    try:
        return Decimal(raw)
    except InvalidOperation as error:
        raise DomainValidationError(f"{name} must be a decimal string") from error
