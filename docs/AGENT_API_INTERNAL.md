# FlowKit — Agent & CLI Internal API Reference (/api & /ws)

Tài liệu này dành riêng cho **FlowKit Agent, CLI tools và các kịch bản tự động hóa nội bộ** (`/api/*`, WebSocket `/ws`, skills `/fk-*`). 

> [!NOTE]
> **Phân định kiến trúc:**
> - Nếu bạn là client bên ngoài cần tích hợp sinh ảnh/video trực tiếp qua API v1, vui lòng tham khảo: **[Tài liệu Client API v1 (docs/CLIENT_V1.md)](CLIENT_V1.md)**.
> - Tài liệu này **CHỈ** dành cho Agent điều phối kịch bản phim, quản lý thực thể (characters, locations), phân cảnh (scenes), scene chaining, review thị giác (vision), TTS narration, và WebSocket Extension.

---

- **Local Dev Base URL**: `http://127.0.0.1:8100`
- **WebSocket URL**: `ws://127.0.0.1:8100/ws`

---

## MỤC LỤC

1. [Kiểm tra Sức khỏe Hệ thống (Health Check)](#1-kiểm-tra-sức-khỏe-hệ-thống-health-check)
2. [Quản lý Thực thể & Nhân vật (Characters / Entities)](#2-quản-lý-thực-thể--nhân-vật-characters--entities)
3. [Quản lý Dự án (Projects & Active Project)](#3-quản-lý-dự-án-projects--active-project)
4. [Quản lý Video & Phân cảnh (Videos & Scenes)](#4-quản-lý-video--phân-cảnh-videos--scenes)
5. [Hàng đợi & Batch Requests Pipeline](#5-hàng-đợi--batch-requests-pipeline)
6. [Direct Google Flow Proxy & Upload](#6-direct-google-flow-proxy--upload)
7. [Lồng tiếng & TTS Narration](#7-lồng-tiếng--tts-narration)
8. [Review & Đánh giá Chất lượng Video](#8-review--đánh-giá-chất-lượng-video)
9. [Materials (Phong cách Mỹ thuật)](#9-materials-phong-cách-mỹ-thuật)
10. [Models & Providers Cấu hình](#10-models--providers-cấu-hình)
11. [Âm nhạc (Music / Suno)](#11-âm-nhạc-music--suno)
12. [Extension Gateway & WebSockets](#12-extension-gateway--websockets)

---

## 1. Kiểm tra Sức khỏe Hệ thống (Health Check)

| Method | Endpoint | Mục đích | Phản hồi mẫu |
|---|---|---|---|
| `GET` | `/health` | Kiểm tra trạng thái server và kết nối Chrome Extension | `{"status": "ok", "version": "0.2.0", "extension_connected": true, "ws": {"connected": true}}` |
| `GET` | `/api/health` | Alias kiểm tra cho Extension và MCP tools | `{"ok": true, "status": "ready"}` |

---

## 2. Quản lý Thực thể & Nhân vật (Characters / Entities)

Dùng cho Agent xây dựng nhân vật nhất quán xuyên suốt các phân cảnh (Reference Image Consistency).

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/characters` | Tạo thực thể/nhân vật mới |
| `GET` | `/api/characters` | Danh sách tất cả thực thể trong database |
| `GET` | `/api/characters/{id}` | Chi tiết thông tin thực thể (`media_id`, prompt, voice description) |
| `PATCH` | `/api/characters/{id}` | Cập nhật tên, mô tả, `media_id`, `reference_image_url` |
| `DELETE` | `/api/characters/{id}` | Xóa thực thể |
| `POST` | `/api/projects/{pid}/characters/{cid}` | Gắn thực thể vào một dự án cụ thể |
| `GET` | `/api/projects/{pid}/characters` | Liệt kê danh sách thực thể thuộc dự án |

**Mẫu Request tạo Character (`POST /api/characters`):**
```json
{
  "name": "Luna",
  "entity_type": "character",
  "description": "Young woman with silver braided hair in futuristic pilot jumpsuit",
  "image_prompt": "Portrait of young woman, silver braided hair, wearing futuristic pilot jumpsuit",
  "voice_description": "Calm, warm, slightly husky female voice",
  "media_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

---

## 3. Quản lý Dự án (Projects & Active Project)

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/projects` | Tạo dự án kịch bản mới kèm `material` và danh sách entities |
| `GET` | `/api/projects` | Danh sách tất cả dự án |
| `GET` | `/api/projects/{id}` | Chi tiết dự án, bao gồm videos và entities |
| `PATCH` | `/api/projects/{id}` | Cập nhật `title`, `story`, `material`, `metadata` |
| `DELETE` | `/api/projects/{id}` | Xóa dự án |
| `GET` | `/api/active-project` | Lấy ID dự án đang hoạt động trong phiên làm việc |
| `POST` | `/api/active-project` | Thiết lập dự án đang hoạt động (`{"project_id": "..."}`) |

---

## 4. Quản lý Video & Phân cảnh (Videos & Scenes)

Mỗi dự án chứa một hoặc nhiều video (tập phim), mỗi video chứa chuỗi các scene nối tiếp nhau (Scene Chaining).

### 4.1. Video (Tập phim)
| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/videos` | Tạo video mới thuộc project (`project_id`, `aspect_ratio`: `VERTICAL` / `HORIZONTAL`) |
| `GET` | `/api/videos/{id}` | Lấy chi tiết video và toàn bộ danh sách scene |
| `DELETE` | `/api/videos/{id}` | Xóa video |
| `POST` | `/api/videos/{id}/narrate` | Tự động sinh toàn bộ audio TTS cho các scene trong video |

### 4.2. Phân cảnh (Scenes)
| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/scenes` | Tạo scene mới trong chuỗi video |
| `GET` | `/api/scenes/{id}` | Lấy thông tin scene (URL ảnh start/end, URL video scene, prompt) |
| `PATCH` | `/api/scenes/{id}` | Cập nhật `prompt`, `video_prompt`, `narrator_text`, `character_names` (mutable) |
| `DELETE` | `/api/scenes/{id}` | Xóa scene |

**Mẫu Request tạo Scene (`POST /api/scenes`):**
```json
{
  "video_id": "vid_123456",
  "display_order": 1,
  "prompt": "Luna steps into the cockpit and initiates pre-flight diagnostic sequence",
  "video_prompt": "0-3s: Luna walks to the console. 3-6s: She flips switches and monitors light up. 6-8s: Luna smiles confidently.",
  "narrator_text": "The engines hummed with raw, untamed energy as departure drew near.",
  "character_names": ["Luna"],
  "chain_type": "FIRST_FRAME"
}
```

---

## 5. Hàng đợi & Batch Requests Pipeline

Agent nộp các batch request đồng loạt qua API này; hệ thống tự động throttle (tối đa 5 concurrent, cooldown 10s giữa các đợt dispatch).

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/requests/batch` | Nộp danh sách request sinh ảnh / sinh video |
| `GET` | `/api/requests/batch-status` | Polling trạng thái tiến độ theo `project_id` hoặc `video_id` |
| `GET` | `/api/requests` | Xem toàn bộ hàng đợi |
| `GET` | `/api/requests/{id}` | Chi tiết một request |

**Polling Batch Status:**
```bash
curl -s "http://127.0.0.1:8100/api/requests/batch-status?video_id=<VID>&type=GENERATE_IMAGE"
```
**Response mẫu:**
```json
{
  "total": 12,
  "pending": 4,
  "processing": 2,
  "completed": 6,
  "failed": 0,
  "done": false,
  "all_succeeded": false
}
```

---

## 6. Direct Google Flow Proxy & Upload

Dành cho Agent khi cần thực hiện thao tác trực tiếp với phiên Google Flow thông qua Extension.

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/flow/upload-image` | Upload file ảnh local lên Google Flow, nhận về UUID `media_id` |
| `POST` | `/api/flow/generate-image` | Gửi lệnh sinh ảnh trực tiếp |
| `POST` | `/api/flow/generate-video` | Gửi lệnh sinh video trực tiếp |
| `POST` | `/api/flow/check-status` | Kiểm tra trạng thái tác vụ từ Google Flow RPC |

**Mẫu Upload ảnh (`POST /api/flow/upload-image`):**
```json
{
  "image_base64": "<base64_string>",
  "mime_type": "image/png",
  "project_id": "<FLOW_PROJECT_ID>",
  "file_name": "reference_luna.png"
}
```
*Hoặc upload qua đường dẫn local:*
```json
{
  "file_path": "C:/images/reference_luna.png"
}
```
*Trả về:* `{"media_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", "raw": {...}}`

---

## 7. Lồng tiếng & TTS Narration

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/tts/generate` | Sinh file âm thanh WAV cho một đoạn văn bản |
| `GET` | `/api/tts/templates` | Lấy danh sách voice templates (mẫu giọng đọc) |
| `POST` | `/api/tts/templates` | Tạo voice template mới |
| `POST` | `/api/videos/{id}/narrate` | Tạo giọng đọc cho toàn bộ scene trong video |

---

## 8. Review & Đánh giá Chất lượng Video

Sử dụng AI Vision (Claude / Gemini) để đánh giá chất lượng các phân cảnh video AI đã sinh.

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/videos/{id}/review` | Chạy review toàn bộ các scene trong video (`mode=light` hoặc `mode=full`) |
| `GET` | `/api/reviews/{video_id}` | Lấy lịch sử review chất lượng |

---

## 9. Materials (Phong cách Mỹ thuật)

Mỗi dự án kịch bản cần một mã `material` để gắn phong cách hình ảnh đồng bộ cho tất cả thực thể và phân cảnh.

| Method | Endpoint | Mục đích |
|---|---|---|
| `GET` | `/api/materials` | Lấy danh sách phong cách có sẵn (`realistic`, `3d_pixar`, `anime`, `lego`, ...) |
| `POST` | `/api/materials` | Đăng ký một material mới |
| `GET` | `/api/materials/{key}` | Chi tiết cấu hình prompt của material |

---

## 10. Models & Providers Cấu hình

| Method | Endpoint | Mục đích |
|---|---|---|
| `GET` | `/api/models` | Xem cấu hình model ảnh và model video đang sử dụng |
| `PATCH` | `/api/models` | Thay đổi model video (Omni Flash, etc.) hoặc model ảnh |
| `GET` | `/api/providers` | Xem cấu hình AI provider cho review (Claude, Gemini, OpenAI) |
| `PATCH` | `/api/providers` | Đổi provider cho module review |

---

## 11. Âm nhạc (Music / Suno)

Tích hợp tạo nhạc nền theo phong cách và độ dài kịch bản qua Suno API.

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/music/generate` | Gửi prompt sinh bài nhạc |
| `GET` | `/api/music/status/{task_id}` | Kiểm tra trạng thái sinh nhạc |
| `GET` | `/api/music/credits` | Kiểm tra số dư credit Suno |

---

## 12. Extension Gateway & WebSockets

Cổng giao tiếp thời gian thực 2 chiều giữa FlowKit Server và Chrome Extension.

| Protocol | Endpoint | Mục đích |
|---|---|---|
| `WS` | `/ws` | Kênh kết nối chính của Chrome Extension |
| `WS` | `/ws/dashboard` | Kênh push sự kiện realtime về Side Panel UI của Extension |
| `POST` | `/api/ext/callback` | HTTP fallback nhận kết quả khi mạng WebSocket chập chờn |
