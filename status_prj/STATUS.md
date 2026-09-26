# Project Status

## Snapshot

| Thuộc tính | Giá trị |
|---|---|
| Cập nhật | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Trạng thái tổng thể | `P1_COMPLETE` |
| Phase hiện tại | Phase 1 — Domain Foundation (`COMPLETE`) |
| Phase tiếp theo | Phase 2 — Simulator & Backtest Core (`PLANNED`, chưa bắt đầu) |
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

## Chưa triển khai

- Chưa có simulator/backtest, Binance adapter, execution engine, database, API hoặc monitoring.
- Chưa có integration test, CI/CD, deployment hoặc dependency lock/security scan.
- Chưa hỗ trợ Geometric, Long hoặc Short Grid.
- Chưa có API credential trong repository và chưa kích hoạt live trading.

## Bằng chứng xác minh

- Domain scaffold: commit `3fe45a0`.
- Arithmetic Grid: commit `534ff4d`.
- `pytest`: 27 passed.
- `ruff check .`: passed; `ruff format --check .`: passed.
- `mypy src`: passed ở strict mode.
- Không phát hiện secret được gán giá trị trong phạm vi P1.

## Next Gate

P1 đã đạt exit criteria. Công việc dừng tại phase boundary theo yêu cầu; P2 không được bắt đầu nếu chưa có yêu cầu mới của chủ dự án.
