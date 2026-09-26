# Security Status

## Current Posture

| Control | Trạng thái | Bằng chứng/Ghi chú |
|---|---|---|
| Secret committed | `PASS` | Không dùng credential thật; fixture chỉ có literal giả rõ ràng |
| `.env` ignored | `PASS` | `.gitignore` loại `.env`/`.env.*`, giữ `.env.example` placeholder-only |
| Testnet-first | `PASS_P6` | Application/private/public config mặc định Testnet; dry-run là mặc định runtime |
| Order submission | `DISABLED_DEFAULT` | Runtime có entry point nhưng chỉ startup opt-in mới bật submit; Control API không thể đổi cờ này |
| Live trading | `DISABLED` | Mainnet submit cần thêm explicit `live_trading_enabled`; chưa được chạy |
| Credential redaction | `PASS_P6` | Config/log formatter redact key/token/signature/header/account identifiers; transport error không chứa raw signed request |
| Request signing | `PASS_P4` | HMAC-SHA256, time offset, bounded `recvWindow`, percent-encoded parameters |
| Withdrawal permission | `NOT_CONFIGURED` | Không có API key thuộc phạm vi repository |
| Dependency manifest | `PASS_P6` | FastAPI, Uvicorn, HTTPX và websockets có bounded version ranges |
| Dependency lock/scanning | `NOT_CONFIGURED` | Chưa có lockfile/vulnerability scanner; chặn service dài hạn |
| CI security checks | `NOT_AVAILABLE` | Chưa có CI |
| Numeric safety | `PASS_P4` | Financial models/parsers dùng `Decimal`; binary float bị reject |
| Persistence security | `LOCAL_ONLY` | SQLite không chứa credential; account/trading data vẫn phải được bảo vệ ở deployment |
| Runtime risk approval | `PASS_P6_FAKE` | Strategy runtime chỉ phát intent; orchestrator bắt buộc gọi P5 risk-managed/P4 durable executor |
| Control API | `PASS_P6_FAKE` | Local/private bind, bearer auth constant-time, durable idempotency/audit và không có config mutation endpoint |
| Runtime supervision | `PASS_P6_FAKE` | Task crash, bounded-queue overflow, stale feed và shutdown đều có fail-closed deterministic test |
| Emergency exit | `PASS_P6_FAKE` | Authenticated command gọi P5 persist-before-mutation cancel-and-flatten; chưa chạy authenticated Testnet |

## Authenticated Testnet Gate

Trước khi chạy private Testnet bằng tài khoản thật phải:

- Cấp API key Testnet riêng qua environment hoặc secret manager, tuyệt đối không commit.
- Xác minh withdrawal bị tắt và áp dụng IP restriction nếu môi trường hỗ trợ.
- Xác minh log/telemetry redact API key, signature, headers và account identifiers end-to-end.
- P5 risk approval, stale-feed breaker, restart reconciliation gate và kill switch đã có deterministic
  test; phải xác minh lại trên authenticated Testnet trước runtime tự động gửi lệnh.
- Có dependency lock và vulnerability scan trước long-running soak.

## P6 Runtime & Control Gate — IMPLEMENTED WITH FAKES

- Application orchestrator phải fail closed, supervise mọi long-running task và không auto-resume sau
  restart/reconnect.
- Control API mặc định bind local/private interface; command thay đổi trạng thái cần
  authentication/authorization và audit record chống replay/duplicate.
- Không endpoint nào được bật động `order_submission_enabled` hoặc `live_trading_enabled`; hai cờ vẫn
  là startup/deployment opt-in riêng.
- Strategy runtime không được gọi private adapter trực tiếp; mọi exposure-increasing intent bắt buộc
  đi qua risk-managed durable executor.
- Log, metric và alert phải redact API key, signature, header, client/account identifier và raw
  user-data payload theo retention policy.
- Các control trên đã có automated deterministic evidence. Chúng chưa thay thế authenticated Testnet
  validation, external secret manager, dependency lock/scanning hoặc deployment hardening của P7.

## Live Canary Gate

- Hoàn thành P6–P7 cùng các deployment gate còn lại, bao gồm backup/restore và soak.
- API key chỉ có quyền Futures cần thiết, không withdrawal, bắt buộc IP whitelist.
- Live mode mặc định false và cần phê duyệt rõ ràng của chủ dự án; P7 không tự động mở P8.
- Không ghi raw user-data/account event nhạy cảm nếu chưa có redaction và retention policy.

## Incident Rule

Nếu nghi ngờ lộ secret: dừng service, revoke/rotate key, hủy open orders nếu an toàn, đối soát vị thế,
lưu audit evidence đã redact và chỉ khởi động lại sau review.
