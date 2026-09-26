# Project Status

## Snapshot

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Trạng thái tổng thể | `P3_COMPLETE` |
| Phase hiện tại | Phase 3 — Binance Public Adapter (`COMPLETE`) |
| Phase tiếp theo | Phase 4 — Execution & Persistence (`PLANNED`, chưa bắt đầu) |
| Release | Chưa publish/tag; package version `0.1.0` |
| Runtime environment | Local development only; không có trading runtime |

## Đã hoàn thành

- Khởi tạo Git repository trên nhánh `main` và cấu hình `origin`.
- Thêm `.gitignore`, contributor guide và kế hoạch kỹ thuật.
- Chốt phạm vi MVP sơ bộ: USDⓈ-M, Neutral Arithmetic Grid, Isolated Margin, One-way Mode và Testnet-first.
- Tạo bộ báo cáo quản trị trong `status_prj/`.
- Tạo Python scaffold, editable package và cấu hình pytest, Ruff, mypy.
- Cài đặt immutable domain models dùng `Decimal` và typed lifecycle enums.
- Cài đặt Neutral Arithmetic Grid calculator với tick/step quantization, anchor và exchange-filter validation.
- Viết 27 unit tests cho domain invariants, rounding, sides, minQty và minNotional.
- Thêm deterministic grid execution simulator với latency, queue-ahead, price priority và
  market-volume allocation.
- Mô phỏng partial fill và tạo replacement intent đúng theo quantity thực tế đã fill; giữ một
  active logical order cho mỗi level/side.
- Thêm one-way position accounting, maker/taker fee, funding cash flow và ghép entry/exit fill để
  tính gross/net grid profit.
- Thêm backtest report cho realized/unrealized/net PnL, drawdown, inventory, notional, fill ratio,
  funding/PnL ratio và profit per completed grid.
- Nâng bộ P2 lên 48 unit tests, gồm partial fill, latency, queue, funding long/short, fee allocation và
  drawdown.
- Thêm async Binance public REST adapter, mặc định dùng USDⓈ-M Futures Testnet và không dùng credential.
- Parse `exchangeInfo` thành `SymbolRules` từ đúng filters; reject symbol không active USDT perpetual.
- Đồng bộ server time theo midpoint/lowest RTT và retry public GET có giới hạn, `Retry-After`/`418`
  aware.
- Thêm typed aggregate trade, mark price/funding và best bid/ask events dùng `Decimal`.
- Thêm routed WebSocket `/market` và `/public`, ping/pong, bounded queue, stale detection, reconnect
  backoff, health snapshot và per-stream event ordering.
- Nâng tổng số unit test lên 74 cases và thêm 3 public Testnet integration smoke tests.

## Chưa triển khai

- Chưa có private Binance adapter, User Data Stream, live execution engine, database, API hoặc
  monitoring.
- Chưa có historical market-data loader, order-book replay/queue calibration, margin/liquidation model
  hoặc slippage model ngoài giả định fill ở resting limit price.
- Chưa có CI/CD, deployment, dependency lock hoặc vulnerability scan.
- Chưa hỗ trợ Geometric, Long hoặc Short Grid.
- Chưa có API credential trong repository và chưa kích hoạt live trading.

## Bằng chứng xác minh

- Báo cáo bàn giao chi tiết: [`P1_REPORT.md`](P1_REPORT.md).
- Báo cáo P2: [`P2_REPORT.md`](P2_REPORT.md).
- Báo cáo P3: [`P3_REPORT.md`](P3_REPORT.md).
- Domain scaffold: commit `3fe45a0`.
- Arithmetic Grid: commit `534ff4d`.
- Simulator core: commit `45a9507`.
- Backtest accounting/reporting: commit `5d55b36`.
- Public REST adapter: commit `af2e314`.
- Public market streams: commit `942dcda`.
- Unit tests: 74 passed.
- Public Testnet integration: 3 passed trong 13.79 giây, không dùng credential.
- Full `pytest` mặc định: 74 passed, 3 integration tests skipped.
- Full `pytest` với Testnet enabled: 77 passed trong 14.67 giây.
- `ruff check .`: passed; `ruff format --check .`: passed.
- `mypy src`: passed ở strict mode.
- Không phát hiện secret được gán giá trị trong phạm vi P3; adapter public không nhận credential.

## Next Gate

P3 đã đạt exit criteria cho exchange rules, time sync và public market WebSocket trên Testnet. Công
việc dừng tại phase boundary; P4 chỉ bắt đầu sau yêu cầu mới của chủ dự án. Chưa có order execution,
persistence, reconciliation hoặc risk runtime; P3 không cho phép giao dịch Testnet/live và không phải
bằng chứng về hiệu quả trên thị trường thật.
