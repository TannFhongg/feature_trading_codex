# P3 Binance Public Adapter — Completion Report

## Report Metadata

| Thuộc tính | Giá trị |
|---|---|
| Phase | P3 — Binance Public Adapter |
| Trạng thái | `COMPLETE` |
| Ngày xác minh | 2026-09-26, Asia/Bangkok |
| Branch | `main` |
| Network scope | Binance USDⓈ-M Futures public Testnet only by default |
| Credential/order scope | Không dùng credential; không có submit/cancel/query order |
| Phase tiếp theo | P4 `PLANNED/NOT_STARTED` |

## 1. Kết quả P3

P3 bổ sung adapter public bất đồng bộ để lấy exchange rules, đồng bộ server time và nhận market data
typed từ Binance USDⓈ-M Futures. Cấu hình mặc định chỉ tới Testnet. Toàn bộ giá, quantity và funding
rate từ Binance được parse từ decimal string thành `Decimal`; adapter không nhận API key/secret và
không expose bất kỳ thao tác giao dịch nào.

## 2. Public REST

- `GET /fapi/v1/time` là nguồn duy nhất cho server time; không dùng trường `serverTime` trong
  `exchangeInfo`.
- Đồng bộ clock lấy timestamp local trước/sau request, tính midpoint và chọn mẫu có RTT thấp nhất.
- `GET /fapi/v1/exchangeInfo` chỉ chấp nhận symbol `TRADING`, `PERPETUAL`, quote/margin asset USDT.
- Grid rules lấy đúng `tickSize`, `stepSize`, `minQty` và `notional` từ `PRICE_FILTER`, `LOT_SIZE` và
  `MIN_NOTIONAL`; không dùng precision fields thay cho increments.
- Public GET retry có giới hạn với exponential backoff cho transport error, `408`, `429`, `5xx`, tôn
  trọng `Retry-After`; `418` fail ngay để tránh tiếp tục gây IP ban.
- HTTP timeout bị giới hạn; transport error không đưa raw response, credential hoặc payload nhạy cảm
  vào exception.

## 3. Public Market WebSocket

- Hỗ trợ aggregate trade, mark price/funding và individual book ticker dưới dạng immutable records.
- Aggregate trade suy ra aggressor BUY/SELL từ cờ buyer-is-maker để tương thích input simulator.
- Dùng route split hiện hành của Binance: `/market` cho `aggTrade`/`markPrice@1s`, `/public` cho
  `bookTicker`; hai nhóm không được trộn trên một connection.
- Client tự động ping/pong, có connection/open timeout, bounded receive queue và stale threshold.
- Khi disconnect/stale/protocol error, stream đóng connection cũ và reconnect với exponential backoff.
- `MarketStreamHealth` công bố state, stale flag, thời điểm message gần nhất, reconnect count và lỗi gần
  nhất để P5 có thể gắn stale-feed breaker.
- Event time được kiểm tra không đi lùi riêng theo từng stream type; payload sai schema, sai symbol,
  float tài chính hoặc event ngoài subscription bị fail closed.

## 4. Testnet Evidence

Public smoke tests đã chạy trực tiếp với `RUN_BINANCE_TESTNET=1`, không dùng credential:

- Time sync và exchange rules của `BTCUSDT` qua `https://demo-fapi.binance.com`.
- Mark price và aggregate trade qua routed `/market` WebSocket.
- Best bid/ask qua routed `/public` WebSocket.
- Kết quả phiên xác minh: 3/3 integration tests passed trong 13.79 giây.

Test integration mặc định bị skip để bộ test local/CI không phụ thuộc network. Lệnh chạy rõ ràng:

```powershell
$env:RUN_BINANCE_TESTNET='1'
.\.venv\Scripts\python -m pytest tests/integration/test_binance_public_testnet.py
```

## 5. Test & Quality Evidence

| Kiểm tra | Kết quả |
|---|---|
| Unit tests | 74/74 passed |
| Testnet integration | 3/3 passed |
| Full pytest mặc định | 74 passed, 3 skipped integration |
| Full pytest với Testnet | 77 passed trong 14.67 giây |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `mypy src` | Passed ở strict mode |
| `python -m pip check` | Passed; no broken requirements |

Unit tests bao phủ strict payload validation, Decimal parsing, exchange filters, clock midpoint/RTT,
bounded retry, `Retry-After`, `418`, routed URL, aggressor side, mark/funding, best bid/ask, reconnect,
stale detection, health state và out-of-order event.

## 6. Runtime Dependencies

- `httpx>=0.28,<1` cho async public REST.
- `websockets>=15,<17` cho public WebSocket và automatic ping/pong.
- Chưa có lockfile/vulnerability scanning; risk R-010 vẫn `OPEN` và chặn long-running service.

## 7. File Inventory

| File | Vai trò |
|---|---|
| `src/trading_bot/binance/models.py` | Config, clock sample, market events và health snapshot |
| `src/trading_bot/binance/parsing.py` | Strict REST/WebSocket JSON parsing |
| `src/trading_bot/binance/rest.py` | Async time sync, exchange rules và bounded retry |
| `src/trading_bot/binance/stream.py` | Routed WebSocket, stale detection và reconnect |
| `tests/unit/test_binance_public_rest.py` | REST parser, sync, retry và config tests |
| `tests/unit/test_binance_market_stream.py` | Event parser, route, health, stale/reconnect tests |
| `tests/integration/test_binance_public_testnet.py` | Public Testnet smoke tests |

## 8. Git Evidence

- `af2e314` — `feat(binance): add public REST adapter`
- `942dcda` — `feat(binance): add resilient public market streams`
- `48d6892` — `test(binance): verify public adapter on Testnet`

## 9. Phase Boundary

P3 đáp ứng exit criteria cho exchange rules, time sync và public market WebSocket trên Testnet. P4
chưa bắt đầu: chưa có signing, private/account endpoint, User Data Stream, order execution, database,
event ledger hay reconciliation. Public market connectivity không phải quyền cho phép trading runtime.
