# P6 Application Runtime, Control API & Observability — Completion Report

## Report Metadata

| Thuộc tính | Giá trị |
|---|---|
| Phase | P6 — Application Runtime, Control API & Observability |
| Trạng thái | `COMPLETE` |
| Ngày xác minh | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Exchange scope | Binance USDⓈ-M Futures, Testnet-first |
| Private network evidence | Deterministic dry-run/fakes; không dùng credential/account thật |
| Trading posture | Order submission và live trading tắt mặc định |
| Phase tiếp theo | P7 Testnet Soak (`PLANNED/NOT_STARTED`) |

## 1. Application & Configuration

- Có console entry point `trading-bot` và `python -m trading_bot`; `--check-config` không mở database
  hay network, `--dry-run` cưỡng chế tắt exchange submission/live trading.
- `ApplicationConfig` immutable, nạp Decimal từ string, yêu cầu control token tối thiểu 32 ký tự,
  mặc định Testnet + dry-run và chỉ cho Control API bind `localhost`/loopback/private literal IP.
- Mainnet submission vẫn cần hai startup opt-in độc lập: `order_submission_enabled` và
  `live_trading_enabled`. Control API không có endpoint thay đổi hai cờ này.
- Composition root là nơi duy nhất mở SQLite, HTTP/WebSocket adapter, grid, risk, reconciliation,
  runtime, API và observability. Import package không tạo I/O side effect.
- Runtime dependency mới được bounded trong `pyproject.toml`: FastAPI và Uvicorn; không thêm lockfile
  vì dependency locking/scanning vẫn là gate trước P7 soak.

## 2. Runtime Lifecycle & Supervision

- Startup đi qua `VALIDATING → STARTING → RECOVERING`, chạy bounded read-only reconciliation và dừng
  ở `PAUSED`; không auto-resume sau start/restart.
- Mọi lần resume là explicit command, chạy reconciliation lại, yêu cầu market/user data fresh,
  One-way/Isolated/leverage hợp lệ và breaker reset thành công trước khi chuyển `RUNNING`.
- Orchestrator sở hữu sáu supervised tasks: market producer/worker, user producer/worker, periodic
  reconciler và stale watchdog. Market/user queue có capacity giới hạn; overflow hoặc task crash đưa
  runtime về `ERROR`, latch breaker, thử hủy grid order và ghi runtime audit.
- Execution lock serialize submit với pause/stop/emergency transition, nên không có intent mới lọt qua
  sau khi control policy bắt đầu. User event được ghi idempotent vào ledger trước khi strategy xử lý.
- Normal pause/stop policy là `CANCEL_OPEN_ORDERS_KEEP_POSITION`; chỉ emergency stop mới gọi P5
  persist-before-mutation cancel-all + MARKET `reduceOnly` flatten.
- Shutdown chuyển `STOPPING`, hủy grid order, dừng producer, drain queue đã nhận, dừng worker rồi
  composition root mới đóng private/public HTTP client và SQLite. Incomplete shutdown được audit/alert,
  không bị báo sai là complete.

## 3. Neutral Grid Runtime

- Strategy runtime phục hồi ownership/cycle từ toàn bộ order history nhưng không đọc ledger trực tiếp.
- Initial grid chỉ phát tối đa một active logical order cho mỗi `(level, side)`.
- Confirmed partial/full `TRADE` được deduplicate theo event ID, tích lũy quantity theo exchange
  `step_size`/`min_qty`/`min_notional`, rồi phát adjacent opposite-side replacement với cycle mới.
- Strategy chỉ trả về immutable `OrderRequest`. Orchestrator bắt buộc gọi P5
  `RiskManagedOrderExecutor`, sau đó P4 `PersistentOrderExecutor`; không có đường gọi private adapter
  trực tiếp từ strategy hoặc Control API.
- Dry-run gateway mô phỏng submit/cancel/fill/account stream hoàn toàn local để kiểm tra luồng
  startup → initial grid → fill → replacement → pause/resume/stop mà không gửi Binance request.

## 4. Control API & Durable Audit

Các endpoint chỉ trả dữ liệu vận hành đã sanitize:

- `GET /health`, `GET /ready`, `GET /status`, `GET /metrics`.
- `POST /commands/status|start|pause|resume|stop|emergency-stop`.

Command thay đổi/đọc state qua command endpoint cần bearer token so sánh constant-time. Body chỉ nhận
`command_id`; extra field bị reject. SQLite claim command ID trước state transition, chống rebind cùng ID
sang command/actor khác và replay terminal result mà không thực thi lại. Bảng `runtime_events` lưu state,
component và reason code đã sanitize; không lưu raw exception, credential hoặc account payload.

## 5. Observability

- JSON-line formatter redact key/token/signature/authorization/listen-key/account/client-order fields và
  chỉ ghi exception type, không ghi raw exception payload.
- Health/readiness phản ánh state, reconciliation, breaker, stream age, queue/task count và open-order
  count. Readiness trả HTTP 503 khi chưa đủ điều kiện vận hành.
- Dependency-free Prometheus text metrics ghi lifecycle, queue, event, order, reconciliation, breaker,
  task và shutdown counters/gauges.
- `AlertSink` nhận sanitized alert cho task crash, backpressure, stale/reconciliation mismatch và
  incomplete shutdown; implementation mặc định ghi structured critical alert. External Telegram/Slack
  delivery và external process-death monitor vẫn là deployment work trước/during P7.

## 6. Test & Quality Evidence

| Kiểm tra | Kết quả |
|---|---|
| Full pytest Python 3.12.10 | 161 passed, 3 public integration tests skipped |
| Full pytest Python 3.14.0 | 161 passed, 3 public integration tests skipped |
| Focused P6 runtime/API | 17 passed |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 86 files checked |
| `mypy src` | Passed strict; 51 source files |
| `python -m pip check` | Passed |

Focused coverage gồm config/mainnet opt-ins, secret redaction, metrics, initial/replacement/partial-fill,
cycle restore, command/runtime persistence, no-auto-resume, reconnect reconciliation gate,
pause/resume/stop, emergency flatten, duplicate command/event, Control API auth, task crash, queue
overflow, stale watchdog và graceful shutdown. Public network smoke không cần chạy lại vì P6 không
thay đổi P3 public adapter; authenticated private Testnet không được chạy khi chưa có credential được
cấp.

## 7. Security & Trading Impact

- Không thêm API key, signature, account data hoặc production configuration vào repository.
- `.env.example` chỉ chứa placeholder; control token thật là required runtime secret và `.env*` tiếp
  tục bị ignore ngoại trừ file example.
- Dry-run là mặc định. Live composition có thể đọc Testnet credential nhưng submit vẫn khóa nếu không
  có startup opt-in; mainnet cần thêm live opt-in riêng.
- Không có Binance private/Testnet/live order nào được gửi trong P6. Toàn bộ mutation evidence dùng
  in-memory fake và vẫn đi qua risk/durable contracts tương ứng.
- SQLite vẫn là single-process/local, Control API chưa có TLS termination và alert delivery mặc định
  vẫn local; chỉ bind private interface và cần deployment hardening trước soak.

## 8. Phase Boundary

P6 đạt exit criteria ở mức deterministic application/runtime/API. P7 authenticated Testnet soak chưa
bắt đầu và cần credential Testnet an toàn, withdrawal disabled/IP restriction được xác minh, dependency
lock/vulnerability scan, backup/restore decision, external alert/process monitor và fault-injection/soak
runbook. P6 completion không tự bật P7 hoặc cho phép P8 live canary.
