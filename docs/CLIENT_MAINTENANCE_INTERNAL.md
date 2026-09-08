# Vận hành bảo trì client — chỉ dành cho agent/operator

Đặt biến môi trường `CLIENT_MAINTENANCE=1` rồi khởi động lại backend để bật.
Đặt `0` rồi khởi động lại để tắt. Đây là cấu hình khởi động, không có endpoint
client để thay đổi trạng thái vận hành.

Health client trả 503/maintenance. Middleware chặn POST/PUT/PATCH/DELETE dưới
`/v1/`, trừ POST `/v1/jobs/status`; các GET và API nội bộ không thay đổi.
Worker tiếp tục xử lý job cũ: cờ này không drain, dừng worker hay hủy job.
Các request đã qua middleware trước khi bật cờ không được hủy.

Endpoint health kiểm tra DB bằng SELECT 1 và kết nối provider, không gọi tạo media,
không lộ account/email/project. Video luôn báo OMNI_INTEGRATION_PENDING cho đến
khi hoàn tất nối Omni thật và polling vào worker. Khi server tắt hoàn toàn,
proxy/deployment phải tự phục vụ trang hoặc response bảo trì nếu cần.
