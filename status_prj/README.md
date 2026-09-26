# Project Reporting Center

`status_prj/` là nơi theo dõi trạng thái thực tế của dự án. `instruction.md` mô tả định hướng sản phẩm; các file trong thư mục này ghi nhận tiến độ triển khai, gate chất lượng và các quyết định đang có hiệu lực.

## Danh mục báo cáo

- `STATUS.md`: ảnh chụp trạng thái hiện tại và bằng chứng xác minh.
- `P1_REPORT.md`: báo cáo bàn giao chi tiết cho Phase 1 — Domain Foundation.
- `P2_REPORT.md`: báo cáo bàn giao chi tiết cho Phase 2 — Simulator & Backtest Core.
- `PHASES.md`: các phase, điều kiện vào/ra và trạng thái.
- `PLAN.md`: phạm vi công việc tiếp theo và Definition of Done.
- `SCHEDULE.md`: thứ tự phụ thuộc và ước lượng thời gian.
- `CONTRACTS.md`: hợp đồng domain, interface và safety invariant.
- `SECURITY.md`: hiện trạng và gate bảo mật.
- `RISKS.md`: risk register cùng biện pháp giảm thiểu.

## Quy ước trạng thái

- `COMPLETE`: đã hoàn thành và có bằng chứng kiểm tra.
- `IN_PROGRESS`: đang thực hiện, chưa đạt exit criteria.
- `PLANNED`: đã xác định nhưng chưa bắt đầu.
- `BLOCKED`: không thể tiếp tục nếu chưa có đầu vào hoặc thay đổi bên ngoài.

Không dùng phần trăm tiến độ tùy ý. Mỗi lần thay đổi phase, phạm vi, lịch, contract hoặc security posture phải cập nhật file liên quan trong cùng commit. Tuyệt đối không ghi API key, tài khoản, vị thế hoặc dữ liệu giao dịch nhạy cảm vào các báo cáo này.
