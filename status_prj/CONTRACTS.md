# Project Contracts

Trạng thái tài liệu: `P3_IMPLEMENTED`. Domain, Strategy, Simulator/Backtest và Binance Public Adapter
Contract đã có unit/integration tests; private execution, persistence và các runtime Safety Invariants
vẫn là `DRAFT`.

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

## Simulator & Backtest Contract

- Input là chuỗi `MarketTrade`/`FundingEvent` theo thứ tự thời gian; event out-of-order bị reject.
- `MarketTrade.aggressor_side` chỉ khớp resting order phía đối diện; volume được phân bổ theo price
  priority và không được dùng lặp lại cho nhiều level.
- Fill xảy ra ở resting limit price. `order_latency_ms` và `queue_ahead_multiplier` là giả định mô
  phỏng rõ ràng; queue mặc định bằng 1× order quantity để tránh fill lạc quan.
- Replacement intent không được fill trên market event đã tạo nó và chỉ dùng quantity thực tế fill.
- Một logical order key `(level, side)` có thể chứa nhiều intent slice nhưng chỉ xuất hiện một lần
  trong active-order view.
- `MAKER`/`TAKER` chọn fee rate tương ứng; fee không được hardcode trong engine mà đi qua config.
- Funding cash flow dùng signed one-way position: rate dương làm long trả và short nhận funding.
- Completed grid trade liên kết opening/closing fill; partial exit phân bổ opening fee theo quantity.
- Total PnL tách realized, unrealized, fee và funding; grid profit được báo riêng với total PnL.
- P2 không nhận OHLC candle làm bằng chứng fill và không tuyên bố mô phỏng liquidation/order book.

## Binance Public Adapter Contract — IMPLEMENTED P3

- Mọi network boundary là async; endpoint mặc định là USDⓈ-M Futures Testnet và không cần credential.
- Public REST chỉ gọi `GET /fapi/v1/time` và `GET /fapi/v1/exchangeInfo` trong phạm vi P3.
- Time sync dùng midpoint local trước/sau request và chọn mẫu có round-trip time thấp nhất.
- `exchangeInfo` chỉ được chuyển thành `SymbolRules` khi symbol ở trạng thái `TRADING`, contract là
  `PERPETUAL`, quote/margin asset là USDT và có đủ `PRICE_FILTER`, `LOT_SIZE`, `MIN_NOTIONAL`.
- Không dùng `pricePrecision`/`quantityPrecision` thay cho `tickSize`/`stepSize`.
- Giá, quantity, funding rate và exchange filters từ JSON phải là chuỗi decimal và được parse thành
  `Decimal`; binary float bị reject.
- Aggregate trade xác định aggressor side từ cờ buyer-is-maker; mark price/funding và best bid/ask là
  immutable typed events.
- Aggregate trade/mark price dùng route `/market`; book ticker dùng `/public`; không trộn hai nhóm trên
  cùng connection.
- Client tự xử lý ping/pong, giới hạn inbound queue, phát hiện stale, reconnect với exponential backoff
  và cung cấp health snapshot cho risk runtime tương lai.
- Event time không được đi lùi trong cùng stream type; payload sai schema/symbol bị fail closed.
- Public GET chỉ retry lỗi transport, `408`, `429` và `5xx` trong số lần hữu hạn; tôn trọng
  `Retry-After` và không retry `418`.

## Private Execution Adapter Contract — DRAFT

- Interface async cho submit, cancel, query, account, commission và User Data Stream.
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
