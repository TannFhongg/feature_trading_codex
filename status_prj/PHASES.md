# Project Phases

| ID | Phase | Trạng thái | Exit criteria chính |
|---|---|---|---|
| P0 | Planning & Governance | `COMPLETE` | Kế hoạch, contributor guide và status reports được version control |
| P1 | Domain Foundation | `COMPLETE` | Scaffold, typed domain, Arithmetic Grid và unit tests đạt |
| P2 | Simulator & Backtest Core | `COMPLETE` | Fill/fee/funding simulation và báo cáo chỉ số chạy được |
| P3 | Binance Public Adapter | `COMPLETE` | Exchange rules, time sync và market WebSocket ổn định trên Testnet |
| P4 | Execution & Persistence | `COMPLETE` | Order lifecycle, event ledger và reconciliation idempotent |
| P5 | Risk & Recovery | `COMPLETE` | Limits, circuit breakers, restart recovery và emergency exit đạt |
| P6 | Control API & Observability | `PLANNED` | Control endpoints, metrics, logs và alerts hoạt động |
| P7 | Testnet Soak | `PLANNED` | Chạy 7–14 ngày, không duplicate/orphan order |
| P8 | Live Canary | `PLANNED` | Chỉ bắt đầu khi có phê duyệt rõ ràng và hard capital cap |

## Phase Rules

- Không bắt đầu phase phụ thuộc nếu exit criteria của phase trước chưa đạt.
- Phát hiện lỗi safety-critical sẽ mở lại phase liên quan.
- P8 không tự động bắt đầu sau P7; đây là gate cần quyết định của chủ dự án.
- Mỗi phase phải có commit riêng, kết quả kiểm tra và cập nhật báo cáo trạng thái.
- P5 hoàn thành ngày 2026-09-26; P6 chưa được bắt đầu ngầm.
