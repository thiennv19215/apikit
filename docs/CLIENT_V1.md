# API v1 dành cho client

Base URL: `https://apikit.shopcongngheso5.io.vn`
Local Dev: `http://127.0.0.1:8100`

Client gửi ảnh Base64 ngay trong request hoặc truyền `media_id` có sẵn. Server tự động xử lý upload lên Google Flow, quản lý cache ảnh tránh upload trùng lặp, tự động cân bằng tải và phân bổ profile tài khoản.

---

## 1. Danh sách Endpoint Client

| Method | Endpoint | Mô tả |
|---|---|---|
| GET | `/v1/health` | Kiểm tra trạng thái hệ thống, khả năng nhận tác vụ và danh sách capabilities |
| POST | `/v1/images/generations` | 202 Accepted, tạo tác vụ sinh ảnh (Nano Banana Pro / Banana 2) |
| POST | `/v1/videos/generations` | 202 Accepted, tạo tác vụ sinh video Gemini Omni Flash (First frame, Start+End, R2V) |
| POST | `/v1/jobs/status` | Tra cứu trạng thái nhiều job |
| GET | `/v1/jobs/{job_id}` | Tra cứu trạng thái một job |
| GET | `/v1/jobs/status/{job_id}` | Alias tra cứu trạng thái |
| GET | `/v1/jobs/{job_id}/executions` | Lịch sử audit thực thi; 404 nếu không tồn tại |
| POST, GET | `/v1/characters` | 201 tạo nhân vật / 200 lấy danh sách |
| GET, PATCH, DELETE | `/v1/characters/{id}` | 200 đọc, sửa, 204 xóa nhân vật |
| POST | `/v1/characters/{id}/images/generations` | 202; sinh ảnh nhân vật |
| POST | `/v1/characters/{id}/videos/generations` | 202; sinh video nhân vật |

---

## 2. Kiểm tra trạng thái hệ thống (`GET /v1/health`)

Endpoint `GET /v1/health` không tạo job, response có header `Cache-Control: no-store`.

Khi hệ thống sẵn sàng và extension Google Flow đã kết nối:
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

## 3. Sinh Video: Gemini Omni Flash (`POST /v1/videos/generations`)

Tất cả các chế độ sinh video đều sử dụng chung endpoint `POST /v1/videos/generations`.
Hệ thống **chỉ sử dụng Gemini Omni Flash** (tuyệt đối không dùng Veo) và tự động ánh xạ đúng Google Flow Batch RPC:

| Chế độ | Google Batch RPC | Model Wire Key | Đầu vào |
|---|---|---|---|
| **1. First Frame** | `eb1hJf` | `abra_i2v_<duration>s` | 1 ảnh đầu (`start_media_id` hoặc role `start_frame`) |
| **2. Start + End Frame** | `nprQif` | `omni_flash_i2v_<duration>s_first_last` | 2 ảnh (`start` + `end` media_id hoặc 2 ảnh role `start_frame` & `end_frame`) |
| **3. Reference-to-Video (R2V)** | `MZZa6b` | `abra_r2v_<duration>s` | 1–7 ảnh tham chiếu (`reference_media_ids` hoặc role `reference`) |

Thời lượng hỗ trợ: `4`, `6`, `8`, `10` giây (mặc định 8s). Tỉ lệ hỗ trợ: `9:16` (Portrait - mặc định) hoặc `16:9` (Landscape).

---

### Cách gọi 1: First Frame to Video (1 ảnh đầu)

```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "The camera slowly pans forward as the girl smiles and waves",
    "duration_seconds": 4,
    "aspect_ratio": "9:16",
    "model": "omni_flash",
    "input_images": [
      {
        "image_base64": "<BASE64_ANH_DAU>",
        "mime_type": "image/jpeg",
        "role": "start_frame"
      }
    ]
  }'
```
*(Nếu đã có `media_id` trên Google Flow, có thể truyền `"start_media_id": "<UUID>"` thay cho `input_images`)*.

---

### Cách gọi 2: Start + End Frame to Video (Ảnh đầu & Ảnh cuối)

Dùng để tạo hoạt cảnh chuyển tiếp mượt mà (morph / camera dolly) giữa 2 khung hình chính xác.

```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Smooth cinematic camera transition from Vietnamese girl in white Ao Dai to white tiger resting in bamboo forest",
    "duration_seconds": 4,
    "aspect_ratio": "9:16",
    "model": "omni_flash",
    "generation_type": "start_end",
    "input_images": [
      {
        "image_base64": "<BASE64_ANH_DAU>",
        "mime_type": "image/jpeg",
        "role": "start_frame"
      },
      {
        "image_base64": "<BASE64_ANH_CUOI>",
        "mime_type": "image/jpeg",
        "role": "end_frame"
      }
    ]
  }'
```
*(Hoặc truyền `"start_media_id": "<UUID_DAU>"`, `"end_media_id": "<UUID_CUOI>"`)*.

---

### Cách gọi 3: Reference-to-Video (R2V - Nhiều ảnh tham chiếu)

Dùng để kết hợp các nhân vật, đối tượng hoặc bối cảnh từ 2 hay nhiều ảnh tham chiếu vào cùng một video sống động.

```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Cinematic handheld shot of white tiger walking peacefully beside the girl in ancient bamboo forest",
    "duration_seconds": 4,
    "aspect_ratio": "9:16",
    "model": "omni_flash",
    "generation_type": "reference_to_video",
    "input_images": [
      {
        "image_base64": "<BASE64_ANH_HO>",
        "mime_type": "image/jpeg",
        "role": "reference"
      },
      {
        "image_base64": "<BASE64_ANH_CO_GAI>",
        "mime_type": "image/jpeg",
        "role": "reference"
      }
    ]
  }'
```
*(Hoặc truyền `"reference_media_ids": ["<UUID_1>", "<UUID_2>"]`)*.

---

## 4. Tra cứu trạng thái Job (`GET /v1/jobs/{job_id}`)

Khi gửi request tạo video/ảnh thành công, server trả về **HTTP 202 Accepted** kèm `job_id`:
```json
{
  "jobs": [
    {
      "id": "job_8baa7d8f6a4141ec",
      "status": "queued",
      "type": "video",
      "generation_type": "image_to_video",
      "media": [],
      "error": null
    }
  ],
  "metadata": {
    "poll_after_seconds": 10,
    "done": false
  }
}
```

Client thực hiện poll trạng thái qua `GET /v1/jobs/{job_id}`:
- Khi đang xử lý: `"status": "running"`
- Khi hoàn tất: `"status": "complete"`, `"media"` chứa danh sách file video kèm signed download URL:
```json
{
  "jobs": [
    {
      "id": "job_8baa7d8f6a4141ec",
      "status": "complete",
      "type": "video",
      "generation_type": "image_to_video",
      "media": [
        {
          "id": "a6428121-16e8-4d81-882b-1467ad1b61ae",
          "type": "video",
          "url": "https://flow-content.google/video/a6428121-16e8-4d81-882b-1467ad1b61ae?Expires=...&Signature=...",
          "media_id": "a6428121-16e8-4d81-882b-1467ad1b61ae"
        }
      ],
      "error": null
    }
  ],
  "metadata": {
    "done": true
  }
}
```
- Khi lỗi: `"status": "failed"`, `"error": "thông báo lỗi chi tiết"`.
