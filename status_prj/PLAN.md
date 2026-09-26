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

## P3 — Binance Public Adapter: COMPLETE

Đã bàn giao:

1. Async public REST client, mặc định dùng USDⓈ-M Futures Testnet và không nhận credential.
2. Strict parser cho `exchangeInfo`, chỉ chấp nhận active USDT perpetual và lấy đúng `tickSize`,
   `stepSize`, `minQty`, `notional` từ filters.
3. Server-time synchronization theo midpoint, chọn mẫu có round-trip time thấp nhất.
4. Retry có giới hạn cho public GET, exponential backoff, tôn trọng `Retry-After` và không retry khi
   Binance trả `418`.
5. Typed WebSocket events dùng `Decimal` cho aggregate trade, mark price/funding và best bid/ask.
6. Route split hiện hành: aggregate trade/mark price qua `/market`, book ticker qua `/public`.
7. Automatic ping/pong, bounded queue, stale detection, reconnect backoff và health snapshot.
8. Reject event đi lùi theo từng stream type; stream Public và Market chạy trên connection riêng.
9. 74 unit tests và 3 public Testnet integration tests; toàn bộ quality gates đạt.

## Phase Boundary

P3 đã hoàn thành. P4 — Execution & Persistence vẫn là `PLANNED/NOT_STARTED`; chưa có signed REST,
User Data Stream, submit/cancel order, database, event ledger hoặc reconciliation. Cần yêu cầu mới của
chủ dự án trước khi bắt đầu P4.

## P4 Preview — Chưa triển khai

P4 dự kiến bổ sung deterministic client order ID, private order/user-data adapter, idempotent event
ledger, persistence và reconciliation. Preview này không cho phép gửi lệnh hoặc dùng credential.
