# Project Contracts

Trạng thái tài liệu: `P6_IMPLEMENTED`. Domain, Strategy, Simulator/Backtest, Binance adapters,
Persistence/Reconciliation, Risk/Recovery và Application Runtime/Control contracts đều có
deterministic test evidence.

## Domain Contract

- Mọi giá, quantity, fee, funding, balance và PnL dùng `Decimal`; public financial inputs reject
  binary float.
- Grid config và `SymbolRules` giữ các invariant về price range, symbol, `tick_size`, `step_size`,
  `min_qty` và `min_notional`.
- P1 chỉ enable Arithmetic Neutral Grid; planned enum values bị reject khi tạo config.
- Config/domain records là immutable và validate tại construction boundary.

## Strategy Contract

- Strategy chỉ tạo plan/intent, không gọi exchange hoặc persistence.
- BUY nằm dưới reference price; SELL nằm trên reference price; có đúng một inactive anchor.
- Price/quantity được quantize theo exchange increments và phải đạt min quantity/notional.
- Một strategy/level/side chỉ có tối đa một active logical order.

## Simulator & Backtest Contract

- Input event phải theo thứ tự thời gian; out-of-order event bị reject.
- Fill dùng aggressor side, price priority, market-volume allocation, configurable latency và
  conservative queue-ahead; replacement không fill trên event sinh ra nó.
- One-way position accounting xử lý long, short, close và flip; fee/funding/PnL được tách riêng.
- Backtest không coi OHLC candle là bằng chứng fill và không mô phỏng liquidation/order book đầy đủ.

## Binance Public Adapter Contract — IMPLEMENTED P3

- Network boundary là async và mặc định dùng USDⓈ-M Futures Testnet.
- Exchange rules lấy từ filters, không dùng precision fields thay cho tick/step increments.
- Public retry có giới hạn, tôn trọng `Retry-After`, fail ngay trên `418`.
- Aggregate trade, mark/funding và book ticker là immutable typed events dùng `Decimal`.
- Routed WebSocket có ping/pong, bounded queue, stale detection, reconnect và health snapshot.
- Event time không được đi lùi trong cùng stream type; payload sai schema/symbol bị fail closed.

## Private Execution Adapter Contract — IMPLEMENTED P4

- Mọi private REST/User Data Stream boundary là async; cấu hình mặc định dùng Testnet.
- Credential không xuất hiện trong `repr`; signing dùng HMAC-SHA256 trên percent-encoded parameters,
  clock offset, bounded `recvWindow` và API-key header.
- Order submission tắt mặc định. Mainnet submission chỉ hợp lệ khi bật riêng cả submission và live
  mode; P4 không tự nạp credential hoặc bật runtime.
- `client_order_id` deterministic, ổn định và duy nhất theo strategy/level/side/cycle, kể cả khi
  strategy ID cần normalize; độ dài tối đa 36 ký tự.
- `PENDING_SUBMIT`, `UNKNOWN` và `SUBMISSION_REJECTED` là local-only; exchange `REJECTED` là một
  terminal status riêng.
- Executor phải persist intent trước network call. Submit/cancel timeout hoặc HTTP 503 phải query cùng
  client ID trước khi retry; retry có giới hạn và không đổi ID.
- Executor không được gửi lại một intent đã tồn tại trong ledger; caller phải query/reconcile hoặc tạo
  cycle mới.
- Nếu query không xác định được kết quả, order local chuyển `UNKNOWN`; không được blind retry.
- REST hỗ trợ submit/query/cancel/open orders, account trades, positions, account totals, actual
  commission rates, income records và listen-key lifecycle.
- User Data Stream là nguồn event thời gian thực; REST là nguồn đối soát. Stream có keepalive,
  ping/pong, bounded queue, reconnect và per-event-type ordering check.

## Persistence & Reconciliation Contract — IMPLEMENTED P4

- SQLite là backend P4 local/single-process. Mọi public database operation là async và serialization
  được bảo vệ bằng lock; file mode bật WAL.
- Ledger lưu orders, fills, exchange events, positions, balances, account snapshots, income records
  và reconciliation reports; số tài chính lưu dạng decimal text.
- Order intent insert là idempotent. Unique constraints bảo vệ client ID, exchange order ID, trade
  business key và active logical `(strategy_id, level_index, side)`.
- Exchange event được deduplicate trước state transition; fill duy nhất theo `(symbol, trade_id)`.
- Exchange snapshot không được thay đổi immutable fields của persisted owned intent. Duplicate
  fill/income key với financial payload khác phải fail closed.
- Terminal exchange status không được trở lại active; late fill progress vẫn được ghi, executed
  quantity không được giảm và timestamp local của pending intent không được che exchange timestamp
  đầu tiên.
- Reconciliation không submit/cancel order. Nó lấy open orders, query local order bị thiếu, account
  trades, positions, account snapshot và funding income rồi áp dụng idempotently.
- `safe_to_resume` chỉ true khi không có orphan remote order, unresolved local order, executed-quantity
  mismatch hoặc position quantity/entry-price mismatch.
- Ledger tồn tại qua close/reopen. P4 chưa cam kết multi-process HA, schema migration framework,
  backup/restore hoặc PostgreSQL.

## Pre-trade Risk Contract — IMPLEMENTED P5

- Mọi giá trị tài chính trong policy/snapshot/decision dùng `Decimal`; binary float bị reject.
- Lệnh thường được xem là có khả năng tăng exposure và chỉ được duyệt khi strategy `RUNNING`, market
  và user data còn fresh, reconciliation an toàn và emergency stop chưa latch.
- One-way Mode, Isolated Margin và leverage không vượt policy cap là điều kiện bắt buộc cho entry.
- Position quantity, tổng position/open-order notional và số open order được kiểm tra trước submit.
- Daily loss, drawdown, funding rate, maintenance-margin ratio, liquidation distance và price boundary
  đều có giới hạn fail-closed; hard breach yêu cầu emergency stop.
- `reduceOnly` chỉ bypass entry gates khi side/quantity thực sự giảm vị thế one-way hiện tại mà không
  cross qua zero; request sai bị reject.

## Runtime Recovery Contract — IMPLEMENTED P5

- Risk-managed executor persist audit decision trước khi gọi durable executor; rejected intent không
  được record order intent hoặc gọi exchange.
- Stale market/user data, unsafe reconciliation và emergency-severity breach latch breaker. Reset chỉ
  thành công từ snapshot `PAUSED`/`RECOVERING`, reconciled, fresh và trong mọi hard limit.
- Restart bắt đầu ở `RECOVERING`, chạy read-only reconciliation, chuyển sang `PAUSED` khi sạch và chỉ
  chuyển `RUNNING` sau explicit healthy resume; không auto-resume.
- Emergency action ID là idempotency key được persist trước mutation. Duplicate action không được
  cancel hoặc submit lần hai.
- Emergency sequence là cancel-all → xác nhận không còn open order khi kết quả mơ hồ → refetch
  position → MARKET `reduceOnly` theo side đối nghịch và đúng toàn bộ one-way position quantity.
- Emergency client order ID deterministic, tối đa 36 ký tự; ambiguous submit dùng query-before-retry.
- Emergency outcome `UNKNOWN` bắt buộc reconciliation và chỉ được chuyển sang kết quả xác định khi
  client ID không đổi; `SUBMITTED` không tự được coi là flat hoặc safe to resume.
- Risk decision, breaker, recovery và emergency transition có audit record idempotent trong SQLite.
- P4 SQLite file được forward-migrate để giữ mark/liquidation/notional/initial/maintenance margin; P5
  vẫn chưa cam kết multi-process HA, backup/restore hoặc migration framework tổng quát.
- Duplicate/out-of-order exchange event không được tạo duplicate order hoặc fill.

## Application Runtime & Control Contract — IMPLEMENTED P6

- Application entry point/composition root là nơi duy nhất ghép config, ledger, adapters, strategy,
  risk, reconciliation, control API và observability; import package không được tự tạo network hoặc
  trading side effect.
- Orchestrator sở hữu async task và resource lifecycle. Startup luôn validate, recover/reconcile rồi
  dừng ở `PAUSED`; restart không được tự chuyển sang `RUNNING`.
- Strategy runtime chỉ nhận typed market/user events và tạo order intent từ Neutral Arithmetic Grid
  hoặc confirmed fill. Nó không được gọi private adapter hay ghi ledger trực tiếp.
- Mọi intent tăng exposure phải đi qua P5 risk-managed executor và P4 persist-before-network boundary.
  Pause, stale data, reconciliation mismatch, breaker hoặc dependency failure phải fail closed.
- State transition và control command phải idempotent, được audit và tuân thủ state machine. Control
  API không được bật `order_submission_enabled` hoặc `live_trading_enabled` trong runtime.
- Task supervision, bounded queue/backpressure và ordered shutdown phải ngăn background failure âm
  thầm, intent mới sau stop/pause và resource leak.
- Health/readiness, structured logs, metrics và alerts phải phản ánh lifecycle, stream, reconciliation,
  order và risk state nhưng không làm lộ credential, signature hoặc raw account payload.
- Pause và normal stop hủy grid order nhưng giữ position; chỉ emergency stop mới gọi P5
  persist-before-mutation cancel-and-flatten. Shutdown ngừng intent, áp dụng policy, drain/cancel task
  rồi composition root mới đóng stream/HTTP/database.
- Control API dùng bearer token so sánh constant-time; command ID được claim trong SQLite trước khi
  đổi state và terminal result không thể bị ghi đè. API không có endpoint đổi cấu hình trading.
- SQLite P6 bổ sung `control_commands` và `runtime_events`; số tài chính tiếp tục lưu dạng decimal text,
  còn audit runtime chỉ lưu mã đã sanitize, không lưu raw exception/account payload.

## Contract Changes

Thay đổi contract phải cập nhật test, `PLAN.md`, `RISKS.md` và ghi rõ compatibility/trading impact
trong commit hoặc pull request.
