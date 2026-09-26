# Risk Register

| ID | Rủi ro | Impact | Biện pháp chính | Trạng thái |
|---|---|---|---|---|
| R-001 | Timeout/503 tạo duplicate order | Critical | Deterministic client ID và reconcile trước retry | `PLANNED` |
| R-002 | WebSocket stale hoặc mất event | Critical | Heartbeat, stale breaker, REST reconciliation | `PLANNED` |
| R-003 | Sai tick/step/minNotional | High | `SymbolRules`, Decimal quantization và boundary tests | `PLANNED` |
| R-004 | Race giữa cancel và fill | High | Exchange event là nguồn sự thật, idempotent ledger | `PLANNED` |
| R-005 | Breakout gây tích lũy vị thế/thanh lý | Critical | Isolated margin, caps, hard boundary và reduce-only exit | `PLANNED` |
| R-006 | Phí/funding xóa lợi nhuận grid | High | Profit-after-cost gate và account commission thực tế | `PLANNED` |
| R-007 | Backtest quá lạc quan | High | Partial fill, latency, fee, funding và queue assumptions bảo thủ | `PLANNED` |
| R-008 | Lộ API credential | Critical | `.env` ignore, secret manager, redaction và IP whitelist | `OPEN` |
| R-009 | Scope creep làm trễ safety work | Medium | Phase gates và P1 non-goals rõ ràng | `ACTIVE` |

## Review Cadence

- Review sau mỗi phase và trước mọi Testnet/live run.
- Risk Critical chưa có control đã kiểm thử sẽ chặn phase tiếp theo.
- Mọi incident hoặc near-miss phải tạo risk entry hoặc cập nhật mitigation hiện có.
