# Delivery Schedule

## Scheduling Basis

- Baseline cập nhật: 2026-09-26.
- `T0` là ngày chủ dự án cho phép bắt đầu P1.
- Ước lượng dưới đây là effort, không phải cam kết deadline; lịch sẽ được cập nhật bằng dữ liệu thực tế sau mỗi phase.

| Phase | Phụ thuộc | Ước lượng | Cửa sổ tương đối |
|---|---|---:|---|
| P1 Domain Foundation | P0 | 3–5 ngày | T0 → T0+5 |
| P2 Simulator | P1 | 4–6 ngày | Sau P1 |
| P3 Binance Public Adapter | P1 | 4–6 ngày | Sau P1; có thể song song một phần với P2 |
| P4 Execution & Persistence | P2, P3 | 5–7 ngày | Sau P2 và P3 |
| P5 Risk & Recovery | P4 | 5–7 ngày | Sau P4 |
| P6 API & Observability | P4, P5 | 3–5 ngày | Sau P5 |
| P7 Testnet Soak | P2–P6 | 7–14 ngày lịch | Sau toàn bộ automated checks |
| P8 Live Canary | P7 + phê duyệt | 3–7 ngày lịch | Không tự động lên lịch |

## Critical Path

`P1 → P2/P3 → P4 → P5 → P6 → P7 → approval → P8`

Ước lượng phát triển hiện tại là 5–7 tuần cho một kỹ sư, chưa tính trì hoãn do API, dữ liệu test hoặc thay đổi phạm vi. Checkpoint tiếp theo diễn ra ngay sau P1 để thay ước lượng bằng số liệu thực tế.
