# Security Status

## Current Posture

| Control | Trạng thái | Bằng chứng/Ghi chú |
|---|---|---|
| Secret committed | `PASS` | Không phát hiện secret được gán giá trị trong baseline |
| `.env` ignored | `PASS` | `.gitignore` loại `.env` và `.env.*`, giữ `.env.example` |
| Live trading | `DISABLED` | Chưa có runtime hoặc credential |
| Withdrawal permission | `NOT_CONFIGURED` | API key chưa thuộc phạm vi repository |
| Dependency manifest | `PASS` | `pyproject.toml` chỉ có dev-tool dependencies ở P1 |
| Dependency lock/scanning | `NOT_CONFIGURED` | Chưa có lockfile hoặc vulnerability scanner |
| CI security checks | `NOT_AVAILABLE` | Chưa có CI |
| Numeric safety | `PASS_P1` | Domain từ chối float; grid sử dụng `Decimal` và exchange increments |

## Required Controls Before Testnet

- API key Testnet tách biệt, không bao giờ commit.
- Secret chỉ đọc từ environment hoặc secret manager.
- Log phải redact key, signature, header và account identifiers.
- Request signing có clock synchronization và giới hạn `recvWindow`.
- Dependency lock và vulnerability scan phải có trước khi chạy service dài hạn.

## Required Controls Before Live Canary

- API key chỉ có quyền Futures cần thiết, không có withdrawal.
- IP whitelist bắt buộc.
- Live mode mặc định `false` và cần xác nhận rõ ràng khi bật.
- Isolated Margin, leverage cap, notional cap và daily-loss cap được enforce.
- Kill switch, stale-feed breaker, reconciliation và backup/restore đã được thử.
- Không ghi raw user-data event chứa thông tin nhạy cảm nếu chưa redact.

## Incident Rule

Nếu nghi ngờ lộ secret: dừng service, revoke/rotate key, hủy open orders nếu an toàn, đối soát vị thế, lưu audit evidence đã redact và chỉ khởi động lại sau review.
