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

## P5 — Risk & Recovery: COMPLETE

Đã hoàn thành mốc risk domain/engine đầu tiên:

1. Immutable `RiskLimits`, `RuntimeRiskSnapshot`, decision/violation records dùng `Decimal`.
2. Pre-trade approval cho position quantity, position/open-order notional và open-order count.
3. Runtime gates cho strategy state, reconciliation, stale market/user data và emergency latch.
4. Loss, drawdown, funding, margin ratio, liquidation distance cùng soft/hard price boundaries.
5. Exit `reduceOnly` chỉ được phép khi giảm nghiêm ngặt vị thế hiện tại và không cross qua zero.

Mốc recovery/execution safety cũng đã hoàn thành:

6. Breaker latch stale/reconciliation và hard-risk breach; chỉ reset từ snapshot paused/recovering,
   fresh, reconciled và nằm trong toàn bộ hard limits.
7. Risk-managed executor audit quyết định trước khi gọi durable executor; rejected intent không chạm
   network.
8. Restart coordinator luôn reconcile trước, dừng ở `PAUSED` và cần explicit healthy resume.
9. Emergency action persist-before-mutation, cancel-all, refetch one-way position rồi submit MARKET
   `reduceOnly` với deterministic client ID và query-before-retry.
10. SQLite audit risk/emergency state cùng forward migration cho P4 position/account risk fields.

P5 đạt final gate với 144 tests mặc định trên cả Python 3.12/3.14, 28 focused risk/recovery tests,
Ruff, format, mypy strict và pip check đều đạt. Chi tiết tại `P5_REPORT.md`.

## P6 — Application Runtime, Control API & Observability: COMPLETE

P6 đã ghép các component P1–P5 thành một ứng dụng async chạy được mà không tạo đường tắt quanh risk,
ledger hoặc reconciliation. Chi tiết implementation và bằng chứng tại `P6_REPORT.md`.

### Delivered Scope

1. Tạo application entry point và composition root để nạp config, khởi tạo ledger, Binance adapters,
   strategy, risk engine, reconciler, control API và observability.
2. Tạo application orchestrator sở hữu lifecycle cùng toàn bộ async task. Startup phải validate cấu
   hình, mở dependency, chạy recovery/reconciliation và dừng ở `PAUSED`; chỉ explicit healthy command
   mới được chuyển sang `RUNNING`.
3. Tạo strategy runtime loop nhận market/user events, duy trì runtime state, triển khai Neutral
   Arithmetic Grid và tạo replacement intent từ confirmed fill/partial fill. Strategy runtime không
   được gọi exchange hoặc persistence trực tiếp.
4. Bắt buộc mọi intent tăng exposure đi qua P5 risk-managed executor và P4 durable execution
   boundary. Control API không được có đường tắt tới private submit.
5. Supervise stream, strategy, reconciliation và persistence tasks; bounded queue/backpressure, task
   failure, stale data và resource failure phải fail closed, latch breaker khi phù hợp và không để
   background task chết âm thầm.
6. Cung cấp state-machine command idempotent cho status, start, pause, resume, stop và emergency stop;
   định nghĩa rõ pause/stop policy đối với open orders và position.
7. Shutdown theo thứ tự: ngừng tạo intent mới, áp dụng stop policy, chờ/cancel task có kiểm soát,
   flush ledger rồi đóng stream, HTTP client và database.
8. Bổ sung structured logs, health/readiness, metrics và alerts cho process death, stale stream,
   reconciliation mismatch, risk/breaker, unknown order và khoảng cách liquidation; dữ liệu nhạy cảm
   phải được redact.
9. Control API mặc định chỉ bind local/private interface, có authentication/authorization cho command
   thay đổi trạng thái và audit mọi command. API không được bật submission/live mode động.
10. Thêm deterministic end-to-end tests bằng fakes cho startup/recovery, initial grid, fill→replacement,
    pause/resume/stop, duplicate command/event, task crash, backpressure và graceful shutdown.

### Definition of Done

- Có executable application entry point, dry-run mode và cấu hình Testnet-first; order submission và
  live trading vẫn tắt mặc định.
- Một process có thể đi qua `STARTING → RECOVERING → PAUSED → RUNNING`, vận hành strategy loop và
  dừng an toàn với deterministic fakes mà không bypass risk, ledger hoặc reconciliation.
- Không tự resume sau restart; không tăng exposure khi stale, unreconciled, breaker latch hoặc runtime
  dependency không healthy.
- Control commands idempotent, được audit và tuân thủ state machine; observability chứng minh được
  lifecycle, order, risk và recovery state mà không làm lộ secret/account payload.
- Focused runtime/API tests, full pytest, Ruff, format, mypy strict và dependency check đều đạt; tài
  liệu phase và báo cáo P6 được cập nhật cùng implementation.
- Authenticated Testnet soak, fault injection dài hạn và live trading không thuộc P6; chúng vẫn là gate
  P7/P8 riêng.

### Verification Evidence

- Executable `trading-bot`/`python -m trading_bot`, dry-run deterministic và composition root cho
  Testnet/mainnet opt-in đã có; import package không tạo I/O.
- Startup đi qua validate/recover và dừng ở `PAUSED`; resume explicit đối soát lại, kiểm tra snapshot
  fresh/healthy rồi mới tạo initial grid qua P5/P4 boundaries.
- Control API bearer-auth local/private, command ID durable/idempotent, runtime audit, structured log
  redaction, health/readiness, Prometheus text metrics và alert sink đã có test.
- 17 focused P6 tests bao phủ startup, initial grid, fill→replacement, partial fill, pause/resume/stop,
  emergency stop, duplicate command/event, reconnect, task crash, backpressure, stale feed và shutdown.
- Full suite đạt 161 passed, 3 public integration tests skipped theo thiết kế trên Python 3.12 và
  Python 3.14; Ruff, format, mypy strict và pip check đạt.

## Phase Boundary

P6 — Application Runtime, Control API & Observability đã `COMPLETE`. Repository ở `P6_COMPLETE` và
checkpoint tiếp theo là P7 authenticated Testnet soak/fault injection sau khi hoàn tất credential,
dependency-lock/scanning và deployment gates. Order submission/live trading vẫn tắt mặc định; hoàn
thành P6 không tự cho phép Testnet soak hay mở P8 live canary.
