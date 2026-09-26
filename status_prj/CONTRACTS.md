# Project Contracts

Trạng thái tài liệu: `P1_IMPLEMENTED`. Domain và Strategy Contract đã có unit tests; Exchange Adapter và các runtime Safety Invariants vẫn là `DRAFT`.

## Domain Contract

- Mọi giá, quantity, fee, funding và PnL dùng `Decimal`.
- `lower_price > 0`, `upper_price > lower_price`, `grid_count >= 2`.
- P1 chỉ enable `ARITHMETIC` và `NEUTRAL`; các enum tương lai bị reject khi tạo config.
- `SymbolRules` cung cấp `tick_size`, `step_size`, `min_qty` và `min_notional`.
- Mọi grid price phải duy nhất sau quantization và nằm trong `[lower_price, upper_price]`.
- `grid_count` là số interval; kết quả có `grid_count + 1` price levels.
- Config và symbol rules phải cùng symbol viết hoa.

## Strategy Contract

- BUY nằm dưới reference price; SELL nằm trên reference price.
- Không gửi order từ module strategy; module chỉ tạo intent.
- Neutral Grid có đúng một anchor không mang order side; tie chọn price thấp hơn để deterministic.
- Lower bound được ceil theo tick; upper bound, intermediate prices và quantity được floor theo exchange increment.
- Mọi active level phải đạt `min_notional`; quantity sau quantization phải đạt `min_qty`.
- Partial fill chỉ sinh replacement intent cho filled quantity hợp lệ.
- Một strategy/level/side chỉ có tối đa một active logical order.

## Exchange Adapter Contract — DRAFT

- Interface async cho time sync, exchange rules, market stream, submit, cancel và query.
- `client_order_id` là deterministic và unique theo strategy/level/side/cycle.
- Timeout hoặc HTTP 503 trạng thái unknown phải query/reconcile trước khi retry.
- User Data Stream là nguồn sự kiện thời gian thực; REST dùng để đối soát.

## Runtime Safety Invariants — DRAFT

- Không tăng exposure khi market/user data stale hoặc reconciliation chưa hoàn tất.
- Risk engine duyệt mọi execution intent.
- Emergency exit chỉ giảm exposure bằng `reduceOnly`.
- Restart không được giả định database đồng nhất với Binance.
- Duplicate và out-of-order event không được tạo duplicate order hoặc fill.

## Contract Changes

Thay đổi contract phải cập nhật test, `PLAN.md`, `RISKS.md` và ghi rõ compatibility impact trong commit/PR.
