# Risk Register

| ID | Rủi ro | Impact | Biện pháp chính | Trạng thái |
|---|---|---|---|---|
| R-001 | Timeout/503 tạo duplicate order | Critical | Deterministic client ID và reconcile trước retry | `PLANNED` |
| R-002 | WebSocket stale hoặc mất event | Critical | P3 có ping/pong, stale detection, reconnect và health snapshot cho market stream; User Data Stream/reconciliation chờ P4–P5 | `PARTIAL_CONTROL_TESTED_P3` |
| R-003 | Sai tick/step/minNotional | High | `SymbolRules`, Decimal quantization và strict `exchangeInfo` filter parser | `CONTROL_TESTED_P3` |
| R-004 | Race giữa cancel và fill | High | Exchange event là nguồn sự thật, idempotent ledger | `PLANNED` |
| R-005 | Breakout gây tích lũy vị thế/thanh lý | Critical | Isolated margin, caps, hard boundary và reduce-only exit | `PLANNED` |
| R-006 | Phí/funding xóa lợi nhuận grid | High | P2 báo maker/taker fee, funding và net grid profit; commission tài khoản thực tế chờ P4 | `PARTIAL_CONTROL_TESTED_P2` |
| R-007 | Backtest quá lạc quan | High | P2 có partial fill, latency, fee, funding, volume allocation và queue mặc định bảo thủ; replay/calibration chờ dữ liệu thật | `PARTIAL_CONTROL_TESTED_P2` |
| R-008 | Lộ API credential | Critical | `.env` ignore, secret manager, redaction và IP whitelist | `OPEN` |
| R-009 | Scope creep làm trễ safety work | Medium | Phase gates và boundary P1–P3 rõ ràng | `CONTROL_ACTIVE` |
| R-010 | Dependencies chưa khóa phiên bản | Medium | P3 thêm bounded ranges cho HTTPX/websockets; vẫn cần lockfile và vulnerability scan trước service dài hạn | `OPEN` |
| R-011 | Binance đổi endpoint hoặc payload public | High | Strict parser, route-specific tests, Testnet smoke và review official change notice mỗi lần nâng dependency/deploy | `CONTROL_TESTED_P3` |

## Review Cadence

- Review sau mỗi phase và trước mọi Testnet/live run.
- Risk Critical chưa có control đã kiểm thử sẽ chặn phase tiếp theo.
- Mọi incident hoặc near-miss phải tạo risk entry hoặc cập nhật mitigation hiện có.
