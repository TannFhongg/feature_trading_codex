# Repository Guidelines

## Project Structure & Module Organization

This repository is currently in the planning stage; `instruction.md` is the source of truth for the Binance USDⓈ-M Futures Grid Bot design. As implementation begins, use this layout:

- `src/trading_bot/`: application code.
- `src/trading_bot/binance/`: REST/WebSocket exchange adapter.
- `src/trading_bot/strategy/`: grid generation and order lifecycle logic.
- `src/trading_bot/risk/`: exposure limits, circuit breakers, and emergency exits.
- `src/trading_bot/persistence/`: database models and reconciliation.
- `src/trading_bot/api/`: control API and health endpoints.
- `tests/unit/` and `tests/integration/`: isolated and exchange-facing tests.
- `migrations/`: database migrations; `config/`: non-secret configuration examples.

Keep exchange-specific code out of strategy modules. Treat persisted orders, fills, and risk events as auditable records.

## Build, Test, and Development Commands

There is no executable scaffold yet. Once `pyproject.toml` is added, standardize on:

```powershell
python -m venv .venv
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
mypy src
uvicorn trading_bot.api:app --reload
```

Use `pytest tests/integration -m integration` for Binance Testnet tests. Never point automated tests at a production account.

## Coding Style & Naming Conventions

Use four-space indentation, explicit type hints, and small single-purpose modules. Use `snake_case` for functions/files, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Use `Decimal` for prices, quantities, fees, and PnL; never use binary floating point for trading calculations. Keep network and database boundaries asynchronous and make order-processing operations idempotent.

## Testing Guidelines

Use pytest and name files `test_<behavior>.py`. Cover grid rounding, partial fills, duplicate/out-of-order events, timeout recovery, position limits, and state transitions. Every execution or risk-control change requires unit tests and at least one reconciliation scenario. Integration tests must use Binance Futures Testnet or a deterministic fake exchange.

## Commit & Pull Request Guidelines

No Git history exists yet. Use Conventional Commits, such as `feat(strategy): add arithmetic grid` or `fix(execution): reconcile unknown order status`. Keep commits scoped and independently testable.

Pull requests must describe behavior changes, trading/risk impact, tests performed, and configuration or migration changes. Link relevant issues and include API examples when endpoints change.

## Security & Configuration

Never commit API keys, account data, logs, `.env` files, or production configuration. Provide `.env.example` with placeholders. API keys must disable withdrawals, use IP restrictions, and default to Testnet and live trading disabled.
