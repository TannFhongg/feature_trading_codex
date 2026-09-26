# Project Status

## Snapshot

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Trạng thái tổng thể | `P4_COMPLETE` |
| Phase hiện tại | Phase 4 — Execution & Persistence (`COMPLETE`) |
| Phase tiếp theo | Phase 5 — Risk & Recovery (`PLANNED`, chưa bắt đầu) |
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
- Cấu hình private mặc định tới USDⓈ-M Futures Testnet; order submission bị khóa mặc định. Mainnet
  submission cần bật riêng cả `order_submission_enabled` và `live_trading_enabled`.

## Chưa triển khai

- Chưa có risk engine, exposure/notional/daily-loss limits, stale-feed circuit breaker, restart
  coordinator, kill switch hoặc reduce-only emergency exit; đây là P5.
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
- Unit/default suite: 108 passed, 3 public integration tests skipped theo thiết kế.
- Public Testnet smoke: 3 passed trong 13.88 giây, không dùng credential.
- P4 focused suite: 34 passed, gồm private REST, User Data Stream, ledger và reconciliation.
- `ruff check .`: passed; `ruff format --check .`: 56 files formatted.
- `mypy src`: passed ở strict mode trên 30 source files.
- `python -m pip check`: passed; no broken requirements.
- P4 private boundaries được kiểm tra bằng deterministic fakes; không dùng production account.

## Next Gate

P4 đã đạt exit criteria cho order lifecycle, idempotent persistence và reconciliation. Repository dừng
tại phase boundary. P5 chỉ bắt đầu theo yêu cầu mới và phải thêm risk/recovery controls trước khi có
runtime gửi lệnh. Trạng thái `P4_COMPLETE` không phải bằng chứng về hiệu quả thị trường, không cho phép
authenticated Testnet soak và tuyệt đối không cho phép live trading.
