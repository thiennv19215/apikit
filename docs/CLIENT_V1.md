# API v1 dành cho client

Base URL: `https://apikit.shopcongngheso5.io.vn` (cần xác minh deployment).
Local: `http://127.0.0.1:8100`.
Client gửi ảnh Base64 ngay trong request. Server xử lý việc đưa ảnh lên provider
nội bộ; client không cần gọi một endpoint riêng cho bước này.

## Endpoint client

| Method | Endpoint | Kết quả |
|---|---|---|
| GET | `/v1/health` | Trạng thái bảo trì và khả năng nhận tác vụ |
| POST | `/v1/images/generations` | 202, job ảnh |
| POST | `/v1/videos/generations` | Video Omni: chưa hoàn tất tích hợp, xem bên dưới |
| POST | `/v1/jobs/status` | Trạng thái nhiều job |
| GET | `/v1/jobs/{job_id}` | Trạng thái một job |
| GET | `/v1/jobs/status/{job_id}` | Alias trạng thái |
| GET | `/v1/jobs/{job_id}/executions` | Lịch sử thực thi; 404 nếu không tồn tại |
| POST, GET | `/v1/characters` | 201 tạo / 200 danh sách |
| GET, PATCH, DELETE | `/v1/characters/{id}` | 200 đọc/sửa, 204 xóa; 404 nếu không tồn tại |
| GET | `/v1/characters/{id}/reference-images/{index}` | 307 khi có URL ở index 0; nếu không 404 |
| POST | `/v1/characters/{id}/images/generations` | 202; alias `/images` |
| POST | `/v1/characters/{id}/videos/generations` | Cùng giới hạn video Omni; alias `/videos` |

## Kiểm tra backend trước khi gửi tác vụ

`GET /v1/health` không tạo job. Response luôn có `Cache-Control: no-store`.

| HTTP / status | Client xử lý |
|---|---|
| 200 / `ready` | Dành cho trạng thái đầy đủ khi các khả năng đã tích hợp |
| 200 / `degraded` | Chỉ bật tính năng có `capabilities.*.available=true` |
| 503 / `maintenance` | Hiển thị bảo trì, không gửi tác vụ mới; thử lại sau `Retry-After` |
| 503 / `unavailable` | Backend/provider chưa sẵn sàng; không kết luận là bảo trì |

Hiện video Omni chưa tích hợp xong nên khi ảnh sẵn sàng, status là `degraded`:

```json
{
  "status": "degraded",
  "maintenance": false,
  "accepting_requests": true,
  "message": "Image generation is available; Omni Flash integration is not ready.",
  "retry_after_seconds": null,
  "capabilities": {
    "image_generation": {"available": true, "reason": null},
    "video_generation": {"available": false, "reason": "OMNI_INTEGRATION_PENDING"}
  }
}
```

`accepting_requests=true` không có nghĩa mọi tính năng đều sẵn sàng; phải đọc
capability của thao tác định dùng. Trạng thái provider chỉ là kiểm tra kết nối,
không đảm bảo quota hoặc yêu cầu tiếp theo sẽ thành công.

Bảo trì trả `maintenance=true`, `accepting_requests=false`, hai capability false
và `Retry-After: 60`. Lúc này request ghi/tạo mới trả 503; vẫn đọc và poll job cũ.
Mất provider/backend trả `Retry-After: 10` và `maintenance=false`.
Timeout, lỗi kết nối, proxy trả HTML hoặc server tắt: hiển thị không kết nối được,
không suy ra bảo trì. Health không bảo đảm worker đã hoàn tất job đang chạy.

## Tạo ảnh

`POST /v1/images/generations`

| Trường | Mặc định | Ý nghĩa |
|---|---|---|
| `prompt` | Bắt buộc | Nội dung ảnh |
| `model` | `NANO_BANANA_PRO` | `NANO_BANANA_PRO` hoặc `NANO_BANANA_2` |
| `image_model` | Không | Alias `model`; gửi cả hai khác nhau trả 422 |
| `aspect_ratio` | Ngang | `16:9`, `9:16`, `1:1`, `3:4`, `4:3` hoặc enum tương ứng |
| `input_images` | Không | Danh sách ảnh tham chiếu Base64 |
| `count`, `variant_count` | `1` | Chưa hỗ trợ yêu cầu nhiều biến thể; giữ 1 |

Alias model không phân biệt hoa/thường: `pro`, `bananapro`, `banana_pro` →
`NANO_BANANA_PRO`; `banana2`, `banana 2`, `banana_2`, `fast` → `NANO_BANANA_2`.

```json
{
  "prompt": "A quiet mountain lake at sunrise",
  "model": "NANO_BANANA_2",
  "aspect_ratio": "16:9",
  "input_images": [{"image_base64": "<BASE64_IMAGE>", "mime_type": "image/png"}]
}
```

`mime_type` mặc định `image/jpeg`. Bỏ `input_images` để tạo ảnh từ prompt.

## Video: chỉ Omni Flash

Contract client yêu cầu `model="omni_flash"`, không cung cấp lựa chọn model video khác.

**Chưa hoàn tất tích hợp thực thi.** Các ví dụ dưới đây mô tả contract cần tích hợp,
không phải cam kết đã chạy. Worker v1 chưa nối module Omni thật và polling của nó;
transport hiện tại chưa có payload Omni tương thích. Không được dùng model khác
thay thế hoặc bỏ end frame/reference rồi coi request là thành công.

| Chế độ | `generation_type` | Ảnh đầu vào |
|---|---|---|
| First frame (`abra_i2v`) | `image_to_video` | 1 ảnh role `start_frame` |
| First + Last | `image_to_video` | 1 `start_frame` + 1 `end_frame` |
| Reference-to-video (`abra_r2v`) | `reference_to_video` | 1–7 ảnh role `reference` |

Module Omni có thời lượng 4/6/8/10 giây và tỷ lệ `16:9`/`9:16`.
`generation_type` là tên ưu tiên, `type` là alias cũ; gửi hai giá trị khác nhau trả 422.
Thoại mô tả trong `prompt`; chưa có công tắc `dialogue` hoạt động.

Ví dụ First+Last (chưa sẵn sàng chạy):

```json
{
  "model": "omni_flash",
  "generation_type": "image_to_video",
  "prompt": "0-3s: Camera advances. 3-6s: Subject turns. 6-8s: Hold on final pose.",
  "duration_seconds": 8,
  "aspect_ratio": "16:9",
  "input_images": [
    {"image_base64": "<START_IMAGE>", "mime_type": "image/png", "role": "start_frame"},
    {"image_base64": "<END_IMAGE>", "mime_type": "image/png", "role": "end_frame"}
  ]
}
```

First frame: bỏ `end_frame`. R2V: đổi type thành `reference_to_video`, dùng 1–7
ảnh role `reference`. Việc khai báo đủ trường chưa có nghĩa provider đã hỗ trợ.

## Nhân vật

Create nhận `name` bắt buộc; `description`, `image_prompt`, `voice_description`,
`entity_type` (mặc định `character`) và `input_images` Base64. Hiện chỉ lưu một ảnh
reference; kiểm tra response vì tạo nhân vật có thể thành công khi ảnh chưa có.

PATCH hỗ trợ sửa tên, mô tả, image prompt, voice description và entity type;
chưa hỗ trợ thay ảnh Base64 qua PATCH.

Tạo ảnh nhân vật nhận `prompt`, `model`/`image_model`, `aspect_ratio`, `input_images`,
`variant_count=1`. Tỷ lệ mặc định `16:9`; bỏ model thì dùng mặc định backend.
Truyền model/aspect ở từng request generation; chúng chưa được lưu trong catalog.

## Theo dõi kết quả

1. Gửi request tạo ảnh, nhận HTTP 202 và `job_id`: chỉ xác nhận đã xếp hàng.
2. Gọi `GET /v1/jobs/{job_id}` hoặc `POST /v1/jobs/status` với
   `{"job_ids":["<JOB_ID>"]}`; cũng nhận `{"job_id":"<JOB_ID>"}`.
3. Poll theo `metadata.poll_after_seconds`, thường 10 giây.
4. `metadata.done=true` gồm cả thành công và thất bại; kiểm tra `jobs[].status`.
5. Khi complete lấy `jobs[].media[].url`; khi failed đọc `jobs[].error`.

Response có `jobs`, `metadata`. Trường ngoài cùng `job_id`, `status`, `type`,
`generation_type`, `media`, `error` phản ánh job đầu tiên cho client cũ.
Trạng thái: `queued`, `running`, `complete`, `failed`.
Kích thước, thumbnail, thời lượng có thể null.

Job không tồn tại: HTTP 200, failed/`JOB_NOT_FOUND`. Validation: 422 dạng `detail`.
Lỗi generation thường là `GENERATION_FAILED`, chi tiết nằm trong message;
trường retry mở rộng chưa phải phân loại hoàn chỉnh. Không tự gửi lại generation
khi timeout nếu chưa kiểm tra trạng thái job cũ.
