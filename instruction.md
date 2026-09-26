# Kế hoạch xây dựng Binance Futures Grid Bot

Tài liệu này giả định mục tiêu là xây dựng một bot **Binance USDⓈ-M Futures Grid** tự vận hành qua API.

> Lưu ý: Đây là kế hoạch kỹ thuật và quản trị rủi ro, không phải khuyến nghị đầu tư. Futures có rủi ro mất vốn và thanh lý vị thế.

## 1. Phạm vi MVP đề xuất

Phiên bản đầu nên được giới hạn để giảm độ phức tạp và rủi ro:

- USDⓈ-M perpetual, ký quỹ USDT.
- Một tài khoản, một symbol và một chiến lược tại một thời điểm.
- Isolated Margin, One-way Mode.
- Neutral Grid, không mở vị thế ban đầu.
- Arithmetic Grid trước; Geometric Grid ở phiên bản tiếp theo.
- Đặt lệnh `LIMIT`, ưu tiên post-only/maker.
- Có stop-loss, giới hạn vị thế, giới hạn lỗ và nút dừng khẩn cấp.
- REST dùng để gửi, hủy và đối soát lệnh; WebSocket là nguồn trạng thái thời gian thực.
- Chạy Binance Futures Testnet trước khi kết nối tài khoản thật.

Binance định nghĩa Futures Grid bằng hướng Long/Short/Neutral, khoảng giá, số grid và Arithmetic/Geometric. Chiến lược phù hợp hơn với thị trường dao động trong vùng, nhưng vẫn có thể thua lớn khi giá breakout hoặc tiến gần thanh lý.

Tham khảo: [Binance Futures Grid](https://www.binance.com/en/support/faq/detail/f4c453bab89648beb722aa26634120c3)

### Chưa đưa vào MVP

- Cross Margin và Multi-Assets Mode.
- COIN-M.
- Nhiều tài khoản hoặc nhiều chiến lược chung một symbol.
- Auto-add margin.
- AI tự chọn vùng giá.
- Trailing grid.
- Giao diện web hoàn chỉnh.

## 2. Logic chiến lược

Với biên dưới `L`, biên trên `U`, số khoảng grid `N`:

- Arithmetic: `price[i] = L + i × (U-L)/N`
- Geometric: `price[i] = L × (U/L)^(i/N)`

Neutral Grid khởi tạo:

- Đặt BUY ở các mức dưới giá hiện tại.
- Đặt SELL ở các mức trên giá hiện tại.
- Không đặt tại mức gần giá hiện tại nhất nếu nó có nguy cơ khớp ngay.
- Khi BUY tại mức `i` khớp, đặt SELL tương ứng tại `i+1`.
- Khi SELL tại mức `i` khớp, đặt BUY tương ứng tại `i-1`.
- Partial fill chỉ tạo lệnh đối ứng cho phần khối lượng thực tế đã khớp.

Tất cả phép tính giá và số lượng phải dùng decimal, không dùng floating point. Giá và quantity phải được làm tròn theo `tickSize`, `stepSize`, `minQty` và `minNotional` lấy từ `exchangeInfo`; không dùng `pricePrecision` thay cho `tickSize`.

Tham khảo: [USDⓈ-M Exchange Information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)

Trước khi chấp nhận cấu hình, bot phải kiểm tra:

```text
lợi nhuận kỳ vọng mỗi vòng
> phí mở + phí đóng + slippage dự phòng + funding dự phòng + safety margin
```

Không hardcode maker/taker fee; lấy commission của chính tài khoản. Funding fee phải được đưa riêng vào PnL vì đây không phải lợi nhuận grid.

## 3. Kiến trúc hệ thống

```text
Market WebSocket ──> Market State ─┐
                                   ├─> Grid Engine ─> Risk Engine ─> Execution Adapter ─> Binance
User Data WebSocket ─> Event Inbox ┘                         │
          │                                                  │
          └────────────> Order/Fill Ledger <─────────────────┘
                               │
                     PostgreSQL + Monitoring
```

### 3.1. `binance_adapter`

- Ký request và đồng bộ server time.
- Gửi, hủy và truy vấn lệnh.
- Đọc account, position, commission và trading rules.
- Retry có phân loại lỗi và tuân thủ rate-limit.

### 3.2. `market_stream`

- Nhận mark price, best bid/ask và dữ liệu cần thiết.
- Reconnect, heartbeat và phát hiện dữ liệu cũ.
- WebSocket USDⓈ-M sử dụng các tuyến `/public`, `/market`, `/private`.
- Chủ động reconnect trước hoặc sau khi kết nối hết vòng đời.
- Dùng User Data Stream làm nguồn trạng thái lệnh và vị thế thời gian thực.

Tham khảo: [WebSocket Market Streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect)

### 3.3. `grid_engine`

- Sinh và validate grid.
- Quản lý một logical order tại mỗi level.
- Xử lý full fill, partial fill và cancel/fill race.
- Tạo lệnh đối ứng sau khi xác nhận fill.

### 3.4. `risk_engine`

- Kiểm tra trước mọi lệnh.
- Theo dõi notional, margin, PnL, funding và khoảng cách tới liquidation.
- Ngăn lệnh mới khi feed lỗi hoặc trạng thái chưa đối soát.
- Lệnh thoát khẩn cấp luôn dùng `reduceOnly`.

### 3.5. `reconciler`

- Chạy khi khởi động, reconnect và định kỳ.
- So sánh database với open orders, fills và position trên Binance.
- Nhận diện orphan order, missing fill và trạng thái không xác định.

### 3.6. `ledger`

- Lưu order, fill, commission, funding, realized và unrealized PnL.
- Phân biệt grid profit với tổng PnL.
- Mọi event phải được xử lý idempotent.

### 3.7. `control_api`

- Create, start, pause, resume và stop bot.
- Dry-run cấu hình trước khi chạy.
- Emergency cancel-and-flatten.
- Audit log mọi thay đổi cấu hình.

### Stack gợi ý

- Python async.
- FastAPI.
- PostgreSQL.
- SQLAlchemy và Alembic.
- Docker Compose.
- Redis chỉ thêm khi thật sự cần coordination hoặc queue.

## 4. State machine

```text
DRAFT → VALIDATING → STARTING → RUNNING
                              ↘ RECOVERING
RUNNING → PAUSED → RUNNING
RUNNING → STOPPING → STOPPED
Bất kỳ trạng thái nào → ERROR / EMERGENCY_STOP
```

Quy tắc:

- Chỉ `RUNNING` và trạng thái đã reconciled mới được tăng exposure.
- `PAUSED`: không tạo order mới; chính sách giữ hoặc hủy lệnh hiện tại phải được định nghĩa rõ.
- `STOPPING`: hủy grid order, đợi xác nhận rồi giữ hoặc đóng vị thế theo lựa chọn.
- `EMERGENCY_STOP`: hủy lệnh và đóng vị thế bằng reduce-only.
- Restart không được tự giả định trạng thái từ database.

Mỗi lệnh dùng `newClientOrderId` xác định trước, ví dụ:

```text
grid-{strategyId}-{level}-{side}-{cycle}
```

Điều này giúp ngăn tạo lệnh trùng sau timeout hoặc restart.

Một số phản hồi HTTP `503` của Binance có trạng thái thực thi **UNKNOWN**: lệnh có thể đã thành công dù client chưa nhận được kết quả. Bot phải kiểm tra WebSocket hoặc truy vấn order trước khi retry.

Tham khảo: [USDⓈ-M API error handling](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info)

## 5. Bộ kiểm soát rủi ro bắt buộc

- API key chỉ bật Futures, tắt withdrawal và whitelist IP.
- Live trading mặc định bị khóa; cần bật bằng cấu hình riêng.
- Leverage mặc định 1x; tăng leverage phải là thao tác rõ ràng.
- Giới hạn tổng notional.
- Giới hạn vị thế tuyệt đối.
- Giới hạn số open orders.
- Giới hạn lỗ theo ngày.
- Giới hạn drawdown của chiến lược.
- Giới hạn funding rate được chấp nhận.
- Thiết lập khoảng cách tối thiểu tới liquidation price.
- Stale-data breaker: không tạo exposure khi mất market stream hoặc user stream.
- Rate-limit breaker: backoff khi nhận `429`, không tiếp tục đến mức bị khóa IP `418`.

### Chính sách khi giá ra ngoài vùng

- Soft boundary: ngừng bổ sung grid.
- Hard boundary: hủy lệnh và reduce-only flatten.
- Không auto-add margin trong MVP.

### Phân loại lệnh

- Entry làm tăng exposure.
- Exit/reduce-only làm giảm exposure.
- Exit được ưu tiên khi hệ thống quá tải.

Account API cung cấp initial margin và maintenance margin để risk engine theo dõi sức khỏe tài khoản.

Tham khảo: [USDⓈ-M Account Information V3](https://developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api/Account-Information-V3)

## 6. Dữ liệu cần lưu

Các bảng tối thiểu:

- `strategies`
- `strategy_configs`
- `grid_levels`
- `orders`
- `fills`
- `positions`
- `balance_snapshots`
- `funding_payments`
- `pnl_snapshots`
- `exchange_events`
- `risk_events`
- `operator_actions`

Ràng buộc database:

- Unique `client_order_id`.
- Unique Binance trade/fill ID.
- Một active logical order trên mỗi strategy/level/side.
- Event inbox xử lý exactly-once về mặt nghiệp vụ, dù WebSocket có gửi lại.

## 7. Kiểm thử

### 7.1. Unit test

- Arithmetic và geometric grid.
- Rounding theo tick/step size.
- Profit-after-fee validator.
- Partial fills.
- Duplicate và out-of-order events.
- Max position/notional.
- State-machine transitions.

### 7.2. Backtest

Không chỉ dùng candle OHLC để kết luận khả năng sinh lời. Backtest cần tối thiểu:

- Agg trades hoặc dữ liệu chi tiết hơn để mô phỏng fill.
- Maker/taker fee thực tế.
- Funding.
- Latency.
- Partial fill.
- Giả định queue position bảo thủ.
- Breakout, gap và thời kỳ volatility cao.

Chỉ số đánh giá:

- Net PnL sau mọi chi phí.
- Max drawdown.
- Inventory exposure.
- Fill ratio.
- Profit per completed grid.
- Funding/PnL ratio.
- Khoảng cách liquidation nhỏ nhất.
- Số orphan/duplicate order.

### 7.3. Integration và chaos test

- Chạy trên Binance Futures Testnet với REST `demo-fapi` và WebSocket `demo-fstream`.
- Kill process giữa lúc gửi lệnh.
- Mất mạng khi lệnh vừa khớp.
- WebSocket reconnect.
- REST timeout/503.
- Database restart.
- Clock drift.
- Cancel và fill xảy ra đồng thời.

Tham khảo:

- [Testnet API information](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info)
- [USDⓈ-M Test Order](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order)

## 8. Lộ trình triển khai

| Giai đoạn | Thời lượng ước tính | Kết quả |
|---|---:|---|
| Đặc tả | 2–3 ngày | Chốt mode, công thức, stop policy và state machine |
| Nền tảng | 3–5 ngày | Project skeleton, DB schema, config và logging |
| Binance adapter | 4–6 ngày | REST signing, WebSocket, rate-limit và testnet |
| Grid engine | 5–7 ngày | Neutral arithmetic, partial fills và replacement |
| Risk và recovery | 5–7 ngày | Limits, kill switch, reconciliation và restart safety |
| Backtest/simulator | 4–6 ngày | Fee/funding/fill simulation và reports |
| Control API/monitoring | 3–5 ngày | API, metrics và alert Telegram/Slack |
| Testnet soak | 7–14 ngày lịch | Chạy liên tục và fault injection |
| Live canary | 3–7 ngày lịch | Vốn giới hạn cứng và theo dõi sát |

Tổng thời gian hợp lý cho một kỹ sư là khoảng **5–7 tuần phát triển**, sau đó thêm thời gian testnet và canary.

## 9. Điều kiện trước khi chạy tiền thật

- Testnet chạy liên tục ít nhất 7 ngày.
- Không có duplicate hoặc orphan order.
- Restart/reconnect khôi phục đúng toàn bộ trạng thái.
- Timeout `503` không sinh lệnh trùng.
- Kill switch đã được thử thực tế.
- PnL khớp với trade ledger và account statement.
- API key không có quyền rút tiền.
- Có cảnh báo khi bot chết, mất stream, chạm risk limit hoặc gần liquidation.
- Live canary sử dụng hard cap nhỏ và không cho phép tự tăng vốn.

## 10. Bước triển khai tiếp theo

1. Chốt đặc tả `GridConfig`.
2. Chốt state machine và chính sách stop/pause.
3. Thiết kế database schema.
4. Liệt kê Binance API endpoint cần dùng.
5. Dựng project skeleton và môi trường test.
6. Xây simulator trước khi cho phép gửi lệnh thật.
