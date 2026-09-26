# Implementation Plan

## P1 — Domain Foundation: COMPLETE

Đã bàn giao:

1. `pyproject.toml`, package `src/trading_bot/` và local editable environment.
2. Cấu hình pytest, Ruff, mypy và `.env.example` an toàn.
3. `GridConfig`, `SymbolRules`, `GridLevel`, `GridPlan` và lifecycle enums.
4. Arithmetic Grid calculator chỉ dùng `Decimal`.
5. Tick/step quantization, minQty/minNotional và symbol validation.
6. Neutral BUY/SELL assignment với đúng một inactive anchor level.
7. 27 unit tests; toàn bộ quality gates đạt.

## Phase Boundary

Không có phase triển khai nào đang được phép tiếp tục trong phạm vi hiện tại. P2 — Simulator & Backtest Core vẫn là `PLANNED/NOT_STARTED`; cần yêu cầu mới trước khi thay đổi mã nguồn cho P2.

## P2 Preview — Chưa triển khai

Khi được phê duyệt, P2 dự kiến mô phỏng order intents, partial fills, maker/taker fees, funding và báo cáo PnL/drawdown. Preview này chỉ phục vụ lập kế hoạch và không phải bằng chứng P2 đã bắt đầu.
