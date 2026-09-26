"""The sole composition root for P6 runtime, adapters, API, and observability."""

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from trading_bot.application.config import ApplicationConfig, SecretValue
from trading_bot.application.control import ControlService, create_control_api
from trading_bot.application.dry_run import (
    DryRunExchangeGateway,
    DryRunMarketSource,
    DryRunUserSource,
)
from trading_bot.application.observability import (
    LoggingAlertSink,
    RuntimeMetrics,
    configure_structured_logging,
)
from trading_bot.application.runtime import (
    ApplicationOrchestrator,
    MarketEventSource,
    UserEventSource,
)
from trading_bot.application.state import RuntimeStateStore
from trading_bot.application.strategy_runtime import NeutralGridRuntime
from trading_bot.binance import (
    BinanceCredentials,
    BinanceMarketStream,
    BinancePrivateConfig,
    BinancePrivateRestClient,
    BinancePublicConfig,
    BinancePublicRestClient,
    BinanceUserDataStream,
    MarketStreamKind,
)
from trading_bot.execution import ExecutionReconciler, PersistentOrderExecutor
from trading_bot.persistence import SqliteExecutionLedger
from trading_bot.risk import (
    EmergencyExitCoordinator,
    RestartRecoveryCoordinator,
    RiskCircuitBreaker,
    RiskEngine,
    RiskManagedOrderExecutor,
)
from trading_bot.strategy import generate_arithmetic_grid


@dataclass(slots=True)
class BuiltApplication:
    """Owned resources returned by the composition root for serving or deterministic tests."""

    config: ApplicationConfig
    runtime: ApplicationOrchestrator
    control: ControlService
    api: FastAPI
    ledger: SqliteExecutionLedger
    public_client: BinancePublicRestClient | None = None
    private_client: BinancePrivateRestClient | None = None
    dry_run_gateway: DryRunExchangeGateway | None = None
    _closed: bool = False

    async def close(self) -> None:
        if self._closed:
            return
        try:
            await self.runtime.shutdown()
        finally:
            if self.private_client is not None:
                await self.private_client.close()
            if self.public_client is not None:
                await self.public_client.close()
            await self.ledger.close()
            self._closed = True


async def build_application(config: ApplicationConfig) -> BuiltApplication:
    """Create every dependency explicitly; importing modules has no network or DB side effect."""

    configure_structured_logging()
    if config.ledger_path != Path(":memory:"):
        config.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = await SqliteExecutionLedger.open(config.ledger_path)
    public_client: BinancePublicRestClient | None = None
    private_client: BinancePrivateRestClient | None = None
    try:
        gateway: DryRunExchangeGateway | BinancePrivateRestClient
        if config.dry_run:
            rules = config.dry_run_rules
            gateway = DryRunExchangeGateway(config.grid.symbol, config.grid.reference_price)
            market_source: MarketEventSource = DryRunMarketSource(
                config.grid.symbol,
                config.grid.reference_price,
            )
            user_source: UserEventSource = DryRunUserSource(gateway)
        else:
            credentials = BinanceCredentials(
                api_key=_secret_value(config.api_key),
                api_secret=_secret_value(config.api_secret),
            )
            public_config = (
                BinancePublicConfig.mainnet()
                if config.environment == "mainnet"
                else BinancePublicConfig()
            )
            private_config = _private_config(config, credentials)
            public_client = BinancePublicRestClient(public_config)
            private_client = BinancePrivateRestClient(private_config)
            time_sync = await public_client.synchronize_time()
            private_client.update_time_sync(time_sync)
            rules = await public_client.fetch_symbol_rules(config.grid.symbol)
            gateway = private_client
            market_source = BinanceMarketStream(
                config.grid.symbol,
                (MarketStreamKind.AGGREGATE_TRADE, MarketStreamKind.MARK_PRICE),
                public_config,
            )
            user_source = BinanceUserDataStream(private_client, private_config)

        plan = generate_arithmetic_grid(config.grid, rules)
        strategy = NeutralGridRuntime(config.strategy_id, plan, rules)
        breaker = RiskCircuitBreaker(config.risk_limits)
        risk_engine = RiskEngine(config.risk_limits)
        persistent_executor = PersistentOrderExecutor(gateway, ledger)
        risk_executor = RiskManagedOrderExecutor(
            risk_engine,
            breaker,
            persistent_executor,
            ledger,
        )
        reconciler = ExecutionReconciler(gateway, ledger)
        recovery = RestartRecoveryCoordinator(reconciler, breaker, ledger)
        emergency = EmergencyExitCoordinator(gateway, ledger, breaker)
        state = RuntimeStateStore(
            config.risk_limits,
            config.grid.reference_price,
            leverage=config.risk_limits.max_leverage,
        )
        metrics = RuntimeMetrics()
        runtime = ApplicationOrchestrator(
            config,
            state,
            strategy,
            risk_executor,
            persistent_executor,
            recovery,
            reconciler,
            emergency,
            breaker,
            ledger,
            market_source,
            user_source,
            metrics=metrics,
            alerts=LoggingAlertSink(),
        )
        control = ControlService(runtime, ledger)
        api = create_control_api(runtime, control, config.control_token)
        return BuiltApplication(
            config=config,
            runtime=runtime,
            control=control,
            api=api,
            ledger=ledger,
            public_client=public_client,
            private_client=private_client,
            dry_run_gateway=gateway if isinstance(gateway, DryRunExchangeGateway) else None,
        )
    except BaseException:
        if private_client is not None:
            await private_client.close()
        if public_client is not None:
            await public_client.close()
        await ledger.close()
        raise


def _private_config(
    config: ApplicationConfig,
    credentials: BinanceCredentials,
) -> BinancePrivateConfig:
    if config.environment == "mainnet":
        return BinancePrivateConfig.mainnet(
            credentials,
            order_submission_enabled=config.order_submission_enabled,
            live_trading_enabled=config.live_trading_enabled,
        )
    return BinancePrivateConfig(
        credentials=credentials,
        order_submission_enabled=config.order_submission_enabled,
        live_trading_enabled=config.live_trading_enabled,
    )


def _secret_value(value: SecretValue | None) -> str:
    if value is None:
        raise ValueError("required secret is unavailable")
    return value.value
