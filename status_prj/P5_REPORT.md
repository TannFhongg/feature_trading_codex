# P5 Risk & Recovery — Completion Report

## Report Metadata

| Thuộc tính | Giá trị |
|---|---|
| Phase | P5 — Risk & Recovery |
| Trạng thái | `COMPLETE` |
| Ngày xác minh | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Exchange scope | Binance USDⓈ-M Futures, Testnet-first |
| Private network evidence | Deterministic fakes; không dùng credential/account thật |
| Trading posture | Order submission và live trading tắt mặc định |
| Phase tiếp theo | P6 `PLANNED/NOT_STARTED` |

## 1. Kết quả P5

P5 bổ sung lớp risk/recovery bắt buộc trước runtime execution mà không tạo application loop hoặc tự bật
giao dịch. Risk decision là deterministic và fail-closed; breaker latch các lỗi stale/reconciliation và
hard-risk breach; restart không auto-resume; emergency exit persist intent trước exchange mutation rồi
cancel-all và flatten bằng deterministic MARKET `reduceOnly`.

## 2. Pre-trade Risk Engine

- Immutable `RiskLimits`, `RuntimeRiskSnapshot`, decision và violation records dùng `Decimal`.
- Entry chỉ được phép khi strategy `RUNNING`, reconciliation sạch, market/user data fresh, breaker chưa
  latch, account ở One-way Mode, symbol dùng Isolated Margin và leverage không vượt policy cap.
- Kiểm tra position quantity, tổng position/open-order notional, open-order count, daily realized loss,
  drawdown, funding rate, maintenance-margin ratio và liquidation distance.
- Soft boundary chặn entry; hard boundary, daily loss, drawdown, margin và liquidation breach yêu cầu
  emergency stop.
- Normal order được xem là có khả năng tăng exposure. `reduceOnly` chỉ bypass entry gates nếu side và
  quantity giảm nghiêm ngặt vị thế hiện tại mà không cross qua zero.
- Risk-managed executor persist audit decision trước durable executor; rejection không gọi network.

## 3. Circuit Breaker & Restart Recovery

- Breaker latch stale market/user data, unsafe reconciliation và mọi emergency-severity violation.
- Reset chỉ thành công từ `PAUSED`/`RECOVERING` snapshot đã reconciled, fresh, đúng account mode và nằm
  trong toàn bộ configured limits.
- Restart coordinator bắt đầu `RECOVERING`, gọi P4 read-only reconciler và chỉ chuyển `PAUSED` khi
  report `safe_to_resume`.
- Resume là thao tác explicit riêng; reconciliation mismatch hoặc unhealthy snapshot giữ bot không ở
  trạng thái `RUNNING`.
- Decision, breaker trip/reset và recovery result được lưu thành idempotent risk audit events.

## 4. Emergency Cancel-and-Flatten

1. Latch breaker và persist unique emergency action trước network mutation.
2. Gọi signed `DELETE /fapi/v1/allOpenOrders`; timeout/503 được kiểm tra bằng open-order query trước retry.
3. Refetch position và chỉ chấp nhận đúng một one-way `BOTH` position.
4. Nếu còn position, submit `MARKET` phía đối nghịch cho toàn bộ quantity với `reduceOnly=true`.
5. Emergency client ID deterministic, tối đa 36 ký tự; ambiguous submit query cùng ID trước retry.
6. Duplicate action ID không gọi cancel/submit lần hai. `UNKNOWN` chỉ được resolve sau reconciliation
   với cùng client ID; `SUBMITTED` không tự được coi là flat hay safe to resume.

Order submission safety locks của P4 vẫn áp dụng cho emergency MARKET order. Cancel-all vẫn khả dụng để
giảm rủi ro khi normal submission bị khóa.

## 5. Persistence & Binance Risk Data

- SQLite bổ sung `risk_events` và `emergency_actions`.
- Forward schema migration nâng P4 position/account tables tại open-time và đã có regression test trên
  database schema cũ.
- Position V3 giữ mark price, liquidation price, notional, initial margin và maintenance margin.
- Account V3 giữ total initial/maintenance margin. Tất cả financial payload tiếp tục reject binary float.
- P5 không thay đổi giới hạn SQLite single-process, không cung cấp HA/backup/restore hay migration
  framework tổng quát.

## 6. Test & Quality Evidence

| Kiểm tra | Kết quả |
|---|---|
| Full pytest Python 3.12 | 144 passed, 3 public integration tests skipped |
| Full pytest Python 3.14 | 144 passed, 3 public integration tests skipped |
| Development mode + warning-as-error | Passed trên cả Python 3.12 và 3.14 |
| P5 focused risk/recovery | 28 passed |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 67 files formatted |
| `mypy src` | Passed ở strict mode; 38 source files |
| `python -m pip check` | Passed; no broken requirements |

Regression coverage gồm stale/reconciliation gate, One-way/Isolated/leverage gate, all financial caps,
long/short liquidation distance, valid/invalid reduce-only, breaker latch/reset, no auto-resume,
reconciliation mismatch, audit-before-submit, duplicate emergency action, empty/long/short position,
unknown emergency outcome, P4→P5 schema migration, cancel-all ambiguity và MARKET query-before-retry.

## 7. Security and Trading Impact

- Không thêm secret, production config hoặc account data.
- Authenticated private Testnet chưa chạy vì không có credential được cấp.
- Không có Testnet/private/live order nào được gửi trong P5; exchange-facing mutations dùng fakes.
- Public Testnet smoke của P3/P4 không cần chạy lại vì P5 không thay đổi public REST/WebSocket code.
- Live trading, authenticated soak, control API và strategy runtime vẫn chưa được phép.

## 8. Phase Boundary

P5 đạt exit criteria về limits, circuit breakers, restart recovery và emergency exit. P6 — Application
Runtime, Control API & Observability chưa bắt đầu. Hoàn thành P5 không thay thế P6 application/runtime
và observability, P7 authenticated Testnet soak, dependency/security deployment gates hoặc phê duyệt
riêng cho P8 live canary.
