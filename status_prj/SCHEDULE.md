# Delivery Schedule

## Scheduling Basis

- Baseline cập nhật: 2026-09-26 sau khi hoàn tất P5.
- P1–P5 hoàn thành ngày 2026-09-26; không có time tracking đủ tin cậy để báo person-hours.
- Ước lượng dưới đây là effort, không phải cam kết deadline.

| Phase | Trạng thái | Phụ thuộc | Ước lượng/Cửa sổ |
|---|---|---|---|
| P1 Domain Foundation | `COMPLETE` | P0 | Hoàn thành 2026-09-26 |
| P2 Simulator | `COMPLETE` | P1 | Hoàn thành 2026-09-26 |
| P3 Binance Public Adapter | `COMPLETE` | P1 | Hoàn thành 2026-09-26 |
| P4 Execution & Persistence | `COMPLETE` | P2, P3 | Hoàn thành 2026-09-26 |
| P5 Risk & Recovery | `COMPLETE` | P4 | Hoàn thành 2026-09-26 |
| P6 API & Observability | `PLANNED` | P4, P5 | 3–5 ngày sau P5 |
| P7 Testnet Soak | `PLANNED` | P2–P6 | 7–14 ngày lịch sau automated checks |
| P8 Live Canary | `PLANNED` | P7 + phê duyệt | 3–7 ngày lịch; không tự động lên lịch |

## Critical Path

`P1 → P2/P3 → P4 → P5 → P6 → P7 → approval → P8`

P5 đã hoàn tất ngày 2026-09-26. Checkpoint tiếp theo là yêu cầu cho phép bắt đầu P6; chưa có phase mới
được khởi động ngầm.
