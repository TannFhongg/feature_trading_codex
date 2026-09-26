# Implementation Plan

## P1 — Domain Foundation: COMPLETE

Đã bàn giao:

1. `pyproject.toml`, package `src/trading_bot/` và local editable environment.
2. Cấu hình pytest, Ruff, mypy và `.env.example` an toàn.
3. `GridConfig`, `SymbolRules`, `GridLevel`, `GridPlan` và lifecycle enums.
4. Arithmetic Grid calculator chỉ dùng `Decimal`.
5. Tick/step quantization, minQty/minNotional và symbol validation.
6. Neutral BUY/SELL assignment với đúng một inactive anchor level.
7. 27 unit tests; toàn bộ quality gates đạt.

## P2 — Simulator & Backtest Core: COMPLETE

Đã bàn giao:

1. Typed market trade, funding event, order intent, fill và report records chỉ dùng `Decimal`.
2. Deterministic resting-order simulator với aggressor side, price priority và volume allocation.
3. Configurable latency và conservative queue-ahead; replacement không fill trên event tạo nó.
4. Partial-fill replacement đúng filled quantity và logical-order aggregation theo level/side.
5. One-way position ledger cho long, short, close và position flip.
6. Maker/taker fee, funding payment và entry/exit fee allocation cho completed grid trade.
7. Báo cáo PnL, grid profit, drawdown, inventory, notional, fill ratio và funding/PnL ratio.
8. 48 unit-test cases; pytest, Ruff, mypy strict và pip check đều đạt.

## Phase Boundary

Không có phase triển khai nào đang được phép tiếp tục trong phạm vi hiện tại. P3 — Binance Public
Adapter vẫn là `PLANNED/NOT_STARTED`; cần yêu cầu mới trước khi thêm network/Testnet code.

## P3 Preview — Chưa triển khai

Khi được phê duyệt, P3 dự kiến bổ sung exchange rules, server-time synchronization, public market
stream, reconnect/stale detection và Testnet-facing integration tests. Preview này không phải bằng
chứng P3 đã bắt đầu.
