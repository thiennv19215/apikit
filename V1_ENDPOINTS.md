# FlowKit Client API v1 — Tài Liệu Toàn Bộ Endpoint

Tài liệu tham chiếu chuẩn xác và toàn diện nhất cho toàn bộ các endpoint thuộc phân hệ **Client API v1** (`/v1/...`) của FlowKit.

- **Production Base URL:** `https://apikit.shopcongngheso5.io.vn`
- **Local Dev Base URL:** `http://127.0.0.1:8100`

---

## 1. Nguyên Tắc Cốt Lõi Khi Tích Hợp Client V1

1. **Đồng Bộ Cho Ảnh & Request-Poll Pattern Cho Video (Bỏ Cờ `wait`):**
   - **Sinh Ảnh (`/v1/images/generations`, `/v1/images/edits`, `/v1/images/generations/batch`):** Luôn luôn **Đồng Bộ Trực Tiếp (Direct Sync 200 OK)**. Trả về ngay `HTTP 200 OK` kèm mảng `media` chứa ảnh trong ~3–5 giây. Không cần xếp hàng chờ đợi, không cần polling.
   - **Sinh Video (`/v1/videos/generations`, `/v1/videos/generations/batch`):** Chuẩn **Asynchronous Job / Request-Poll Pattern**. Do video render từ 30–90 giây, server gửi lệnh tới Google Flow và trả về ngay **`HTTP 200 OK`** chứa mã `job_id`, `status="running"`, `phase="polling"`, kèm response headers `Location: /v1/jobs/{job_id}` và `Retry-After: 10`.
   - **Polling Duy Nhất Qua GET:** Backend Client thực hiện polling kết quả video độc quyền qua **`GET /v1/jobs/{job_id}`** (hoặc `GET /v1/jobs/status/{job_id}`).
2. **Hỗ trợ linh hoạt cả Direct URL (`image_url`) và Base64 (`image_base64`):**
   - Phía Client V1 có thể truyền trực tiếp URL ảnh (từ CDN, AWS S3, Cloudflare R2, web) qua trường `image_url` (hoặc `base_image_url` cho Image Edit, `image_url` cho Upscale) mà không cần tải về máy để encode Base64.
   - Hoặc vẫn có thể tiếp tục truyền `image_base64` như bình thường.
   - Tuyệt đối **không truyền direct `media_id`** của Google Flow từ phía client nếu không có ảnh gốc.
3. **Cơ chế Auto-Cache SHA-256:**
   - Server tự động băm mã SHA-256 nội dung ảnh để tra cứu cache. Nếu ảnh đã tải lên trước đó, server tái sử dụng media UUID ngay lập tức (0ms trễ, 0 tốn captcha).
4. **Mô hình Video độc quyền Gemini Omni Flash:**
   - Hệ thống Client V1 **sử dụng Gemini Omni Flash** (`model="omni_flash"`), hỗ trợ đủ cả 4 chế độ tạo video: *Text-to-Video (T2V)*, *First Frame (I2V)*, *Start + End Frame*, và *Reference-to-Video (R2V)*.
5. **Hỗ trợ Batch linh hoạt:**
   - Cả sinh ảnh và sinh video đều có endpoint Batch riêng (`/v1/images/generations/batch` và `/v1/videos/generations/batch`), chấp nhận cả dạng object `{"requests": [...]}` lẫn mảng trực tiếp `[...]`.

---

## 2. Bảng Tra Cứu Toàn Bộ Endpoint v1 (Quick Reference)

| Nhóm | Method | Endpoint | Mô tả | Trạng thái HTTP |
|---|---|---|---|---|
| **System** | `GET` | `/v1/health` | Kiểm tra độ sẵn sàng & tính năng hệ thống | 200 OK / 503 Maintenance |
| **Ảnh (Image)** | `POST` | `/v1/images/generations` | Sinh ảnh trực tiếp từ văn bản / URL / Base64 (1-4 ảnh, Sync) | 200 OK |
| | `POST` | `/v1/images/generations/batch` | Sinh nhiều ảnh theo lô (Batch, Sync) | 200 OK |
| | `POST` | `/v1/images/edits` | Chỉnh sửa ảnh gốc từ prompt / URL / Base64 (Sync) | 200 OK |
| | `POST` | `/v1/images/upscale` | Phóng to ảnh 2K/4K từ URL / Base64 (JSON Base64 hoặc nhị phân) | 200 OK |
| | `POST` | `/v1/images/export` | Alias phóng to ảnh trả trực tiếp file JPEG nhị phân | 200 OK |
| **Video** | `POST` | `/v1/videos/generations` | Sinh 1 video Omni Flash (T2V/I2V/Start+End/R2V, 360p/720p). Trả `job_id` cho polling | 200 OK (Job Polling) |
| | `POST` | `/v1/videos/generations/batch` | Sinh nhiều video Omni Flash theo lô (Batch). Trả danh sách `job_id` cho polling | 200 OK (Job Polling) |
| **Jobs / Polling** | `GET` | `/v1/jobs/{job_id}` | Polling tra cứu trạng thái và link media khi hoàn tất (Chuẩn GET) | 200 OK |
| | `GET` | `/v1/jobs/status/{job_id}` | Alias tra cứu trạng thái job qua GET | 200 OK |
| | `POST` | `/v1/jobs/status` | Tra cứu trạng thái theo mảng job_ids | 200 OK |
| | `GET` | `/v1/jobs/{job_id}/executions` | Tra cứu lịch sử audit các lần gọi RPC Flow của job | 200 OK |
| | `POST` | `/v1/videos/generations/batch` | Sinh nhiều video Omni Flash theo lô (Batch) đồng bộ | 200 OK |
| **Jobs (Tra cứu)** | `POST` | `/v1/jobs/status` | Tra cứu trạng thái/kết quả nhiều job theo danh sách `job_ids` | 200 OK |
| | `GET` | `/v1/jobs/{job_id}` | Tra cứu trạng thái/kết quả 1 job cụ thể | 200 OK / 404 |
| | `GET` | `/v1/jobs/status/{job_id}` | Alias tra cứu trạng thái 1 job | 200 OK / 404 |
| | `GET` | `/v1/jobs/{job_id}/executions` | Lịch sử audit thực thi (thời gian, profile) | 200 OK / 404 |
| **Nhân vật** | `GET` | `/v1/characters` | Danh sách nhân vật / thực thể | 200 OK |
| | `POST` | `/v1/characters` | Tạo mới nhân vật kèm ảnh tham chiếu | 201 Created |
| | `GET` | `/v1/characters/{id}` | Lấy chi tiết một nhân vật | 200 OK / 404 |
| | `PATCH` | `/v1/characters/{id}` | Cập nhật thông tin nhân vật | 200 OK / 404 |
| | `DELETE` | `/v1/characters/{id}` | Xóa nhân vật | 204 No Content |
| | `POST` | `/v1/characters/{id}/images/generations` | Sinh ảnh cho nhân vật bằng prompt riêng | 200 OK |
| | `POST` | `/v1/characters/{id}/videos/generations` | Sinh video cho nhân vật | 200 OK |
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

#### `POST /v1/images/generations` (Đơn lẻ - Đồng bộ)
Tạo tác vụ sinh ảnh bằng Banana Pro / Banana 2. Server xử lý trực tiếp và trả về ngay kết quả HTTP 200 OK.

**Request Body:**
- `prompt` (string, bắt buộc): Mô tả hình ảnh cần tạo.
- `count` (int, tuỳ chọn, 1-4, mặc định 1): Số lượng ảnh sinh ra (1 đến 4 ảnh). Alias: `variant_count`.
- `aspect_ratio` (string, tuỳ chọn): Tỉ lệ ảnh (`"IMAGE_ASPECT_RATIO_LANDSCAPE"`, `"IMAGE_ASPECT_RATIO_PORTRAIT"`, `"IMAGE_ASPECT_RATIO_SQUARE"`, hoặc alias `"16:9"`, `"9:16"`, `"1:1"`). Mặc định `16:9`.
- `model` (string, tuỳ chọn): `"NANO_BANANA_PRO"` (alias `"pro"`), `"NANO_BANANA_2"` (alias `"banana2"`), hoặc `"NANO_BANANA_2_LITE"` (alias `"banana2_lite"`, `"harbor_seal"`, `"lite"`).
- `input_images` (array, tuỳ chọn): Mảng các ảnh tham chiếu dạng Direct URL hoặc Base64:
  - `image_url` (string, tuỳ chọn): Link ảnh trực tiếp từ CDN, S3, R2, web.
  - `image_base64` (string, tuỳ chọn): Chuỗi Base64 của ảnh.
  - `mime_type` (string, tuỳ chọn): Mặc định `image/jpeg`.

**Curl Example (Text-to-Image hoặc Reference URL):**
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A cybernetic samurai standing in neon rain, hyper-detailed, 8k",
    "aspect_ratio": "16:9",
    "model": "pro",
    "count": 1,
    "input_images": [
      {
        "image_url": "https://cdn.example.com/character-face.jpg"
      }
    ]
  }'
```

**Response (200 OK):**
```json
{
  "jobs": [
    {
      "id": "job_a1b2c3d4e5f60718",
      "status": "complete",
      "type": "image",
      "generation_type": "image",
      "provider": "google_flow",
      "media": [
        {
          "id": "media_a1b2c3d4",
          "type": "image",
          "url": "https://storage.googleapis.com/...signed_url...",
          "media_id": "media_a1b2c3d4"
        }
      ],
      "error": null
    }
  ],
  "metadata": {
    "counts": {"queued": 0, "running": 0, "complete": 1, "failed": 0},
    "done": true,
    "poll_after_seconds": 10
  },
  "job_id": "job_a1b2c3d4e5f60718",
  "status": "complete",
  "media": [
    {
      "id": "media_a1b2c3d4",
      "type": "image",
      "url": "https://storage.googleapis.com/...signed_url...",
      "media_id": "media_a1b2c3d4"
    }
  ]
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

#### `POST /v1/images/edits` (Chỉnh sửa ảnh)
Chỉnh sửa ảnh gốc dựa trên prompt mô tả và tùy chọn ảnh tham chiếu phong cách.

**Request Body:**
- `prompt` (string, bắt buộc): Mô tả thay đổi cần thực hiện.
- `base_image_base64` (string, tuỳ chọn nếu đã có `base_media_id`): Chuỗi Base64 của ảnh gốc cần chỉnh sửa.
- `base_media_id` (string, tuỳ chọn): Google Flow media ID của ảnh gốc nếu đã có.
- `input_images` (array, tuỳ chọn): Danh sách ảnh tham chiếu bổ sung dạng Base64.
- `model` (string, tuỳ chọn): Model sinh ảnh (`"NANO_BANANA_2"`, `"NANO_BANANA_2_LITE"`, `"NANO_BANANA_PRO"`).
- `count` (int, tuỳ chọn, 1-4, mặc định 1): Số lượng biến thể.
- `aspect_ratio` (string, tuỳ chọn): Tỉ lệ ảnh (`"16:9"`, `"9:16"`, `"1:1"`).
- `seed` (int, tuỳ chọn): Seed số ngẫu nhiên.
- `installation_id` (string, tuỳ chọn): Định tuyến profile cụ thể trong hệ thống đa tài khoản.

**Curl Example:**
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/images/edits \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Change the background from sunny park to a futuristic cyber city in rain",
    "base_image_base64": "<BASE64_ORIGINAL_IMAGE>",
    "model": "banana2",
    "count": 1
  }'
```

**Response (202 Accepted):**
Trả về `JobsResponse` chứa job chỉnh sửa ảnh đang được xử lý trong hàng đợi.

---

#### `POST /v1/images/upscale` & `POST /v1/images/export` (Phóng to ảnh 2K/4K)
Phóng to nâng cao chất lượng ảnh lên 2K hoặc 4K.

**Request Body:**
- `image_base64` (string, tuỳ chọn): Chuỗi Base64 của ảnh cần phóng to (hệ thống tự động upload và tối ưu).
- `media_id` (string, tuỳ chọn): Media ID của ảnh đã có trên Google Flow.
- `quality` (string, tuỳ chọn): Mức phóng to (`"2K"` hoặc `"4K"`). Mặc định `"4K"`. Alias: `resolution`.
- `download` (boolean, tuỳ chọn, mặc định `false`): Nếu `true` (hoặc gọi qua `/v1/images/export`), trả về trực tiếp file nhị phân `image/jpeg` đính kèm thay vì JSON.
- `installation_id` (string, tuỳ chọn): Định tuyến tài khoản cụ thể.

**Curl Example (Trả JSON Base64):**
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/images/upscale \
  -H "Content-Type: application/json" \
  -d '{
    "image_base64": "<BASE64_IMAGE_DATA>",
    "quality": "4K"
  }'
```

**Curl Example (Tải trực tiếp file JPEG):**
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/images/export \
  -H "Content-Type: application/json" \
  -d '{
    "media_id": "media_abc123xyz",
    "quality": "4K"
  }' \
  --output upscaled_photo_4k.jpg
```

---

### 3.3. Sinh Video (Gemini Omni Flash)

#### `POST /v1/videos/generations` (Asynchronous Job / Request-Poll Pattern)
Hỗ trợ cả 4 chế độ video Omni Flash. Lệnh được đẩy thẳng tới Google Flow, không qua hàng đợi SQLite chờ 202. Server trả ngay `HTTP 200 OK` kèm `job_id` và response headers:
- `Location: /v1/jobs/{job_id}`
- `Retry-After: 10`

| Chế độ | Wire Model Key | Đầu vào yêu cầu trong `input_images` |
|---|---|---|
| **Text-to-Video (T2V)** | `generate_omni_flash_text_video` | Không cần ảnh (`input_images: []`), chỉ cần `prompt` |
| **First Frame (I2V)** | `abra_i2v_<duration>s[_360p]` | 1 ảnh URL/Base64 với `role="start_frame"` |
| **Start + End Frame** | `omni_flash_i2v_<duration>s_first_last[_360p]` | 2 ảnh URL/Base64: `role="start_frame"` và `role="end_frame"` |
| **Reference-to-Video (R2V)** | `abra_r2v_<duration>s[_360p]` | 1–7 ảnh URL/Base64 với `role="reference"` |

- `duration_seconds`: `4`, `6`, `8`, hoặc `10` (mặc định 8s).
- `resolution`: `"720p"` (mặc định) hoặc `"360p"`.
- `aspect_ratio`: `"9:16"` (Portrait, mặc định) hoặc `"16:9"` (Landscape).
- `type`: `"text_to_video"` (t2v), `"image_to_video"` (i2v), `"start_end"`, hoặc `"reference_to_video"` (r2v).
- `input_images`: Hỗ trợ cả `image_url` (URL ảnh trực tiếp) hoặc `image_base64`.

**1. Gửi yêu cầu sinh video (POST):**
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
        "image_url": "https://cdn.example.com/character-start.jpg",
        "role": "start_frame"
      }
    ]
  }'
```

**Response trả về ngay lập tức (200 OK):**
```json
{
  "jobs": [
    {
      "id": "job_v1234567890abcdef",
      "operation_id": "workflows/omni-video-1234",
      "status": "running",
      "phase": "polling",
      "type": "video",
      "generation_type": "image_to_video",
      "provider": "google_flow",
      "media": [],
      "error": null
    }
  ],
  "job_id": "job_v1234567890abcdef",
  "status": "running",
  "phase": "polling",
  "media": []
}
```

**2. Polling lấy kết quả (GET duy nhất — Response tối giản):**
Backend Client định kỳ gọi `GET /v1/jobs/{job_id}` (ví dụ mỗi 5–10 giây):
```bash
curl -X GET https://apikit.shopcongngheso5.io.vn/v1/jobs/job_v1234567890abcdef
```

**Response khi đang xử lý (200 OK — Tối giản, không bọc lồng rườm rà):**
```json
{
  "job_id": "job_v1234567890abcdef",
  "status": "running",
  "type": "video",
  "url": null,
  "media": [],
  "error": null
}
```

**Response khi video render hoàn tất (200 OK — Có sẵn direct `url`):**
```json
{
  "job_id": "job_v1234567890abcdef",
  "status": "complete",
  "type": "video",
  "url": "https://storage.googleapis.com/...signed_url.mp4",
  "media": [
    {
      "url": "https://storage.googleapis.com/...signed_url.mp4",
      "media_id": "media_v98765432"
    }
  ],
  "error": null
}
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
Endpoint polling tra cứu tiến độ và kết quả của 1 job đơn lẻ. Trả về response cấu trúc phẳng, tối giản (không lồng mảng `jobs` hay `metadata` rườm rà), có sẵn direct `url` khi hoàn tất:
```json
{
  "job_id": "job_12345",
  "status": "complete",
  "type": "video",
  "url": "https://storage.googleapis.com/...video.mp4",
  "media": [{"url": "https://storage.googleapis.com/...video.mp4", "media_id": "media_123"}],
  "error": null
}
```

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
