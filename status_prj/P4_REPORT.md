# P4 Execution & Persistence — Completion Report

## Report Metadata

| Thuộc tính | Giá trị |
|---|---|
| Phase | P4 — Execution & Persistence |
| Trạng thái | `COMPLETE` |
| Ngày xác minh | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Exchange scope | Binance USDⓈ-M Futures, Testnet-first |
| Private network evidence | Deterministic fakes; không dùng credential/account thật |
| Order safety | Submission và live trading tắt mặc định |
| Phase tiếp theo | P5 `PLANNED/NOT_STARTED` |

## 1. Kết quả P4

P4 bổ sung boundary riêng tư, order lifecycle, persistence và reconciliation mà không thay đổi strategy
thành một trading runtime. Execution records là immutable, financial values dùng `Decimal`, intent được
lưu trước network call và mọi luồng retry có trạng thái mơ hồ đều query exchange trước. SQLite ledger
giữ audit trail có business-key deduplication; reconciliation chỉ đọc exchange và không submit/cancel.

## 2. Execution Domain

- `OrderRequest` sinh deterministic client order ID theo strategy/level/side/cycle, tối đa 36 ký tự.
- Strategy ID dài hoặc cần normalize được gắn digest để tránh collision.
- Typed records bao phủ exchange order/fill, account/balance/position, commission, income,
  order/account stream event và reconciliation report.
- Local-only states gồm `PENDING_SUBMIT`, `UNKNOWN`, `REJECTED`; exchange statuses được phân biệt và
  validate rõ ràng.

## 3. Private REST Adapter

- Signed async calls cho create/query/cancel order, open orders, user trades, position risk, account,
  commission rates, income history và listen-key lifecycle.
- Signing dùng HMAC-SHA256, time offset từ P3, bounded `recvWindow`, API-key header và encoded query.
- Submit tạo GTC limit order, one-way `BOTH` position side, reuse cùng deterministic client ID.
- Timeout/503 khi submit hoặc cancel kích hoạt query-before-retry. Chỉ retry khi query xác nhận chưa có
  order hoặc order vẫn active; query failure trả ambiguous error thay vì gửi lại mù.
- Config mặc định Testnet/read-only. Mainnet submission cần hai opt-in độc lập.
- Strict parsers reject float tài chính và normalize REST payload về execution records.

## 4. User Data Stream

- Listen key được tạo/keepalive/đóng qua REST API-key calls.
- WebSocket dùng private route, automatic ping/pong, bounded queue và reconnect backoff.
- `listenKeyExpired` đóng vòng connection hiện tại và lấy key mới.
- Event ordering được theo dõi theo event type trong từng connection; event đi lùi bị fail closed.
- Health snapshot không chứa listen key hoặc credential.

## 5. Persistence Ledger

- SQLite schema lưu orders, fills, exchange events, positions, balances, account snapshots, income và
  reconciliation runs.
- Async public API dùng `asyncio.to_thread` và lock; file-backed mode bật WAL.
- Intent được lưu idempotently trước submit. Partial unique index giữ một active logical order cho mỗi
  strategy/level/side.
- Exchange event có deterministic event ID; fills unique theo `(symbol, trade_id)`.
- Một transaction atomically deduplicate event, cập nhật order và insert fill.
- Late cancel không làm lùi `FILLED`; cumulative executed quantity không giảm.
- Local pending timestamp được thay bằng exchange timestamp đầu tiên để event hợp lệ tiếp theo không bị
  coi là stale.
- File ledger đã được kiểm tra close/reopen và giữ nguyên order state.

## 6. Reconciliation

Một reconciliation run:

1. Lấy local active orders và remote open orders.
2. Nhận diện remote orphan, quantity mismatch và query local order không xuất hiện trong open list.
3. Nạp account trades idempotently để bổ sung fill bị mất.
4. So sánh local/remote position quantity và entry price, rồi lưu remote snapshot.
5. Lưu account snapshot và funding income.
6. Ghi audit report với số orphan/unresolved/mismatch/inserted records.

`safe_to_resume` chỉ true khi không còn remote orphan, unresolved local active order, quantity mismatch
hoặc position mismatch. Remote orphan đã được quan sát vẫn giữ trạng thái orphan ở các lần chạy sau
cho đến khi exchange xác nhận nó không còn active; việc ghi audit local không biến nó thành order thuộc
strategy. Reconciler không có phương thức submit/cancel và không tự xử lý orphan bằng hành động phá hủy.

## 7. Test & Quality Evidence

| Kiểm tra | Kết quả |
|---|---|
| Full pytest mặc định | 108 passed, 3 public integration tests skipped |
| P4 focused suite | 34 passed |
| Public Testnet integration | 3 passed trong 13.88 giây |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 56 files formatted |
| `mypy src` | Passed ở strict mode; 30 source files |
| `python -m pip check` | Passed; no broken requirements |

Regression coverage gồm deterministic-ID collision, disabled-by-default submission, mainnet opt-in,
signing, timeout/503 recovery, cancel/fill race, duplicate events/fills, User Data reconnect/order,
ledger restart, orphan/unresolved orders, fill/funding insertion và position mismatch.

Public Testnet smoke chỉ kiểm tra P3 read-only endpoints/streams và không dùng credential. Authenticated
private Testnet không chạy vì không có API key được cấp; private boundary dùng deterministic fake theo
repository policy.

## 8. File Inventory

| File/Package | Vai trò |
|---|---|
| `src/trading_bot/execution/models.py` | Execution/account/event/reconciliation records |
| `src/trading_bot/execution/service.py` | Persist-before-submit executor và stream event applier |
| `src/trading_bot/execution/reconciliation.py` | Read-only exchange/ledger reconciliation |
| `src/trading_bot/binance/private.py` | Signed private REST adapter |
| `src/trading_bot/binance/private_parsing.py` | Strict private REST/User Data parsing |
| `src/trading_bot/binance/user_stream.py` | Resilient User Data Stream |
| `src/trading_bot/persistence/ledger.py` | Async idempotent SQLite ledger |
| `tests/unit/test_binance_private_rest.py` | Signing, lifecycle, retry và read-contract tests |
| `tests/unit/test_binance_user_stream.py` | User Data parsing/reconnect/order tests |
| `tests/unit/test_execution_ledger.py` | Idempotence, race, persistence và executor tests |
| `tests/unit/test_execution_reconciliation.py` | Orphan/fill/income/position reconciliation tests |

## 9. Security and Trading Impact

- Không thêm secret, account data hoặc production configuration.
- Credential dataclasses có redacted representation; errors không chứa signed query/secret.
- Order submission vẫn tắt mặc định; không có application entry point để tự động giao dịch.
- P4 chưa có risk approval. Không được dùng executor như live runtime trước khi P5–P7 đạt gate.

## 10. Phase Boundary

P4 đạt exit criteria về order lifecycle, idempotent event ledger và reconciliation. P5 chưa bắt đầu và
phải bổ sung exposure limits, circuit breakers, restart coordinator, emergency reduce-only exit và
recovery tests. Việc hoàn thành P4 không cho phép authenticated soak hoặc live trading.
