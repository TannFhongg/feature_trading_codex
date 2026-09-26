# Implementation Plan

## P1 — Domain Foundation: COMPLETE

Đã bàn giao Python scaffold, immutable domain models dùng `Decimal`, Neutral Arithmetic Grid,
exchange-filter quantization và 27 unit tests.

## P2 — Simulator & Backtest Core: COMPLETE

Đã bàn giao deterministic simulator, latency/queue/partial-fill model, position accounting,
maker/taker fee, funding, PnL/drawdown reporting và 48 unit-test cases.

## P3 — Binance Public Adapter: COMPLETE

Đã bàn giao Testnet-first public REST/WebSocket, exchange rules, server-time sync, strict Decimal
parsing, bounded retry, routed market streams, stale detection và public Testnet smoke tests.

## P4 — Execution & Persistence: COMPLETE

Đã bàn giao:

1. Immutable execution records và deterministic `client_order_id` theo
   strategy/level/side/cycle, giới hạn đúng 36 ký tự.
2. Async signed REST adapter cho submit, cancel, query, open orders, account trades, positions,
   account, commission, funding income và listen-key lifecycle.
3. Testnet-first configuration; order submission bị khóa mặc định và mainnet cần hai opt-in riêng
   cho submission/live trading.
4. Query-before-retry cho submit/cancel khi timeout hoặc HTTP 503; trạng thái không thể xác định được
   giữ là `UNKNOWN` để đối soát, không gửi mù.
5. User Data Stream có keepalive, ping/pong, bounded queue, reconnect, health snapshot và kiểm tra
   event ordering.
6. Async SQLite execution ledger lưu intent trước network, order/fill/event/account/position/income và
   reconciliation audit; duplicate event/fill bị loại bằng unique business keys.
7. Cancel/fill race không được làm lùi order đã `FILLED`; cumulative executed quantity không giảm.
8. Read-only reconciliation đối chiếu open order, query order bị thiếu, fills, positions, account và
   funding; chỉ `safe_to_resume` khi không còn orphan/unresolved/quantity/position mismatch.
9. 113 tests mặc định đạt trên Python 3.12 và 3.14, 3 public Testnet smoke tests đạt; Ruff, mypy
   strict và pip check đạt.

Private authenticated Testnet chưa chạy vì repository không có credential và không được phép suy diễn
quyền sử dụng tài khoản. Các boundary riêng tư được kiểm tra bằng deterministic fakes theo chính sách
Testnet-or-fake. P4 không bổ sung strategy loop, risk approval, emergency exit hay control API.

## P5 — Risk & Recovery: IN PROGRESS

Đã hoàn thành mốc risk domain/engine đầu tiên:

1. Immutable `RiskLimits`, `RuntimeRiskSnapshot`, decision/violation records dùng `Decimal`.
2. Pre-trade approval cho position quantity, position/open-order notional và open-order count.
3. Runtime gates cho strategy state, reconciliation, stale market/user data và emergency latch.
4. Loss, drawdown, funding, margin ratio, liquidation distance cùng soft/hard price boundaries.
5. Exit `reduceOnly` chỉ được phép khi giảm nghiêm ngặt vị thế hiện tại và không cross qua zero.

Các mốc còn lại của P5 là breaker có latch, restart recovery coordinator, risk-aware executor,
emergency cancel-and-flatten, audit persistence và full quality gate. Order submission vẫn tắt theo
mặc định; P5 không tự bật Testnet hay live trading.
