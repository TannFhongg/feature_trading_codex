# Implementation Plan

## Milestone tiếp theo: P1 — Domain Foundation

### Phạm vi

1. Tạo `pyproject.toml` và package `src/trading_bot/`.
2. Cấu hình pytest, Ruff và mypy.
3. Tạo `.env.example` chỉ chứa placeholder an toàn.
4. Định nghĩa `GridConfig`, `SymbolRules`, `GridLevel`, `GridType`, `GridDirection` và `StrategyState`.
5. Xây Arithmetic Grid calculator sử dụng `Decimal`.
6. Validate range, grid count, `tickSize`, `stepSize`, `minQty` và `minNotional`.
7. Gán BUY/SELL quanh reference price mà không đặt level có nguy cơ khớp ngay.
8. Viết unit test cho rounding, validation, invariants và edge cases.

### Ngoài phạm vi P1

- Không gọi Binance API.
- Không lưu database.
- Không gửi lệnh thật hoặc Testnet order.
- Chưa triển khai Geometric, Long hoặc Short Grid.
- Chưa có web UI.

## Definition of Done

- `pytest` thành công.
- `ruff check .` và `ruff format --check .` thành công.
- `mypy src` thành công.
- Không dùng `float` trong domain tiền tệ.
- Grid có đúng số level, giá duy nhất, đã quantize và nằm trong range.
- Code strategy không import module Binance, HTTP, WebSocket hoặc persistence.
- `STATUS.md`, `PHASES.md`, `CONTRACTS.md` và `SCHEDULE.md` được cập nhật cùng commit hoàn tất phase.
