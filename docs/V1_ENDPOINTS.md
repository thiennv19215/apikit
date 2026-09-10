# FlowKit Client API v1 — Tài Liệu Toàn Bộ Endpoint

Tài liệu tham chiếu chuẩn xác và toàn diện nhất cho toàn bộ các endpoint thuộc phân hệ **Client API v1** (`/v1/...`) của FlowKit.

- **Production Base URL:** `https://apikit.shopcongngheso5.io.vn`
- **Local Dev Base URL:** `http://127.0.0.1:8100`

---

## 1. Nguyên Tắc Cốt Lõi Khi Tích Hợp Client V1

1. **Ảnh đầu vào 100% Base64 (`image_base64`):**
   - Phía Client V1 **KHÔNG CÓ endpoint upload media riêng lẻ**. Truyền trực tiếp chuỗi Base64 vào trường `image_base64` của mảng `input_images`.
   - Tuyệt đối **không truyền UUID / `media_id`** của Google Flow. Mọi cố gắng truyền direct `media_id` sẽ bị từ chối với lỗi `HTTP 422`.
2. **Cơ chế Auto-Cache SHA-256:**
   - Server tự động băm mã SHA-256 nội dung Base64 để tra cứu cache. Nếu ảnh đã tải lên trước đó, server tái sử dụng UUID ngay lập tức (0ms trễ, 0 tốn captcha).
3. **Mô hình Video độc quyền Gemini Omni Flash:**
   - Hệ thống Client V1 **chỉ sử dụng Gemini Omni Flash** (`model="omni_flash"`), hỗ trợ đủ 3 chế độ tạo video: *First Frame (I2V)*, *Start + End Frame*, và *Reference-to-Video (R2V)*.
4. **Tự động Điều phối Hàng đợi (Worker Throttling):**
   - Mọi yêu cầu tạo ảnh/video đều trả về **`HTTP 202 Accepted`** ngay lập tức kèm `JobsResponse`.
   - SQLite queue tự động điều phối: tối đa 5 tác vụ chạy đồng thời, giãn cách 10s cooldown giữa các đợt dispatch, tự động failover sang tài khoản Google Flow khác khi hết quota.
5. **Hỗ trợ Batch:**
   - Cả sinh ảnh và sinh video đều có endpoint Batch riêng (`/v1/images/generations/batch` và `/v1/videos/generations/batch`), chấp nhận cả dạng object `{"requests": [...]}` lẫn mảng trực tiếp `[...]`.

---

## 2. Bảng Tra Cứu Toàn Bộ Endpoint v1 (Quick Reference)

| Nhóm | Method | Endpoint | Mô tả | Trạng thái HTTP |
|---|---|---|---|---|
| **System** | `GET` | `/v1/health` | Kiểm tra độ sẵn sàng & tính năng hệ thống | 200 OK / 503 Maintenance |
| **Ảnh (Image)** | `POST` | `/v1/images/generations` | Sinh 1 ảnh từ văn bản / ảnh tham chiếu Base64 | 202 Accepted |
| | `POST` | `/v1/images/generations/batch` | Sinh nhiều ảnh theo lô (Batch) | 202 Accepted |
| **Video** | `POST` | `/v1/videos/generations` | Sinh 1 video Omni Flash (First frame / Start+End / R2V) | 202 Accepted |
| | `POST` | `/v1/videos/generations/batch` | Sinh nhiều video Omni Flash theo lô (Batch) | 202 Accepted |
| **Jobs (Polling)** | `POST` | `/v1/jobs/status` | Tra cứu trạng thái nhiều job theo danh sách `job_ids` | 200 OK |
| | `GET` | `/v1/jobs/{job_id}` | Tra cứu trạng thái 1 job cụ thể | 200 OK / 404 |
| | `GET` | `/v1/jobs/status/{job_id}` | Alias tra cứu trạng thái 1 job | 200 OK / 404 |
| | `GET` | `/v1/jobs/{job_id}/executions` | Lịch sử audit thực thi (thời gian, profile) | 200 OK / 404 |
| **Nhân vật** | `GET` | `/v1/characters` | Danh sách nhân vật / thực thể | 200 OK |
| | `POST` | `/v1/characters` | Tạo mới nhân vật kèm ảnh tham chiếu Base64 | 201 Created |
| | `GET` | `/v1/characters/{id}` | Lấy chi tiết một nhân vật | 200 OK / 404 |
| | `PATCH` | `/v1/characters/{id}` | Cập nhật thông tin nhân vật | 200 OK / 404 |
| | `DELETE` | `/v1/characters/{id}` | Xóa nhân vật | 204 No Content |
| | `POST` | `/v1/characters/{id}/images/generations` | Sinh ảnh cho nhân vật bằng prompt riêng | 202 Accepted |
| | `POST` | `/v1/characters/{id}/videos/generations` | Sinh video cho nhân vật | 202 Accepted |
| | `GET` | `/v1/characters/{id}/reference-image` | Chuyển hướng tới link ảnh gốc nhân vật | 307 Temporary Redirect |
| **Mỹ thuật** | `GET` | `/v1/materials` | Danh sách visual styles/materials (`realistic`, `3d_pixar`...) | 200 OK |
| | `GET` | `/v1/materials/{id}` | Chi tiết phong cách & hướng dẫn prompt | 200 OK / 404 |
| **Hậu kỳ** | `POST` | `/v1/videos/concat` | Ghép nhiều clip video thành MP4 hoàn chỉnh | 200 OK |
| | `GET` | `/v1/videos/download/{filename}`| Tải file video hoàn thiện sau khi concat | 200 OK / 404 |

---

## 3. Chi Tiết Từng Endpoint

### 3.1. Hệ Thống (System)

#### `GET /v1/health`
Kiểm tra khả năng nhận tác vụ của hệ thống và các năng lực hiện hành. Không tạo job, response có header `Cache-Control: no-store`.

**Response (200 OK):**
```json
{
  "status": "ready",
  "maintenance": false,
  "accepting_requests": true,
  "message": "Image and Omni Flash video generation are available.",
  "retry_after_seconds": null,
  "capabilities": {
    "image_generation": {"available": true, "reason": null},
    "video_generation": {"available": true, "reason": null},
    "video_first_frame": {"available": true, "reason": null},
    "video_start_end": {"available": true, "reason": null},
    "video_reference": {"available": true, "reason": null}
  }
}
```

---

### 3.2. Sinh Hình Ảnh (Image Generation)

#### `POST /v1/images/generations` (Đơn lẻ)
Tạo tác vụ sinh ảnh bằng Banana Pro / Banana 2.

**Request Body:**
- `prompt` (string, bắt buộc): Mô tả hình ảnh cần tạo.
- `aspect_ratio` (string, tuỳ chọn): Tỉ lệ ảnh (`"IMAGE_ASPECT_RATIO_LANDSCAPE"`, `"IMAGE_ASPECT_RATIO_PORTRAIT"`, `"IMAGE_ASPECT_RATIO_SQUARE"`, hoặc alias `"16:9"`, `"9:16"`, `"1:1"`). Mặc định `16:9`.
- `model` (string, tuỳ chọn): `"NANO_BANANA_PRO"` (alias `"pro"`) hoặc `"NANO_BANANA_2"` (alias `"banana2"`).
- `input_images` (array, tuỳ chọn): Mảng các ảnh tham chiếu dạng Base64 `[{"image_base64": "...", "mime_type": "image/jpeg"}]`.

**Curl Example:**
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A cybernetic samurai standing in neon rain, hyper-detailed, 8k",
    "aspect_ratio": "16:9",
    "model": "pro"
  }'
```

**Response (202 Accepted):**
```json
{
  "jobs": [
    {
      "id": "job_a1b2c3d4e5f60718",
      "status": "queued",
      "type": "image",
      "generation_type": "image",
      "provider": "google_flow",
      "media": [],
      "error": null
    }
  ],
  "metadata": {
    "counts": {"queued": 1, "running": 0, "complete": 0, "failed": 0},
    "done": false,
    "poll_after_seconds": 10
  },
  "job_id": "job_a1b2c3d4e5f60718",
  "status": "queued"
}
```

---

#### `POST /v1/images/generations/batch` (Theo lô)
Gửi nhiều yêu cầu sinh ảnh trong một lệnh gọi duy nhất. Hỗ trợ dạng object bọc `{"requests": [...]}` hoặc mảng JSON trực tiếp `[...]`.

**Curl Example:**
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/images/generations/batch \
  -H "Content-Type: application/json" \
  -d '{
    "requests": [
      {
        "prompt": "Cyberpunk detective in rainy Tokyo street, neon reflections",
        "aspect_ratio": "16:9",
        "model": "pro"
      },
      {
        "prompt": "Portrait of female android with glowing amber eyes",
        "aspect_ratio": "9:16",
        "model": "banana2"
      }
    ]
  }'
```

**Response (202 Accepted):**
Trả về `JobsResponse` chứa toàn bộ các job được queue thành công trong DB.

---

### 3.3. Sinh Video (Gemini Omni Flash)

#### `POST /v1/videos/generations` (Đơn lẻ)
Hỗ trợ cả 3 chế độ video độc quyền qua Gemini Omni Flash:

| Chế độ | Wire Model Key | Đầu vào yêu cầu trong `input_images` |
|---|---|---|
| **First Frame (I2V)** | `abra_i2v_<duration>s` | 1 ảnh Base64 với `role="start_frame"` |
| **Start + End Frame** | `omni_flash_i2v_<duration>s_first_last` | 2 ảnh Base64: `role="start_frame"` và `role="end_frame"` |
| **Reference-to-Video (R2V)** | `abra_r2v_<duration>s` | 1–7 ảnh Base64 với `role="reference"` |

- `duration_seconds`: `4`, `6`, `8`, hoặc `10` (mặc định 8s).
- `aspect_ratio`: `"9:16"` (Portrait, mặc định) hoặc `"16:9"` (Landscape).

**Curl Example (First Frame):**
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "0-3s: The girl looks up smiling. 3-6s: She raises her hand to shield her eyes from sunlight.",
    "duration_seconds": 6,
    "aspect_ratio": "9:16",
    "model": "omni_flash",
    "input_images": [
      {
        "image_base64": "<BASE64_STRING>",
        "mime_type": "image/jpeg",
        "role": "start_frame"
      }
    ]
  }'
```

**Curl Example (Start + End Frame):**
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Smooth camera transition from samurai meditating to samurai standing under cherry blossom",
    "duration_seconds": 4,
    "aspect_ratio": "16:9",
    "model": "omni_flash",
    "generation_type": "start_end",
    "input_images": [
      {"image_base64": "<START_BASE64>", "role": "start_frame"},
      {"image_base64": "<END_BASE64>", "role": "end_frame"}
    ]
  }'
```

---

#### `POST /v1/videos/generations/batch` (Theo lô)
Gửi nhiều tác vụ sinh video Gemini Omni Flash đồng thời.

**Curl Example:**
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/videos/generations/batch \
  -H "Content-Type: application/json" \
  -d '{
    "requests": [
      {
        "prompt": "0-4s: Hovercar zooms down neon highway in high speed",
        "duration_seconds": 4,
        "aspect_ratio": "16:9",
        "model": "omni_flash",
        "input_images": [{"image_base64": "<BASE64_1>", "role": "start_frame"}]
      },
      {
        "prompt": "0-4s: Camera tracks tiger walking through misty forest",
        "duration_seconds": 4,
        "aspect_ratio": "9:16",
        "model": "omni_flash",
        "input_images": [{"image_base64": "<BASE64_2>", "role": "start_frame"}]
      }
    ]
  }'
```

---

### 3.4. Tra Cứu Trạng Thái Job (Jobs & Polling)

#### `POST /v1/jobs/status`
Truy vấn trạng thái của nhiều job cùng lúc (khuyên dùng khi xử lý batch).

**Request Body:**
```json
{
  "job_ids": ["job_a1b2c3d4e5f60718", "job_9876543210fedcba"]
}
```

**Response (200 OK):**
```json
{
  "jobs": [
    {
      "id": "job_a1b2c3d4e5f60718",
      "status": "complete",
      "type": "video",
      "generation_type": "image_to_video",
      "media": [
        {
          "id": "media_f914b1a4",
          "type": "video",
          "url": "https://storage.googleapis.com/...signed_url...",
          "duration_seconds": 8
        }
      ],
      "error": null
    },
    {
      "id": "job_9876543210fedcba",
      "status": "running",
      "type": "video",
      "generation_type": "image_to_video",
      "media": [],
      "error": null
    }
  ],
  "metadata": {
    "counts": {"queued": 0, "running": 1, "complete": 1, "failed": 0},
    "done": false,
    "poll_after_seconds": 10
  }
}
```

#### `GET /v1/jobs/{job_id}`
Truy vấn trạng thái của 1 job đơn lẻ.

#### `GET /v1/jobs/{job_id}/executions`
Xem toàn bộ lịch sử audit thực thi của job: profile Google Flow nào đã xử lý, thời gian bắt đầu, kết thúc, mã lỗi (nếu có).

---

### 3.5. Nhân Vật & Thực Thể (Characters)

#### `POST /v1/characters`
Tạo mới nhân vật kèm ảnh tham chiếu dạng Base64.
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/characters \
  -H "Content-Type: application/json" \
  -d '{
    "name": "General Victor",
    "description": "Veteran commander with scarred silver armor",
    "image_prompt": "Portrait of veteran commander, ornate silver armor",
    "entity_type": "character",
    "input_images": [
      {
        "image_base64": "<BASE64_STRING>",
        "mime_type": "image/jpeg"
      }
    ]
  }'
```

#### `GET /v1/characters`
Liệt kê toàn bộ danh sách nhân vật và ảnh tham chiếu hiện có.

#### `GET /v1/characters/{character_id}`
Lấy chi tiết thông tin của 1 nhân vật.

#### `PATCH /v1/characters/{character_id}`
Cập nhật thuộc tính nhân vật (`name`, `description`, `image_prompt`, `voice_description`).

#### `DELETE /v1/characters/{character_id}`
Xóa nhân vật khỏi cơ sở dữ liệu (`204 No Content`).

#### `POST /v1/characters/{character_id}/images/generations`
Tự động dùng ảnh đại diện của nhân vật làm ảnh tham chiếu Base64 để sinh ảnh mới theo prompt riêng.

#### `POST /v1/characters/{character_id}/videos/generations`
Tự động dùng ảnh đại diện của nhân vật làm khung hình đầu để sinh video Omni Flash.

---

### 3.6. Phong Cách Mỹ Thuật (Visual Styles / Materials)

#### `GET /v1/materials`
Lấy danh sách tất cả các style mỹ thuật có sẵn (`realistic`, `3d_pixar`, `anime`, `cinematic`...).

#### `GET /v1/materials/{material_id}`
Lấy chi tiết prompt mẫu, hướng dẫn lighting, và từ khóa phủ định (negative prompt) của 1 style.

---

### 3.7. Hậu Kỳ & Ghép Nối Video (Post-Processing)

#### `POST /v1/videos/concat`
Ghép nối nhiều clip video con (sinh từ Gemini Omni Flash) thành một file video MP4 hoàn chỉnh bằng ffmpeg.
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/videos/concat \
  -H "Content-Type: application/json" \
  -d '{
    "video_urls": [
      "https://storage.googleapis.com/...clip1.mp4",
      "https://storage.googleapis.com/...clip2.mp4"
    ]
  }'
```
**Response (200 OK):**
```json
{
  "status": "success",
  "video_url": "https://apikit.shopcongngheso5.io.vn/v1/videos/download/final_concat_12345.mp4",
  "duration_seconds": 16.5
}
```

#### `GET /v1/videos/download/{filename}`
Tải trực tiếp video MP4 thành phẩm về máy.

---

## 4. Bảng Mã Lỗi & HTTP Status

| Mã HTTP | Tên | Ý nghĩa & Hành động khắc phục |
|---|---|---|
| **200 OK** | Success | Tác vụ truy vấn thành công. |
| **201 Created** | Created | Tạo mới nhân vật/tài nguyên thành công. |
| **202 Accepted** | Queued | Yêu cầu sinh ảnh/video hợp lệ và đã đưa vào SQLite queue. |
| **204 No Content** | Deleted | Xóa tài nguyên thành công. |
| **404 Not Found** | Not Found | Không tìm thấy `job_id`, `character_id`, hoặc file âm thanh. |
| **422 Unprocessable** | Validation Error | Sai định dạng body (ví dụ mảng batch rỗng, thiếu prompt, hoặc cố tình gửi direct `media_id` thay vì Base64). |
| **503 Unavailable** | Maintenance Mode | Hệ thống đang bật cờ `CLIENT_MAINTENANCE`. Client cần tạm dừng gọi và thử lại sau `Retry-After: 60` giây. |

---

## 5. Ví Dụ Sử Dụng Qua Python SDK (`flowkit_client.py`)

```python
from flowkit_client import FlowKitClient

client = FlowKitClient(base_url="https://apikit.shopcongngheso5.io.vn")

# 1. Kiểm tra sức khỏe hệ thống
print(client.v1_health())

# 2. Sinh nhiều ảnh cùng lúc bằng Batch API
batch_img_resp = client.v1_generate_images_batch([
    {"prompt": "A futuristic city under purple sunset", "aspect_ratio": "16:9"},
    {"prompt": "A close-up portrait of cybernetic warrior", "aspect_ratio": "9:16"}
])
job_ids = [j["id"] for j in batch_img_resp["jobs"]]
print("Queued jobs:", job_ids)

# 3. Chờ toàn bộ lô hoàn thành (Polling)
result = client.v1_poll_jobs(job_ids, interval=10.0)
for job in result["jobs"]:
    print(f"Job {job['id']}: {job['status']} -> {job['media'][0]['url']}")
```
