# P2 Simulator & Backtest Core — Completion Report

## Report Metadata

| Thuộc tính | Giá trị |
|---|---|
| Phase | P2 — Simulator & Backtest Core |
| Trạng thái | `COMPLETE` |
| Ngày xác minh | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Runtime trading | Không có; không kết nối Binance hoặc tài khoản giao dịch |
| Phase tiếp theo | P3 `PLANNED/NOT_STARTED` |

## 1. Kết quả P2

P2 bổ sung một simulator thuần Python, deterministic và không có network dependency. Simulator nhận
`GridPlan` từ P1 cùng chuỗi aggregate market trades/funding events, tạo order intents, mô phỏng fills
và trả về báo cáo hiệu quả/rủi ro có thể audit. Mọi phép tính tài chính tiếp tục dùng `Decimal`.

P2 là công cụ kiểm chứng logic và giả định. Nó không gửi order, không đọc API key và không phải bằng
chứng chiến lược sẽ sinh lời trên Testnet hoặc thị trường thật.

## 2. Execution Simulation

- Khởi tạo một ENTRY intent cho mỗi active grid level.
- Dùng aggressor side để chọn resting BUY/SELL có thể khớp.
- Phân bổ aggregate-trade volume theo price priority: BUY cao trước, SELL thấp trước.
- Hỗ trợ partial fill và chỉ tạo replacement cho filled quantity.
- Replacement BUY/SELL ở adjacent level theo Grid Contract.
- Replacement không thể fill trên cùng event đã tạo nó.
- `order_latency_ms` trì hoãn thời điểm intent được phép fill.
- `queue_ahead_multiplier` mô phỏng volume đứng trước; mặc định bảo thủ là 1× order quantity.
- Nhiều partial-fill slices được gom thành một active logical order view theo `(level, side)`.
- Intent/fill ID deterministic trong từng run để trace và so sánh test.

## 3. Accounting & Reporting

- One-way signed position ledger hỗ trợ tăng/giảm long, short và flip position.
- Tính realized/unrealized PnL theo average entry price.
- Chọn maker/taker commission rate theo liquidity của từng fill.
- Positive funding rate làm long trả, short nhận; funding tách khỏi grid profit.
- Liên kết closing fill với opening fill để phân bổ entry fee cho partial exit.
- Báo gross/net grid profit và net profit per completed grid.
- Báo final balance/equity, net PnL, max drawdown/ratio, max inventory, max notional, fill ratio và
  funding/PnL ratio.
- Report giữ toàn bộ intents, fills, fill accounting, funding payments, completed-grid records và
  final open orders dưới dạng immutable records.

## 4. Giả định và Giới hạn

- Input phải được sắp theo `event_time_ms`; P2 reject out-of-order event.
- Fill price là resting limit price, không mô phỏng price improvement.
- Queue model là deterministic multiplier, chưa replay L2 order book hoặc calibrate queue position.
- Không có market-data loader, spread model riêng, liquidation, margin, leverage hay forced exit.
- Không dùng OHLC candle để suy diễn fill; caller phải cung cấp aggregate trades hoặc dữ liệu chi tiết
  tương đương.
- `Liquidity` được cung cấp trong event để test maker/taker economics; P3/P4 mới xác định nó từ hành
  vi exchange thực tế.
- Fill ratio của P2 là filled quantity chia tổng submitted intent quantity trong run.

## 5. Test & Quality Evidence

| Kiểm tra | Kết quả |
|---|---|
| `pytest` | 48/48 cases passed |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `mypy src` | Passed ở strict mode |
| `python -m pip check` | Passed; no broken requirements |
| Runtime dependency | Không thêm dependency mới |

Các test P2 bao phủ initial intents, partial fills, replacement quantity, latency, queue-ahead, price
priority, gap qua nhiều level, active logical-order aggregation, maker/taker fee, proportional fee
allocation, funding cho long/short, PnL, drawdown, inventory/notional, empty run và out-of-order input.

## 6. File Inventory

| File | Vai trò |
|---|---|
| `src/trading_bot/simulator/models.py` | Execution config, events, intents, fills và snapshots |
| `src/trading_bot/simulator/execution.py` | Deterministic order/fill simulator |
| `src/trading_bot/simulator/accounting.py` | One-way position accounting |
| `src/trading_bot/simulator/reporting.py` | Backtest config, funding và report records |
| `src/trading_bot/simulator/backtest.py` | Event orchestration, fee/funding/PnL và metrics |
| `tests/unit/test_simulator_execution.py` | Fill, partial, latency, queue và priority tests |
| `tests/unit/test_backtest_reporting.py` | Fee, funding, PnL và report tests |

## 7. Git Evidence

- `45a9507` — `feat(simulator): add deterministic grid fill engine`
- `5d55b36` — `feat(backtest): add pnl funding and performance reports`

## 8. Phase Boundary

P2 đáp ứng exit criteria: fill/fee/funding simulation và báo cáo chỉ số chạy được, có unit tests và
quality gates sạch. P3 chưa bắt đầu; repository chưa có Binance REST/WebSocket, Testnet integration,
credential handling hay runtime trading.
