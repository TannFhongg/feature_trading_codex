# Risk Register

| ID | Rủi ro | Impact | Biện pháp chính | Trạng thái |
|---|---|---|---|---|
| R-001 | Timeout/503 tạo duplicate order | Critical | Deterministic client ID, persist-before-send, query-before-retry và bounded retry | `CONTROL_TESTED_P4` |
| R-002 | WebSocket stale hoặc mất event | Critical | Market/User stream health, keepalive, reconnect, ordering check và REST reconciliation; P5 còn thiếu breaker | `PARTIAL_CONTROL_TESTED_P4` |
| R-003 | Sai tick/step/minNotional | High | `SymbolRules`, Decimal quantization và strict filter parser | `CONTROL_TESTED_P3` |
| R-004 | Race giữa cancel và fill | High | Exchange event là nguồn sự thật; monotonic fill state và idempotent ledger | `CONTROL_TESTED_P4` |
| R-005 | Breakout gây tích lũy vị thế/thanh lý | Critical | Isolated margin, caps, hard boundary và reduce-only exit trong P5 | `PLANNED` |
| R-006 | Phí/funding xóa lợi nhuận grid | High | Backtest fee/funding, private commission endpoint và funding income ledger; P5 còn thiếu runtime gate | `PARTIAL_CONTROL_TESTED_P4` |
| R-007 | Backtest quá lạc quan | High | Partial fill, latency, fee, funding, volume allocation và conservative queue; replay calibration còn thiếu | `PARTIAL_CONTROL_TESTED_P2` |
| R-008 | Lộ API credential | Critical | `.env` ignore, redacted config repr, secret-safe errors, Testnet-first; secret manager/IP restriction còn là deployment gate | `PARTIAL_CONTROL_TESTED_P4` |
| R-009 | Scope creep làm trễ safety work | Medium | Phase gates và boundary P1–P4 rõ ràng | `CONTROL_ACTIVE` |
| R-010 | Dependencies chưa khóa phiên bản | Medium | Bounded ranges; cần lockfile và vulnerability scan trước service dài hạn | `OPEN` |
| R-011 | Binance đổi private/public endpoint hoặc payload | High | Strict parsers, route-specific tests, fake contracts, public Testnet smoke và review official docs | `PARTIAL_CONTROL_TESTED_P4` |
| R-012 | SQLite hỏng/mất hoặc không phù hợp multi-process | High | WAL, transaction, reopen test; P5/P6 cần migration, backup/restore và production datastore decision | `OPEN` |
| R-013 | Local/exchange diverge sau restart | Critical | Audit ledger và read-only reconciliation; P5 còn thiếu restart coordinator/fail-closed resume gate | `PARTIAL_CONTROL_TESTED_P4` |

## Review Cadence

- Review sau mỗi phase và trước mọi authenticated Testnet/live run.
- Risk Critical chưa có control đã kiểm thử sẽ chặn phase phụ thuộc.
- Mọi incident hoặc near-miss phải tạo risk entry hoặc cập nhật mitigation hiện có.
