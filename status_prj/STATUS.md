# Project Status

## Snapshot

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Trạng thái tổng thể | `P5_IN_PROGRESS` |
| Phase hiện tại | Phase 5 — Risk & Recovery (`IN_PROGRESS`) |
| Phase tiếp theo | Phase 6 — Control API & Observability (`PLANNED`, chưa bắt đầu) |
| Release | Chưa publish/tag; package version `0.1.0` |
| Runtime environment | Local development; order submission và live trading tắt mặc định |

## Đã hoàn thành

- P1: immutable domain models, Neutral Arithmetic Grid và exchange-filter quantization dùng
  `Decimal`.
- P2: deterministic simulator/backtester với partial fill, latency/queue, fee, funding, position và
  PnL reporting.
- P3: Testnet-first Binance public REST/WebSocket, exchange rules, clock sync, market events, stale
  detection và reconnect.
- P4: typed execution model với deterministic `client_order_id` tối đa 36 ký tự.
- P4: async signed private REST cho order lifecycle, account, position, commission, income và
  listen-key lifecycle; strict parser reject binary float cho dữ liệu tài chính.
- P4: query-before-retry khi submit/cancel có kết quả mơ hồ; không blind retry và giữ `UNKNOWN` khi
  không thể xác nhận trạng thái.
- P4: User Data Stream với keepalive, ping/pong, bounded queue, reconnect, health snapshot và
  out-of-order rejection.
- P4: async SQLite ledger lưu order intent trước network, deduplicate exchange event/fill, bảo toàn
  trạng thái `FILLED` trước late cancel và giữ cumulative quantity đơn điệu.
- P4: reconciliation read-only cho open orders, missing orders, trades, positions, account và funding;
  lưu audit report và chỉ cho `safe_to_resume` khi không còn mismatch.
- P5: deterministic pre-trade risk engine với position/notional/open-order caps, loss/drawdown/funding,
  margin/liquidation, stale/reconciliation gates và soft/hard price boundaries.
- P5: `reduceOnly` exit chỉ được duyệt khi thực sự giảm vị thế one-way mà không cross qua zero.
- P5: latched circuit breaker, risk-audited executor và restart coordinator chặn auto-resume cho tới
  khi reconciliation cùng runtime snapshot đều an toàn.
- P5: persist-before-mutation emergency cancel-all và deterministic MARKET `reduceOnly`, với
  query-before-retry cho kết quả mơ hồ và durable `UNKNOWN` outcome.
- P5: SQLite lưu risk/emergency audit và forward-migrate P4 position/account tables để giữ mark,
  liquidation, notional và margin fields.
- Cấu hình private mặc định tới USDⓈ-M Futures Testnet; order submission bị khóa mặc định. Mainnet
  submission cần bật riêng cả `order_submission_enabled` và `live_trading_enabled`.

## Chưa triển khai

- P5 đã triển khai code chính; còn completion report, review regression cuối và supported-Python gate.
- Chưa có application entry point, strategy runtime loop, control API, metrics/alerts, CI/CD hoặc
  deployment tooling.
- SQLite ledger hiện dành cho single-process/local runtime; chưa có PostgreSQL, migration framework,
  backup/restore hay high availability.
- Chưa chạy authenticated private Testnet vì không có credential được cấp. Không có lệnh thật hoặc
  Testnet order nào được gửi trong P4.
- Chưa có dependency lock, vulnerability scan, historical data loader, liquidation model hoặc
  calibrated order-book replay.
- Chưa hỗ trợ Geometric, Long hoặc Short Grid; live trading vẫn bị khóa.

## Bằng chứng xác minh

- Báo cáo: [`P1_REPORT.md`](P1_REPORT.md), [`P2_REPORT.md`](P2_REPORT.md),
  [`P3_REPORT.md`](P3_REPORT.md), [`P4_REPORT.md`](P4_REPORT.md).
- Unit/default suite hiện tại: 141 passed, 3 public integration tests skipped theo thiết kế trên Python
  3.14; Python 3.12 sẽ được chạy lại ở final P5 gate.
- Public Testnet smoke: 3 passed trong 13.53 giây trên Python 3.12, không dùng credential.
- P4 focused suite: 39 passed, gồm private REST, User Data Stream, ledger và reconciliation.
- P5 risk-engine focused suite: 18 passed.
- P5 risk/recovery focused suite: 25 passed, gồm limits, breaker, restart, audit và emergency exit.
- `ruff check .`: passed; `ruff format --check .`: 56 files formatted.
- `mypy src`: passed ở strict mode trên 30 source files.
- `python -m pip check`: passed; no broken requirements.
- P4 private boundaries được kiểm tra bằng deterministic fakes; không dùng production account.

## Next Gate

Hoàn thành completion report, review regression và full quality gate trên Python 3.12/3.14 để quyết
định `P5_COMPLETE`. Trạng thái `P5_IN_PROGRESS` không cho phép authenticated Testnet soak hoặc live
trading.
