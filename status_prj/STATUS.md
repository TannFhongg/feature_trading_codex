# Project Status

## Snapshot

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Trạng thái tổng thể | `P2_COMPLETE` |
| Phase hiện tại | Phase 2 — Simulator & Backtest Core (`COMPLETE`) |
| Phase tiếp theo | Phase 3 — Binance Public Adapter (`PLANNED`, chưa bắt đầu) |
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
- Nâng tổng số test lên 48 cases, gồm kịch bản partial fill, latency, queue, funding long/short,
  fee allocation và drawdown.

## Chưa triển khai

- Chưa có Binance adapter, live execution engine, database, API hoặc monitoring.
- Chưa có market-data loader, order-book replay/queue calibration, margin/liquidation model hoặc
  slippage model ngoài giả định fill ở resting limit price.
- Chưa có integration test, CI/CD, deployment hoặc dependency lock/security scan.
- Chưa hỗ trợ Geometric, Long hoặc Short Grid.
- Chưa có API credential trong repository và chưa kích hoạt live trading.

## Bằng chứng xác minh

- Báo cáo bàn giao chi tiết: [`P1_REPORT.md`](P1_REPORT.md).
- Báo cáo P2: [`P2_REPORT.md`](P2_REPORT.md).
- Domain scaffold: commit `3fe45a0`.
- Arithmetic Grid: commit `534ff4d`.
- Simulator core: commit `45a9507`.
- Backtest accounting/reporting: commit `5d55b36`.
- `pytest`: 48 passed.
- `ruff check .`: passed; `ruff format --check .`: passed.
- `mypy src`: passed ở strict mode.
- Không phát hiện secret được gán giá trị trong phạm vi P2.

## Next Gate

P2 đã đạt exit criteria. Công việc dừng tại phase boundary; P3 chỉ bắt đầu sau yêu cầu mới của chủ
dự án. Trước khi dùng kết quả để ra quyết định giao dịch, P3+ phải bổ sung market-data ingestion và
Testnet calibration; P2 không phải bằng chứng về hiệu quả trên thị trường thật.
