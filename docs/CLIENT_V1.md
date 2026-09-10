# API v1 dành cho client

Base URL: `https://apikit.shopcongngheso5.io.vn`
Local Dev: `http://127.0.0.1:8100`

Client gửi ảnh dạng **Base64 (`image_base64`)** trực tiếp trong request. **Phía Client V1 KHÔNG CÓ endpoint upload** (chức năng upload chỉ có ở tầng FlowKit Agent `/api/flow/upload-image` cho quy trình kịch bản nội bộ). Server tự động xử lý upload Base64 lên Google Flow, quản lý SHA-256 cache tránh upload trùng lặp, tự động cân bằng tải và phân bổ profile tài khoản.

---

## 1. Danh sách Endpoint Client V1

| Nhóm | Method | Endpoint | Mô tả |
|---|---|---|---|
| **System** | GET | `/v1/health` | Kiểm tra trạng thái hệ thống, khả năng nhận tác vụ và capabilities |
| **Visual Styles** | GET | `/v1/materials` | Danh sách phong cách mỹ thuật ảnh (`realistic`, `3d_pixar`, `anime`...) |
| | GET | `/v1/materials/{id}` | Chi tiết hướng dẫn prompt và phong cách của một style |
| **Media Gen** | POST | `/v1/images/generations` | 202 Accepted, tạo tác vụ sinh ảnh Base64 (Nano Banana Pro / Banana 2) |
| | POST | `/v1/images/generations/batch` | 202 Accepted, tạo nhiều tác vụ sinh ảnh Base64 theo lô (batch) |
| | POST | `/v1/videos/generations` | 202 Accepted, tạo tác vụ sinh video Gemini Omni Flash (First frame, Start+End, R2V) |
| | POST | `/v1/videos/generations/batch` | 202 Accepted, tạo nhiều tác vụ sinh video Omni Flash theo lô (batch) |
| **Post-Process** | POST | `/v1/videos/concat` | Ghép nhiều clip video thành MP4 hoàn chỉnh bằng ffmpeg |
| **Entities** | GET | `/v1/characters` | Liệt kê danh sách nhân vật/thực thể cố định |
| | POST | `/v1/characters` | Tạo mới nhân vật kèm ảnh tham chiếu |
| | GET | `/v1/characters/{id}` | Lấy thông tin chi tiết nhân vật |
| **Jobs** | POST | `/v1/jobs/status` | Tra cứu trạng thái nhiều job |
| | GET | `/v1/jobs/{job_id}` | Tra cứu trạng thái một job |
| | GET | `/v1/jobs/{job_id}/executions` | Lịch sử audit thực thi |

> [!IMPORTANT]
> **Quy định điều phối dành cho AI Agent & Client:**
> - API Client V1 (`/v1/...`) hỗ trợ cả tạo tác vụ đơn lẻ (`/v1/.../generations`) và tạo hàng loạt theo lô qua Batch API (`/v1/images/generations/batch`, `/v1/videos/generations/batch`).
> - **Cơ chế Batch tự động:** Khuyên dùng các endpoint Batch để gửi toàn bộ danh sách phân cảnh trong 1 request. Server backend tự động đưa vào hàng đợi SQLite queue và điều phối (tối đa 5 request song song, 10s cooldown, tự động failover quota đa tài khoản).
> - **Tra cứu trạng thái:** Dùng `POST /v1/jobs/status` với danh sách `job_ids` để theo dõi tiến độ cả lô.
> - **Không có endpoint Upload trên Client V1:** Truyền chuỗi Base64 trực tiếp vào trường `image_base64` của `input_images`. Server tự động hash SHA-256 cache và upload lên Flow.

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

## 3. Sinh Hình Ảnh (`POST /v1/images/generations`)

Endpoint tạo tác vụ sinh ảnh bằng mô hình Banana Pro / Banana 2. Hỗ trợ Text-to-Image và Image-to-Image (tham chiếu Base64).

### Tham số Request Body:
- `prompt` (string, bắt buộc): Mô tả hình ảnh cần tạo.
- `count` (int, tuỳ chọn, 1-4, mặc định 1): Số lượng biến thể ảnh sinh ra (1 đến 4 ảnh). Alias: `variant_count`. Khi hoàn thành, toàn bộ danh sách ảnh sẽ có trong mảng `media` của Job.
- `aspect_ratio` (string, tuỳ chọn): Tỉ lệ ảnh. Giá trị chuẩn: `IMAGE_ASPECT_RATIO_LANDSCAPE` (16:9, mặc định), `IMAGE_ASPECT_RATIO_PORTRAIT` (9:16), `IMAGE_ASPECT_RATIO_SQUARE` (1:1), `IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE` (3:4), `IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE` (4:3). *Hỗ trợ alias rút gọn: `"16:9"`, `"9:16"`, `"1:1"`, `"PORTRAIT"`, `"LANDSCAPE"`.*
- `model` (string, tuỳ chọn): Mô hình sinh ảnh. Mặc định `NANO_BANANA_PRO` (hoặc alias `"pro"`). Hỗ trợ `NANO_BANANA_2` (hoặc alias `"banana2"`).
- `input_images` (array, tuỳ chọn): Danh sách ảnh tham chiếu dạng Base64.
  - `image_base64` (string): Chuỗi base64 của ảnh.
  - `mime_type` (string): `image/jpeg` hoặc `image/png`.

#### Ví dụ Text-to-Image:
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A cybernetic samurai standing under cherry blossoms in neon rain, hyper-detailed, 8k",
    "aspect_ratio": "16:9",
    "model": "pro"
  }'
```

#### Ví dụ Image-to-Image (Có ảnh tham chiếu Base64):
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Same samurai looking up at neon skyscrapers, glowing blade in hand",
    "aspect_ratio": "9:16",
    "model": "pro",
    "input_images": [
      {
        "image_base64": "<BASE64_STRING_HERE>",
        "mime_type": "image/jpeg"
      }
    ]
  }'
```

---

### Sinh Nhiều Hình Ảnh Theo Lô (Batch) (`POST /v1/images/generations/batch`)

Endpoint cho phép gửi nhiều tác vụ sinh ảnh cùng lúc. Hỗ trợ 2 định dạng payload:
1. Object: `{"requests": [ImageGenerationRequest, ...]}`
2. Array trực tiếp: `[ImageGenerationRequest, ...]`

Server tự động đưa tất cả vào hàng đợi xử lý ngầm (SQLite queue) và trả về `202 Accepted` kèm danh sách toàn bộ các job được tạo.

#### Ví dụ gọi:
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/images/generations/batch \
  -H "Content-Type: application/json" \
  -d '{
    "requests": [
      {
        "prompt": "Cyberpunk detective in rainy Tokyo street at night, neon reflections, 8k",
        "aspect_ratio": "16:9",
        "model": "pro"
      },
      {
        "prompt": "Portrait of female android with glowing amber eyes in dark laboratory",
        "aspect_ratio": "9:16",
        "model": "banana2"
      }
    ]
  }'
```

Response trả về `JobsResponse` chứa danh sách tất cả các job kèm `job_id`:
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
    },
    {
      "id": "job_9876543210fedcba",
      "status": "queued",
      "type": "image",
      "generation_type": "image",
      "provider": "google_flow",
      "media": [],
      "error": null
    }
  ],
  "metadata": {
    "counts": {"queued": 2, "running": 0, "complete": 0, "failed": 0},
    "done": false,
    "poll_after_seconds": 10
  }
}
```

Sau khi nhận kết quả, client có thể lấy danh sách `job_id` và dùng `POST /v1/jobs/status` để kiểm tra tiến độ của cả lô.

---

## 4. Sinh Video: Gemini Omni Flash (`POST /v1/videos/generations`)

Tất cả các chế độ sinh video đều sử dụng chung endpoint `POST /v1/videos/generations`.
Hệ thống **chỉ sử dụng Gemini Omni Flash** (tuyệt đối không dùng Veo) và tự động ánh xạ đúng Google Flow Batch RPC:

| Chế độ | Google Batch RPC | Model Wire Key | Đầu vào (100% Base64) |
|---|---|---|---|
| **1. First Frame** | `eb1hJf` | `abra_i2v_<duration>s` | 1 ảnh Base64 với role `start_frame` |
| **2. Start + End Frame** | `nprQif` | `omni_flash_i2v_<duration>s_first_last` | 2 ảnh Base64 với role `start_frame` & `end_frame` |
| **3. Reference-to-Video (R2V)** | `MZZa6b` | `abra_r2v_<duration>s` | 1–7 ảnh Base64 với role `reference` |

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

> [!NOTE]
> **Quy chuẩn Input V1:** Phía Client V1 **chỉ truyền ảnh dạng Base64 (`image_base64`)**, không cho phép truyền `media_id` hay các UUID nội bộ của Google Flow. Hệ thống FlowKit sẽ tự động upload Base64 lên Flow, lấy UUID và map vào đúng RPC backend.

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

---

### Sinh Nhiều Video Theo Lô (Batch) (`POST /v1/videos/generations/batch`)

Endpoint cho phép gửi nhiều tác vụ sinh video Gemini Omni Flash (hỗ trợ cả First frame, Start+End, R2V) cùng lúc. Hỗ trợ 2 định dạng payload:
1. Object: `{"requests": [VideoGenerationRequest, ...]}`
2. Array trực tiếp: `[VideoGenerationRequest, ...]`

Server tự động đưa tất cả vào SQLite queue điều phối tự động và trả về `202 Accepted` kèm danh sách toàn bộ các job được tạo.

#### Ví dụ gọi:
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/videos/generations/batch \
  -H "Content-Type: application/json" \
  -d '{
    "requests": [
      {
        "prompt": "0-3s: Cyberpunk hovercar speeds through rainy neon street. 3-6s: Camera follows close behind.",
        "duration_seconds": 8,
        "aspect_ratio": "16:9",
        "model": "omni_flash",
        "input_images": [
          {
            "image_base64": "<BASE64_FRAME_1>",
            "mime_type": "image/jpeg",
            "role": "start_frame"
          }
        ]
      },
      {
        "prompt": "Camera pushes slowly in on ancient temple hidden in bamboo forest misty morning",
        "duration_seconds": 4,
        "aspect_ratio": "9:16",
        "model": "omni_flash",
        "input_images": [
          {
            "image_base64": "<BASE64_FRAME_2>",
            "mime_type": "image/jpeg",
            "role": "start_frame"
          }
        ]
      }
    ]
  }'
```

Response trả về `JobsResponse`:
```json
{
  "jobs": [
    {
      "id": "job_1122334455667788",
      "status": "queued",
      "type": "video",
      "generation_type": "image_to_video",
      "provider": "google_flow",
      "media": [],
      "error": null
    },
    {
      "id": "job_9988776655443322",
      "status": "queued",
      "type": "video",
      "generation_type": "image_to_video",
      "provider": "google_flow",
      "media": [],
      "error": null
    }
  ],
  "metadata": {
    "counts": {"queued": 2, "running": 0, "complete": 0, "failed": 0},
    "done": false,
    "poll_after_seconds": 10
  }
}
```

---

## 5. Tra cứu trạng thái Job (`GET /v1/jobs/{job_id}` & `POST /v1/jobs/status`)

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

### 5.1. Tra cứu một job:
```bash
curl -X GET https://apikit.shopcongngheso5.io.vn/v1/jobs/job_8baa7d8f6a4141ec
```

### 5.2. Tra cứu nhiều job cùng lúc:
```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/jobs/status \
  -H "Content-Type: application/json" \
  -d '{"job_ids": ["job_8baa7d8f6a4141ec", "job_12345678abcdef01"]}'
```

### 5.3. Các trạng thái của Job:
- `"queued"`: Đang xếp hàng đợi phân bổ tài khoản.
- `"running"`: Đang được xử lý trên Google Flow.
- `"complete"`: Xử lý thành công. Trường `media` chứa link tải trực tiếp (`url` signed URL).
- `"failed"`: Thất bại. Thông tin lỗi chi tiết hiển thị trong object `error`.

Mẫu response khi hoàn tất:
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

---

## 6. Phong cách Mỹ thuật (`GET /v1/materials`)

Lấy danh sách các preset phong cách mỹ thuật ảnh (`realistic`, `3d_pixar`, `anime`, `cyberpunk`, `ghibli`, `comic_book`...). Agent có thể sử dụng các chỉ dẫn phong cách (`style_instruction`, `scene_prefix`, `negative_prompt`) để tự động tối ưu prompt cho người dùng.

```bash
curl -s https://apikit.shopcongngheso5.io.vn/v1/materials
```

Chi tiết 1 phong cách:
---

## 7. Biên tập & Ghép nối Video (`POST /v1/videos/concat`)

Nhận danh sách URL các video clip (sinh từ Gemini Omni Flash) để xuất ra 1 file MP4 hoàn chỉnh bằng ffmpeg.

```bash
curl -X POST https://apikit.shopcongngheso5.io.vn/v1/videos/concat \
  -H "Content-Type: application/json" \
  -d '{
    "video_urls": [
      "https://flow-content.google/video/clip1.mp4?...",
      "https://flow-content.google/video/clip2.mp4?..."
    ]
  }'
```

Response (HTTP 201):
```json
{
  "id": "concat_91fab82310de",
  "status": "complete",
  "video_url": "https://apikit.shopcongngheso5.io.vn/v1/videos/download/concat_91fab82310de.mp4"
}
```

---

## 8. Quản lý Thực thể & Nhân vật (`/v1/characters`)

Dành cho Agent muốn lưu trữ profile nhân vật/địa điểm cố định, tái sử dụng xuyên suốt nhiều cảnh phim.

- **Tạo nhân vật:** `POST /v1/characters` (nhận `name`, `description`, `image_prompt`, `input_images` Base64).
- **Danh sách nhân vật:** `GET /v1/characters`.
- **Chi tiết nhân vật:** `GET /v1/characters/{id}`.
- **Cập nhật nhân vật:** `PATCH /v1/characters/{id}`.
- **Xóa nhân vật:** `DELETE /v1/characters/{id}`.

---

## 9. Code mẫu tích hợp (SDK / Scripts)

### Python (Sử dụng `requests`)

```python
import base64
import time
import requests

BASE_URL = "https://apikit.shopcongngheso5.io.vn"

def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def generate_video_from_first_frame(image_path: str, prompt: str) -> str:
    # 1. Chuẩn bị payload với ảnh Base64
    payload = {
        "prompt": prompt,
        "duration_seconds": 4,
        "aspect_ratio": "9:16",
        "model": "omni_flash",
        "input_images": [
            {
                "image_base64": encode_image(image_path),
                "mime_type": "image/jpeg",
                "role": "start_frame"
            }
        ]
    }

    # 2. Gửi request tạo tác vụ
    res = requests.post(f"{BASE_URL}/v1/videos/generations", json=payload)
    res.raise_for_status()
    data = res.json()
    job_id = data["jobs"][0]["id"]
    print(f"[*] Job created: {job_id}. Polling for result...")

    # 3. Poll trạng thái cho đến khi hoàn thành
    while True:
        status_res = requests.get(f"{BASE_URL}/v1/jobs/{job_id}")
        status_res.raise_for_status()
        job = status_res.json()["jobs"][0]
        status = job["status"]

        if status == "complete":
            video_url = job["media"][0]["url"]
            print(f"[+] Hoàn thành! URL video: {video_url}")
            return video_url
        elif status == "failed":
            error_msg = job.get("error", {}).get("message", "Unknown error")
            raise RuntimeError(f"[-] Job thất bại: {error_msg}")

        print(f"[*] Trạng thái: {status}... chờ 10s")
        time.sleep(10)

if __name__ == "__main__":
    generate_video_from_first_frame("start.jpg", "A cinematic camera pans over a futuristic city")
```

### Node.js / TypeScript (Sử dụng `fetch`)

```typescript
import * as fs from "fs";

const BASE_URL = "https://apikit.shopcongngheso5.io.vn";

async function generateOmniVideo(imagePath: string, prompt: string): Promise<string> {
  const imageBase64 = fs.readFileSync(imagePath, { encoding: "base64" });

  // 1. Gửi request tạo tác vụ
  const res = await fetch(`${BASE_URL}/v1/videos/generations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt,
      duration_seconds: 4,
      aspect_ratio: "9:16",
      model: "omni_flash",
      input_images: [
        {
          image_base64: imageBase64,
          mime_type: "image/jpeg",
          role: "start_frame"
        }
      ]
    })
  });

  if (!res.ok) {
    throw new Error(`API Error: ${res.statusText} (${res.status})`);
  }

  const data = await res.json();
  const jobId = data.jobs[0].id;
  console.log(`Job queued: ${jobId}. Polling...`);

  // 2. Poll kết quả
  while (true) {
    await new Promise((r) => setTimeout(r, 10000));
    const pollRes = await fetch(`${BASE_URL}/v1/jobs/${jobId}`);
    const pollData = await pollRes.json();
    const job = pollData.jobs[0];

    if (job.status === "complete") {
      console.log("Success! Video URL:", job.media[0].url);
      return job.media[0].url;
    } else if (job.status === "failed") {
      throw new Error(`Generation failed: ${JSON.stringify(job.error)}`);
    }
    console.log(`Current status: ${job.status}...`);
  }
}
```

---

## 11. Các lưu ý kỹ thuật quan trọng cho bên tích hợp

1. **Gửi ảnh trực tiếp dạng Base64:**
   - Client V1 **không cần endpoint upload**. Truyền trực tiếp chuỗi Base64 qua trường `image_base64`.
   - **Tuyệt đối không truyền `media_id`** (UUID nội bộ của Google Flow). Nếu truyền direct media IDs, API sẽ trả về lỗi `HTTP 422 Unprocessable Entity`.
2. **Cơ chế Bất đồng bộ (Async Job):**
   - API trả về HTTP `202 Accepted` ngay lập tức kèm `job_id`.
   - Thời gian sinh video Omni Flash thường mất từ **60 giây đến 180 giây**. Khuyến nghị poll mỗi **10 giây**.
3. **Mô hình Video:**
   - Chỉ hỗ trợ model `"omni_flash"`. Tuyệt đối không dùng Veo qua Client V1.
4. **Mã lỗi thường gặp:**
   - `422 Unprocessable Entity`: Sai định dạng tham số (ví dụ: truyền `media_id` trực tiếp, tỉ lệ không hợp lệ, hoặc model không phải `omni_flash`).
   - `503 Service Unavailable`: Hệ thống đang bảo trì hoặc tài khoản backend quá tải/mất kết nối extension. Kiểm tra trước qua `GET /v1/health`.
