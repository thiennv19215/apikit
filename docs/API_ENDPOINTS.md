# FlowKit — Toàn Bộ Danh Mục Endpoint (API Reference)

Tài liệu tổng hợp toàn bộ các endpoint của FlowKit Server, phân định rõ ràng giữa **Client Backend** (hợp đồng 1:1 với FlowProviderAPI) và **Agent / CLI Workflows** (điều phối kịch bản, scene chaining, video concat).

- **Production URL**: `https://apikit.shopcongngheso5.io.vn`
- **Local Dev URL**: `http://127.0.0.1:8100`

---

## MỤC LỤC
1. [Nhóm 1: Client Backend API (/v1 & Health)](#nhóm-1-client-backend-api-v1--health)
   - [Kiểm tra Sức khỏe & Readiness Probe](#11-kiểm-tra-sức-khỏe--readiness-probe)
   - [Upload Media & Base64](#12-upload-media--base64)
   - [Sinh Hình Ảnh & Video](#13-sinh-hình-ảnh--video)
   - [Tra cứu trạng thái Tác vụ (Jobs)](#14-tra-cứu-trạng-thái-tác-vụ-jobs)
   - [Quản lý Nhân vật (Characters)](#15-quản-lý-nhân-vật-characters)
2. [Nhóm 2: Agent / CLI Workflows (/api)](#nhóm-2-agent--cli-workflows-api)
   - [Dự án (Projects)](#21-dự-án-projects)
   - [Tập phim (Videos)](#22-tập-phim-videos)
   - [Phân cảnh (Scenes)](#23-phân-cảnh-scenes)
   - [Hàng đợi & Batch Requests](#24-hàng-đợi--batch-requests)
   - [Direct Flow Proxy](#25-direct-flow-proxy)
   - [Lồng tiếng & TTS](#26-lồng-tiếng--tts)
   - [Review Chất Lượng Video](#27-review-chất-lượng-video)
   - [Materials, Models & Providers](#28-materials-models--providers)
3. [Nhóm 3: Extension Gateway & WebSockets](#nhóm-3-extension-gateway--websockets)

---

## NHÓM 1: CLIENT BACKEND API (/v1 & Health)
*Được thiết kế chuẩn 1:1 theo hợp đồng của `FlowProviderAPI` và MCP tool. Không cần truyền `project_id`, gửi trực tiếp ảnh Base64, tự động cân bằng tải và chọn profile tài khoản. Xem tài liệu hướng dẫn tích hợp chi tiết tại [CLIENT_V1.md](CLIENT_V1.md), cơ chế audit log tại [EXECUTION_LOGS.md](EXECUTION_LOGS.md), và bảo trì hệ thống tại [CLIENT_MAINTENANCE_INTERNAL.md](CLIENT_MAINTENANCE_INTERNAL.md).*

### 1.1. Kiểm tra Sức khỏe & Readiness Probe
Dùng cho load balancer, Docker, Kubernetes hoặc client backend kiểm tra trước khi dispatch tác vụ.

| Method | Endpoint | Mô tả | Response Code & Mẫu dữ liệu |
|---|---|---|---|
| `GET` | `/v1/health` | Client health check (kiểm tra readiness DB, extension capabilities, maintenance gate) | `200 OK` (hoặc `503 Service Unavailable`)<br>`{"status": "degraded", "maintenance": false, "accepting_requests": true, "capabilities": {...}}` |
| `GET` | `/health/live` | Liveness check cơ bản | `200 OK`<br>`{"status": "ok"}` |
| `GET` | `/health/ready` | Kiểm tra Extension sẵn sàng & tải của worker queue | `200 OK` (hoặc `503` nếu chưa có extension)<br>`{"status": "ready", "provider_accounts": 1, "video_lite_ready_accounts": 1, "jobs": {"queued": 0, "running": 0}, "active_jobs": 0, "job_queue_capacity": 200, "job_queue_remaining": 200}` |
| `GET` | `/api/health` | Alias health cho extension & MCP | `200 OK`<br>`{"ok": true, "status": "ready", ...}` |
| `GET` | `/health` | Endpoint health gốc của FlowKit | `200 OK`<br>`{"status": "ok", "version": "0.2.0", "extension_connected": true, "ws": {...}}` |

---

### 1.2. Upload Media & Base64
| Method | Endpoint | Mô tả & Header |
|---|---|---|
| `POST` | `/v1/media` | Upload Base64 ảnh lên Flow, trả về Google `media_id`.<br>Headers trả về: `X-Flow-Project-Id`, `X-Flow-Media-Cache-Hits` |

**Request Body:**
```json
{
  "image_base64": "iVBORw0KGgoAAAANSUhEUgAA...",
  "mime_type": "image/jpeg",
  "file_name": "portrait.jpg"
}
```
**Response Body (200 OK):**
```json
{
  "media_id": "9e5449d8-0555-4b6a-af84-e2cf7d8ed5d6",
  "file_name": "portrait.jpg",
  "media": {
    "name": "9e5449d8-0555-4b6a-af84-e2cf7d8ed5d6",
    "projectId": "ba107792-8075-4b62-afc1-f3e4a896fde8"
  }
}
```

---

### 1.3. Sinh Hình Ảnh & Video
Các endpoint này nhận yêu cầu bất đồng bộ (Asynchronous), trả về HTTP **202 Accepted** ngay lập tức kèm theo `JobsResponse`. Worker background tự động upload ảnh Base64 lên profile tài khoản được phân bổ và sinh tác vụ.

#### 1.3.1. Sinh Ảnh: `POST /v1/images/generations`
**Request Body:**
```json
{
  "prompt": "A cybernetic samurai standing under cherry blossoms in neon rain",
  "aspect_ratio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
  "model": "pro",
  "variant_count": 1,
  "input_images": [
    {
      "image_base64": "iVBORw0KGgoAAA...",
      "mime_type": "image/png"
    }
  ]
}
```
*Ghi chú `aspect_ratio` cho Ảnh:*
- **Chuẩn FlowKit Model:** `"IMAGE_ASPECT_RATIO_LANDSCAPE"` (Mặc định), `"IMAGE_ASPECT_RATIO_PORTRAIT"`, `"IMAGE_ASPECT_RATIO_SQUARE"`, `"IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE"`, `"IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE"`.
- **Hỗ trợ Alias:** `"LANDSCAPE"`, `"PORTRAIT"`, `"SQUARE"`, `"HORIZONTAL"`, `"VERTICAL"`, `"16:9"`, `"9:16"`, `"1:1"`, `"3:4"`, `"4:3"`.

#### 1.3.2. Sinh Video: `POST /v1/videos/generations` (Omni Flash Mode)
Hệ thống sử dụng **Gemini Omni Flash** độc quyền trên Google Flow (tuyệt đối không dùng Veo), hỗ trợ cả 3 chế độ qua Batch RPC:

> [!IMPORTANT]
> **Quy định dữ liệu ảnh trên Client V1:** API v1 **chỉ chấp nhận ảnh dạng Base64 (`image_base64`)** trong mảng `input_images`. Khách hàng gọi API v1 **không được phép truyền `media_id`** (UUID nội bộ của Google Flow). Nếu truyền direct media IDs, API sẽ trả về lỗi HTTP 422. Server sẽ tự động upload Base64 và map UUID backend.

**1. First Frame (Image-to-Video - RPC `eb1hJf`):**
```json
{
  "prompt": "Samurai draws katana with lightning aura, camera zooms in",
  "model": "omni_flash",
  "generation_type": "image_to_video",
  "aspect_ratio": "9:16",
  "duration_seconds": 4,
  "input_images": [
    {
      "image_base64": "iVBORw0KGgoAAA...",
      "mime_type": "image/jpeg",
      "role": "start_frame"
    }
  ]
}
```

**2. Start + End Frame (First & Last Frame - RPC `nprQif`):**
```json
{
  "prompt": "Smooth cinematic camera transition from girl in white dress to white tiger in bamboo forest",
  "model": "omni_flash",
  "generation_type": "start_end",
  "aspect_ratio": "9:16",
  "duration_seconds": 4,
  "input_images": [
    {
      "image_base64": "iVBORw0KGgoAAA...",
      "mime_type": "image/jpeg",
      "role": "start_frame"
    },
    {
      "image_base64": "iVBORw0KGgoAAA...",
      "mime_type": "image/jpeg",
      "role": "end_frame"
    }
  ]
}
```

**3. Reference-to-Video (R2V - RPC `MZZa6b`):**
```json
{
  "prompt": "White tiger walking peacefully beside the girl in ancient bamboo forest",
  "model": "omni_flash",
  "generation_type": "reference_to_video",
  "aspect_ratio": "9:16",
  "duration_seconds": 4,
  "input_images": [
    {
      "image_base64": "iVBORw0KGgoAAA...",
      "role": "reference"
    },
    {
      "image_base64": "iVBORw0KGgoAAA...",
      "role": "reference"
    }
  ]
}
```

*Ghi chú `aspect_ratio` cho Video:*
- **Chuẩn FlowKit Model:**
  - `"VIDEO_ASPECT_RATIO_LANDSCAPE"` (Ngang / 16:9 - Mặc định)
  - `"VIDEO_ASPECT_RATIO_PORTRAIT"` (Dọc / 9:16)
- **Hỗ trợ Alias:** `"LANDSCAPE"`, `"PORTRAIT"`, `"HORIZONTAL"`, `"VERTICAL"`, `"16:9"`, `"9:16"`.

*Cơ chế Base64 Auto-Cache:*
- Client luôn gửi ảnh `image_base64`.
- Backend tự động tính SHA-256 hash và tra cứu trong cache `(image_hash, project_id)`.
- Nếu ảnh đã tải lên project đó rồi, backend tái sử dụng `media_id` ngay lập tức (0ms, 0 tốn captcha upload).
- Tránh trùng lặp hoặc xung đột giữa các Google Account / Chrome Profile khác nhau.

**Response Body chuẩn (202 Accepted):**
```json
{
  "jobs": [
    {
      "id": "job_e10ae811a8284b65",
      "project_id": null,
      "provider": "google_flow",
      "type": "video",
      "generation_type": "image_to_video",
      "status": "queued",
      "media": [],
      "error": null
    }
  ],
  "metadata": {
    "counts": { "queued": 1, "running": 0, "complete": 0, "failed": 0 },
    "done": false,
    "poll_after_seconds": 10
  },
  "job_id": "job_e10ae811a8284b65",
  "status": "queued"
}
```

---

### 1.4. Tra cứu trạng thái Tác vụ (Jobs)
| Method | Endpoint | Tham số |
|---|---|---|
| `POST` | `/v1/jobs/status` | Body nhận danh sách `{"job_ids": ["job_1", "job_2"]}` hoặc đơn lẻ `{"job_id": "job_1"}` |
| `GET` | `/v1/jobs/{job_id}` | Path param `job_id` |
| `GET` | `/v1/jobs/status/{job_id}` | Alias path param |
| `GET` | `/v1/jobs/{job_id}/executions` | Lịch sử thực thi (audit log, routing profile, thời gian bắt đầu/kết thúc) |

**Kết quả khi hoàn thành (`status == "complete"`):**
```json
{
  "jobs": [
    {
      "id": "job_e10ae811a8284b65",
      "status": "complete",
      "type": "video",
      "generation_type": "image_to_video",
      "media": [
        {
          "id": "media_f914b1a4",
          "type": "video",
          "url": "https://storage.googleapis.com/...signed_url...",
          "thumbnail_url": null,
          "duration_seconds": 8
        }
      ],
      "error": null
    }
  ],
  "metadata": {
    "counts": { "queued": 0, "running": 0, "complete": 1, "failed": 0 },
    "done": true,
    "poll_after_seconds": null
  },
  "job_id": "job_e10ae811a8284b65",
  "status": "complete"
}
```

---

### 1.5. Quản lý Nhân vật (Characters)
| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/v1/characters` | Tạo nhân vật mới (chấp nhận ảnh Base64 `input_images` hoặc `reference_media_ids`) |
| `GET` | `/v1/characters` | Lấy danh sách toàn bộ nhân vật trong catalog |
| `GET` | `/v1/characters/{id}` | Lấy chi tiết thông tin một nhân vật |
| `PATCH` | `/v1/characters/{id}` | Cập nhật tên, mô tả, image_prompt, model của nhân vật |
| `DELETE` | `/v1/characters/{id}` | Xóa nhân vật khỏi catalog |
| `GET` | `/v1/characters/{id}/reference-images/{index}` | Trả về HTTP 307 Redirect tới URL ảnh mẫu của nhân vật |
| `POST` | `/v1/characters/{id}/images/generations` | Sinh ảnh nhân vật (Alias: `/images`) |
| `POST` | `/v1/characters/{id}/videos/generations` | Sinh video nhân vật (Alias: `/videos`) |

---

## NHÓM 2: AGENT / CLI WORKFLOWS (/api)
*Dành cho quy trình biên kịch và chỉ đạo nghệ thuật tự động của Agent CLI (kịch bản nhiều cảnh, camera transition, TTS auto-fit, review đánh giá chất lượng).*

### 2.1. Dự án (Projects)
- `POST /api/projects`: Tạo dự án mới kèm danh sách thực thể (characters, locations) và phong cách hình ảnh (`material`).
- `GET /api/projects`: Liệt kê tất cả dự án.
- `GET /api/projects/{id}`: Chi tiết dự án, bao gồm danh sách video và entities.
- `PATCH /api/projects/{id}`: Sửa story, title, material, metadata.
- `DELETE /api/projects/{id}`: Xóa dự án và các dữ liệu liên quan.

### 2.2. Tập phim (Videos)
- `POST /api/videos`: Tạo một video/tập phim mới thuộc project (`project_id`, `aspect_ratio`: VERTICAL / HORIZONTAL).
- `GET /api/videos/{id}`: Lấy chi tiết video và toàn bộ danh sách scene bên trong.
- `POST /api/videos/{id}/concat`: Ghép (concatenate) toàn bộ video scene bằng ffmpeg.
- `POST /api/videos/{id}/narrate`: Tự động sinh giọng đọc TTS cho toàn bộ scene trong video.

### 2.3. Phân cảnh (Scenes)
- `POST /api/scenes`: Tạo scene mới (`video_id`, `display_order`, `prompt`, `video_prompt`, `narrator_text`, `character_names`, `chain_type`).
- `GET /api/scenes/{id}`: Chi tiết phân cảnh, URL ảnh start/end, URL video scene.
- `PATCH /api/scenes/{id}`: Cập nhật prompt, video_prompt, narrator_text mà không cần xóa tạo lại.
- `DELETE /api/scenes/{id}`: Xóa phân cảnh.

### 2.4. Hàng đợi & Batch Requests
- `POST /api/requests/batch`: Nộp đồng loạt N requests (hệ thống tự động throttle max 5 concurrent và cooldown 10s).
- `GET /api/requests/batch-status`: Polling trạng thái tổng hợp của video/project:
  `GET /api/requests/batch-status?video_id=<VID>&type=GENERATE_IMAGE`
  *(Trả về `total`, `pending`, `processing`, `completed`, `failed`, `done: true/false`)*.
- `GET /api/requests`: Danh sách yêu cầu chi tiết trong queue.
- `GET /api/requests/{id}`: Chi tiết một yêu cầu.

### 2.5. Direct Flow Proxy
- `POST /api/flow/upload-image`: Upload ảnh trực tiếp.
- `POST /api/flow/generate-image`: Gọi sinh ảnh trực tiếp không qua queue.
- `POST /api/flow/generate-video`: Gọi sinh video trực tiếp không qua queue.
- `GET /api/flow/video-status`: Tra cứu trạng thái video từ Google Flow RPC.

### 2.6. Lồng tiếng & TTS
- `POST /api/tts/generate`: Sinh file âm thanh WAV từ văn bản lồng tiếng (`narrator_text`).
- `POST /api/tts/template`: Tạo mẫu giọng đọc mới.

### 2.7. Review Chất Lượng Video
- `POST /api/videos/{id}/review`: Đánh giá video AI bằng Claude Vision (`mode=light` hoặc `mode=full`).
  Tự động chấm điểm (0 - 10) và gợi ý cập nhật `video_prompt` nếu điểm < 7.5.

### 2.8. Materials, Models & Providers
- `GET /api/materials`: Danh sách phong cách mỹ thuật hỗ trợ (`realistic`, `3d_pixar`, `anime`, `cyberpunk`...).
- `POST /api/materials`: Đăng ký custom material mới.
- `GET /models`: Danh sách các model AI đang hỗ trợ (Image, Video Veo 3.1, TTS).
- `GET /providers`: Danh sách provider backend.
- `GET /active-project`: Lấy ID project Flow đang hoạt động trên Extension.

---

## NHÓM 3: EXTENSION GATEWAY & WEBSOCKETS
*Các kênh kết nối thời gian thực dành cho tiện ích mở rộng Chrome Extension.*

| Protocol | Endpoint | Mục đích |
|---|---|---|
| `WS` | `/ws` | Cổng WebSocket chính cho Chrome Extension FlowKit |
| `WS` | `/api/extensions/ws` | Cổng WebSocket tương thích với Extension từ bản FlowProviderAPI |
| `WS` | `/ws/dashboard` | WebSocket push sự kiện realtime về Side Panel UI của Extension |
| `POST` | `/api/ext/callback` | HTTP callback dự phòng khi mạng chập chờn, extension POST kết quả hoàn thành về |
