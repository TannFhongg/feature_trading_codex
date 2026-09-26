# Delivery Schedule

## Scheduling Basis

- Baseline cập nhật: 2026-09-26 sau khi hoàn tất P3.
- P1 hoàn thành ngày 2026-09-26 trong một phiên triển khai; không có time tracking đủ tin cậy để báo person-hours.
- P2 hoàn thành ngày 2026-09-26; không có time tracking đủ tin cậy để báo person-hours.
- `T3` là 2026-09-26, ngày chủ dự án cho phép và hoàn tất P3.
- Ước lượng dưới đây là effort, không phải cam kết deadline; lịch sẽ được cập nhật bằng dữ liệu thực tế sau mỗi phase.

| Phase | Trạng thái | Phụ thuộc | Ước lượng/Cửa sổ |
|---|---|---|---|
| P1 Domain Foundation | `COMPLETE` | P0 | Hoàn thành 2026-09-26 |
| P2 Simulator | `COMPLETE` | P1 | Hoàn thành 2026-09-26 |
| P3 Binance Public Adapter | `COMPLETE` | P1 | Hoàn thành 2026-09-26 |
| P4 Execution & Persistence | `PLANNED` | P2, P3 | 5–7 ngày sau P2 và P3 |
| P5 Risk & Recovery | `PLANNED` | P4 | 5–7 ngày sau P4 |
| P6 API & Observability | `PLANNED` | P4, P5 | 3–5 ngày sau P5 |
| P7 Testnet Soak | `PLANNED` | P2–P6 | 7–14 ngày lịch sau automated checks |
| P8 Live Canary | `PLANNED` | P7 + phê duyệt | 3–7 ngày lịch; không tự động lên lịch |

## Critical Path

`P1 → P2/P3 → P4 → P5 → P6 → P7 → approval → P8`

P2 và P3 đã hoàn tất; P4 là dependency còn thiếu trên critical path. Checkpoint tiếp theo là yêu cầu
cho phép bắt đầu P4; chưa có lịch triển khai phase mới.
