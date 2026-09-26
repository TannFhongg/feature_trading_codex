# Repository Guidelines

## Current Scope and Sources of Truth

The repository is at `P6_COMPLETE`. It implements a typed domain model, Neutral Arithmetic Grid,
deterministic simulator/backtester, Binance USDⓈ-M public and private adapters, an idempotent execution
ledger/reconciler, pre-trade risk/recovery controls, and a supervised application runtime with an
authenticated Control API and observability. P7 authenticated Testnet soak is planned but not started.

- `instruction.md` is the product and safety blueprint.
- `status_prj/STATUS.md` and `status_prj/PHASES.md` define current scope and phase state.
- `status_prj/CONTRACTS.md` defines implemented contracts and explicitly labelled planned contracts.
- `status_prj/RISKS.md` and `status_prj/SECURITY.md` contain active delivery and safety gates.

Do not treat planned components as implemented or start a new phase implicitly. Update the relevant
`status_prj/` files in the same change whenever phase, scope, contracts, risks, schedule, or security
posture changes.

## Structure and Boundaries

- `src/trading_bot/domain/`: dependency-free enums, validation errors, and immutable models.
- `src/trading_bot/strategy/`: exchange-independent grid generation and quantization; it creates
  plans/intents and must not call exchange or persistence code.
- `src/trading_bot/simulator/`: deterministic fills, position accounting, backtests, and reports.
- `src/trading_bot/binance/`: async public/private REST and WebSocket adapters and strict parsers.
- `src/trading_bot/execution/`: order models, durable execution service, and read-only reconciliation.
- `src/trading_bot/persistence/`: async SQLite ledger for the current single-process/local scope.
- `src/trading_bot/risk/`: pre-trade limits, breaker, recovery, audited execution, and emergency exit.
- `src/trading_bot/application/`: strict config, composition root, strategy runtime, supervised
  lifecycle, Control API, dry-run adapters, metrics, structured logging, and alerts.
- `tests/unit/`: deterministic unit/regression tests using fakes at network boundaries.
- `tests/integration/`: opt-in public Binance Futures Testnet smoke tests.
- `status_prj/`: phase reports, plan, contracts, risks, security posture, and delivery evidence.

The executable is `trading-bot` (or `python -m trading_bot`). It requires an explicit control token;
dry-run is the default and exchange submission/live trading remain disabled by default. There is no
CI/CD, dependency lockfile, deployment tooling, authenticated Testnet soak, or live approval.

## Trading and Persistence Invariants

- Use `Decimal` for every price, quantity, fee, funding value, balance, margin, and PnL. Public
  financial inputs must reject binary floats.
- Preserve exchange-filter validation and quantization using `tick_size`, `step_size`, `min_qty`, and
  `min_notional`; do not substitute precision fields for increments.
- Keep network and database boundaries asynchronous. Preserve deterministic event ordering, stable
  identifiers, and auditable records.
- Persist an order intent before a network submission. A timeout or HTTP 503 must query the same
  deterministic `client_order_id` before bounded retry; never blind-retry or change the ID. Keep an
  unresolved result `UNKNOWN` for reconciliation.
- Order/event/fill processing must remain idempotent. Do not regress terminal order state or cumulative
  executed quantity, and retain the Binance-supported 36-character client-order-ID limit.
- Reconciliation is read-only with respect to exchange mutations. Restart must reconcile first, stop
  at `PAUSED`, and require an explicit healthy resume; never auto-resume.
- Every exposure-increasing intent must pass the risk-managed durable execution boundary. Emergency
  closing orders must remain deterministic, one-way-safe, and `reduceOnly`.

## Environment and Quality Commands

The package requires Python 3.12 or newer and uses a setuptools `src/` layout. Runtime dependencies
are bounded `fastapi`, `uvicorn`, `httpx`, and `websockets`; development dependencies are declared in
`pyproject.toml`. There is no dependency lockfile or task runner.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m mypy src
.\.venv\Scripts\python -m pip check
```

Validate runtime configuration without opening network/database resources:

```powershell
$env:TRADING_BOT_CONTROL_TOKEN = "<at-least-32-random-characters>"
.\.venv\Scripts\python -m trading_bot --check-config
```

Run focused tests while iterating and the complete quality set before handoff. The default pytest run
skips public network smoke tests. Run those explicitly only when network Testnet validation is in scope:

```powershell
$env:RUN_BINANCE_TESTNET = "1"
.\.venv\Scripts\python -m pytest tests\integration -m integration
```

Public integration tests require no credentials. Private exchange behavior currently uses deterministic
fakes; never point automated tests at a production account.

## Coding and Testing Conventions

Use four-space indentation, explicit type hints, small single-purpose modules, `snake_case` for files
and functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Ruff enforces a
100-character line length, double quotes, imports, and the configured lint rules; mypy is strict.

Name tests `test_<behavior>.py`. Add focused regression tests for every behavior change, especially
rounding, partial fills, latency/queue handling, fee/funding accounting, event ordering, duplicate
events, timeout recovery, cancel/fill races, reconciliation, risk limits, restart, and emergency paths.
Use Binance Futures Testnet or deterministic fakes for exchange-facing tests, never production.

Do not commit generated artifacts such as `__pycache__/`, `*.egg-info/`, tool caches, virtual
environments, logs, databases, coverage output, or local data.

## Security and Delivery

Never commit API keys, signatures, account data, sensitive trading data, `.env` files, or production
configuration. Keep `.env.example` placeholder-only. Config remains Testnet-first, order submission is
disabled by default, and mainnet submission requires separate explicit submission and live-trading
opt-ins. Any supplied API key must have withdrawals disabled and use IP restrictions where available.

Use Conventional Commits, for example `feat(strategy): add arithmetic grid` or
`fix(execution): reconcile unknown order`. Keep commits scoped and independently testable. Pull
requests must state behavior and trading/risk impact, tests run, and any configuration, migration,
contract, phase, or security changes.
