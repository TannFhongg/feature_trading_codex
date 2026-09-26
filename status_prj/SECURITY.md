# Security Status

## Current Posture

| Control | Trạng thái | Bằng chứng/Ghi chú |
|---|---|---|
| Secret committed | `PASS` | Không dùng credential thật; fixture chỉ có literal giả rõ ràng |
| `.env` ignored | `PASS` | `.gitignore` loại `.env`/`.env.*`, giữ `.env.example` placeholder-only |
| Testnet-first | `PASS_P4` | Private/public config mặc định tới USDⓈ-M Futures Testnet |
| Order submission | `DISABLED_DEFAULT` | Cần explicit `order_submission_enabled`; chưa có runtime entry point |
| Live trading | `DISABLED` | Mainnet submit cần thêm explicit `live_trading_enabled`; chưa được chạy |
| Credential redaction | `PASS_P4` | Credential/config `repr` không lộ key/secret; transport error không chứa raw signed request |
| Request signing | `PASS_P4` | HMAC-SHA256, time offset, bounded `recvWindow`, percent-encoded parameters |
| Withdrawal permission | `NOT_CONFIGURED` | Không có API key thuộc phạm vi repository |
| Dependency manifest | `PASS_P3` | HTTPX/websockets có bounded version ranges |
| Dependency lock/scanning | `NOT_CONFIGURED` | Chưa có lockfile/vulnerability scanner; chặn service dài hạn |
| CI security checks | `NOT_AVAILABLE` | Chưa có CI |
| Numeric safety | `PASS_P4` | Financial models/parsers dùng `Decimal`; binary float bị reject |
| Persistence security | `LOCAL_ONLY` | SQLite không chứa credential; account/trading data vẫn phải được bảo vệ ở deployment |
| Runtime risk approval | `PASS_P5_FAKE` | P5 risk-managed boundary audit decision trước durable executor; chưa có application runtime |
| Emergency exit | `PASS_P5_FAKE` | Persist-before-mutation, cancel-all và deterministic MARKET `reduceOnly`; chưa chạy authenticated Testnet |

## Authenticated Testnet Gate

Trước khi chạy private Testnet bằng tài khoản thật phải:

- Cấp API key Testnet riêng qua environment hoặc secret manager, tuyệt đối không commit.
- Xác minh withdrawal bị tắt và áp dụng IP restriction nếu môi trường hỗ trợ.
- Xác minh log/telemetry redact API key, signature, headers và account identifiers end-to-end.
- P5 risk approval, stale-feed breaker, restart reconciliation gate và kill switch đã có deterministic
  test; phải xác minh lại trên authenticated Testnet trước runtime tự động gửi lệnh.
- Có dependency lock và vulnerability scan trước long-running soak.

## Live Canary Gate

- Hoàn thành P6–P7 cùng các deployment gate còn lại, bao gồm backup/restore và soak.
- API key chỉ có quyền Futures cần thiết, không withdrawal, bắt buộc IP whitelist.
- Live mode mặc định false và cần phê duyệt rõ ràng của chủ dự án; P7 không tự động mở P8.
- Không ghi raw user-data/account event nhạy cảm nếu chưa có redaction và retention policy.

## Incident Rule

Nếu nghi ngờ lộ secret: dừng service, revoke/rotate key, hủy open orders nếu an toàn, đối soát vị thế,
lưu audit evidence đã redact và chỉ khởi động lại sau review.
