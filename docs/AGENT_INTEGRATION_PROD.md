# HƯỚNG DẪN TÍCH HỢP CHO AI AGENT (KẾT NỐI PRODUCTION SERVER)

Tài liệu này dành riêng cho các kỹ sư và nhà phát triển muốn tích hợp **AI Agent** (như Cursor, Claude, Antigravity, LangChain, CrewAI, AutoGen, OpenAI Assistant hoặc Agent tự phát triển) kết nối trực tiếp đến **Production Server của FlowKit**.

> [!IMPORTANT]
> **Không cần cài đặt Local hay Chrome Extension:**
> Production Server đã duy trì sẵn toàn bộ tài khoản Google Flow, Chrome worker headless, hàng đợi điều phối và phân bổ tải. 
> Phía đối tác / Agent của bạn chỉ cần gửi HTTP Request hoặc gọi Function Calling / MCP trực tiếp tới URL Production.

- **Production Base URL:** `https://apikit.shopcongngheso5.io.vn`
- **Giao thức:** REST API (JSON & Base64), WebSocket, MCP Server.

---

## 1. NGUYÊN LÝ HOẠT ĐỘNG DÀNH CHO AGENT

Quy trình tự động hóa của AI Agent diễn ra theo 3 bước:

```
[User Yêu Cầu] 
      │
      ▼
[AI Agent] ──(1. Chuẩn hóa Prompt & Base64)──► [POST /v1/.../generations]
                                                      │
                                                      ▼ (Trả về ngay HTTP 202 kèm job_id)
[AI Agent] ◄──(2. Poll mỗi 10s: GET /v1/jobs/{id})── [Worker Production xử lý]
      │
      ▼ (3. Khi status == "complete")
[Trả video/ảnh cho User qua Signed URL]
```

### Quy tắc dữ liệu cho Agent:
1. **Ảnh gửi lên 100% Base64:** Agent không cần upload ảnh lên trước. Truyền chuỗi base64 vào trường `image_base64`.
2. **Không dùng UUID/Media ID:** Agent không tự sinh hay truyền `media_id` lên Client API v1. Server tự động hash SHA-256, cache và upload lên Flow.
3. **Model Video:** Luôn sử dụng `"omni_flash"`.
4. **Prompt Video:** Agent nên định dạng theo **Sub-clip timing** để đạt chất lượng chuyển động tốt nhất:
   - Ví dụ: `0-3s: The camera slowly tracks forward as the samurai walks in neon rain. 3-6s: He unsheathes his katana with glowing blue light.`

---

## 2. CÁCH 1: FUNCTION CALLING CHO LLM (LANGCHAIN / CREWAI / OPENAI TOOLS)

Cung cấp trực tiếp Tool Definitions cho LLM (GPT-4o, Claude 3.5 Sonnet, Gemini 2.0 Flash) để LLM tự quyết định gọi sinh ảnh hay video.

### 2.1. Tool Definitions (JSON Schema chuẩn OpenAI / LangChain)

```python
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Sinh hình ảnh AI chất lượng cao (Nano Banana Pro / Banana 2). Hỗ trợ text-to-image hoặc biến đổi từ ảnh gốc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Mô tả chi tiết hình ảnh cần tạo (bối cảnh, góc máy, ánh sáng, chi tiết nhân vật)."
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "enum": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                        "default": "16:9",
                        "description": "Tỉ lệ khung hình."
                    },
                    "model": {
                        "type": "string",
                        "enum": ["NANO_BANANA_PRO", "NANO_BANANA_2"],
                        "default": "NANO_BANANA_PRO",
                        "description": "Mô hình sinh ảnh."
                    },
                    "image_base64": {
                        "type": "string",
                        "description": "Chuỗi base64 của ảnh gốc nếu muốn vẽ dựa trên ảnh có sẵn (Image-to-Image)."
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_omni_video",
            "description": "Sinh video AI bằng Gemini Omni Flash. Hỗ trợ tạo video từ 1 ảnh đầu (first frame), chuyển cảnh giữa 2 ảnh (start+end frame), hoặc kết hợp nhiều ảnh tham chiếu (R2V).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Mô tả hành động và chuyển động camera theo từng mốc giây (ví dụ: '0-3s: camera pan. 3-6s: character turns around')."
                    },
                    "duration_seconds": {
                        "type": "integer",
                        "enum": [4, 6, 8, 10],
                        "default": 8,
                        "description": "Độ dài video (4s, 6s, 8s hoặc 10s)."
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "enum": ["9:16", "16:9"],
                        "default": "9:16",
                        "description": "Tỉ lệ video (9:16 cho Shorts/TikTok/Reels, 16:9 cho YouTube ngang)."
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["first_frame", "start_end", "reference_to_video"],
                        "default": "first_frame",
                        "description": "Chế độ sinh video."
                    },
                    "start_frame_base64": {
                        "type": "string",
                        "description": "Base64 của ảnh xuất phát (bắt buộc cho first_frame và start_end)."
                    },
                    "end_frame_base64": {
                        "type": "string",
                        "description": "Base64 của ảnh kết thúc (chỉ dùng cho mode start_end)."
                    },
                    "reference_images_base64": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Danh sách 1-7 ảnh Base64 tham chiếu cho mode reference_to_video."
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_job_status",
            "description": "Kiểm tra tiến độ tác vụ theo job_id và lấy URL kết quả tải về.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Mã job nhận được khi tạo yêu cầu."
                    }
                },
                "required": ["job_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_speech",
            "description": "Sinh file âm thanh giọng đọc thuyết minh (TTS) từ văn bản.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Văn bản cần đọc."},
                    "voice_id": {"type": "string", "description": "Tên hoặc ID giọng đọc."},
                    "speed": {"type": "number", "default": 1.0, "description": "Tốc độ đọc."},
                    "instruct": {"type": "string", "description": "Chỉ dẫn phong cách giọng đọc (ví dụ: trầm ấm, kịch tính)."}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_music",
            "description": "Sinh nhạc nền AI bằng Suno.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Mô tả thể loại hoặc phong cách âm nhạc."},
                    "style": {"type": "string", "description": "Tags phong cách (ví dụ: cinematic, synthwave, lo-fi)."},
                    "title": {"type": "string", "description": "Tiêu đề bản nhạc."},
                    "instrumental": {"type": "boolean", "default": true}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "concat_videos",
            "description": "Ghép nối nhiều video clips và lồng tiếng thuyết minh / nhạc nền thành 1 file MP4 hoàn chỉnh.",
            "parameters": {
                "type": "object",
                "properties": {
                    "video_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Danh sách URL các video clip theo thứ tự."
                    },
                    "narration_audio_url": {"type": "string", "description": "URL file âm thanh thuyết minh (nếu có)."},
                    "music_url": {"type": "string", "description": "URL file nhạc nền (nếu có)."},
                    "narration_volume": {"type": "number", "default": 1.0},
                    "music_volume": {"type": "number", "default": 0.3}
                },
                "required": ["video_urls"]
            }
        }
    }
]
```

### 2.2. Tool Implementation (Mã nguồn thực thi cho Agent)

```python
import base64
import time
import requests

PROD_BASE_URL = "https://apikit.shopcongngheso5.io.vn"

def tool_generate_image(prompt: str, aspect_ratio: str = "16:9", model: str = "NANO_BANANA_PRO", image_base64: str = None) -> dict:
    payload = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "model": model,
        "input_images": [{"image_base64": image_base64, "mime_type": "image/jpeg"}] if image_base64 else []
    }
    res = requests.post(f"{PROD_BASE_URL}/v1/images/generations", json=payload, timeout=30)
    res.raise_for_status()
    return res.json()

def tool_generate_omni_video(
    prompt: str,
    duration_seconds: int = 8,
    aspect_ratio: str = "9:16",
    mode: str = "first_frame",
    start_frame_base64: str = None,
    end_frame_base64: str = None,
    reference_images_base64: list = None
) -> dict:
    input_images = []
    generation_type = "image_to_video"

    if mode == "first_frame" and start_frame_base64:
        input_images.append({"image_base64": start_frame_base64, "mime_type": "image/jpeg", "role": "start_frame"})
    elif mode == "start_end" and start_frame_base64 and end_frame_base64:
        generation_type = "start_end"
        input_images.append({"image_base64": start_frame_base64, "mime_type": "image/jpeg", "role": "start_frame"})
        input_images.append({"image_base64": end_frame_base64, "mime_type": "image/jpeg", "role": "end_frame"})
    elif mode == "reference_to_video" and reference_images_base64:
        generation_type = "reference_to_video"
        for b64 in reference_images_base64:
            input_images.append({"image_base64": b64, "mime_type": "image/jpeg", "role": "reference"})

    payload = {
        "prompt": prompt,
        "duration_seconds": duration_seconds,
        "aspect_ratio": aspect_ratio,
        "model": "omni_flash",
        "generation_type": generation_type,
        "input_images": input_images
    }
    res = requests.post(f"{PROD_BASE_URL}/v1/videos/generations", json=payload, timeout=30)
    res.raise_for_status()
    return res.json()

def tool_check_job_status(job_id: str) -> dict:
    res = requests.get(f"{PROD_BASE_URL}/v1/jobs/{job_id}", timeout=30)
    res.raise_for_status()
    return res.json()

def tool_wait_for_completion(job_id: str, max_wait_seconds: int = 240, interval: int = 10) -> str:
    """Agent gọi hàm này để tự động chờ và lấy thẳng URL kết quả."""
    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        data = tool_check_job_status(job_id)
        job = data["jobs"][0]
        status = job.get("status")
        
        if status == "complete":
            return job["media"][0]["url"]
        elif status == "failed":
            raise RuntimeError(f"Job failed: {job.get('error')}")
        
        time.sleep(interval)
    raise TimeoutError(f"Job {job_id} timed out after {max_wait_seconds}s")

def tool_generate_speech(text: str, voice_id: str = None, speed: float = 1.0, instruct: str = None) -> dict:
    payload = {"text": text, "voice_id": voice_id, "speed": speed, "instruct": instruct}
    res = requests.post(f"{PROD_BASE_URL}/v1/audio/speech", json=payload, timeout=30)
    res.raise_for_status()
    return res.json()

def tool_generate_music(prompt: str, style: str = "", title: str = "", instrumental: bool = True) -> dict:
    payload = {"prompt": prompt, "style": style, "title": title, "instrumental": instrumental}
    res = requests.post(f"{PROD_BASE_URL}/v1/audio/music", json=payload, timeout=30)
    res.raise_for_status()
    return res.json()

def tool_concat_videos(video_urls: list, narration_audio_url: str = None, music_url: str = None,
                       narration_volume: float = 1.0, music_volume: float = 0.3) -> dict:
    payload = {
        "video_urls": video_urls,
        "narration_audio_url": narration_audio_url,
        "music_url": music_url,
        "narration_volume": narration_volume,
        "music_volume": music_volume
    }
    res = requests.post(f"{PROD_BASE_URL}/v1/videos/concat", json=payload, timeout=180)
    res.raise_for_status()
    return res.json()
```

---

## 3. CÁCH 2: DÙNG MODEL CONTEXT PROTOCOL (MCP) CHO CLAUDE / CURSOR / ANTIGRAVITY

Nếu đối tác sử dụng Cursor IDE, Claude Desktop, hoặc Antigravity, chỉ cần cung cấp file `flowkit_mcp.py` và cấu hình biến môi trường trỏ về Production Server.

### File cấu hình MCP (`mcp.json`):
```json
{
  "mcpServers": {
    "flowkit": {
      "command": "python",
      "args": ["/duong_dan_luu_file/flowkit_mcp.py"],
      "env": {
        "FLOWKIT_BASE_URL": "https://apikit.shopcongngheso5.io.vn"
      }
    }
  }
}
```

Agent sẽ có sẵn các công cụ native:
- `flowkit_health`: Kiểm tra readiness của Production Server.
- `flowkit_v1_generate_video`: Sinh video Omni Flash từ xa.
- `flowkit_v1_generate_image`: Sinh ảnh Banana Pro/2 từ xa.
- `flowkit_poll_batch`: Tự động đợi hoàn tất tác vụ.

---

## 4. CÁCH 3: DÙNG PYTHON CLIENT SDK (`flowkit_client.py`)

Đối tác chỉ cần copy 1 file duy nhất `flowkit_client.py` vào project của họ:

```python
import base64
from flowkit_client import FlowKitClient

# Khởi tạo client kết nối thẳng tới Production Server
client = FlowKitClient(base_url="https://apikit.shopcongngheso5.io.vn")

# 1. Kiểm tra trạng thái hệ thống
print(client.health())

# 2. Đọc ảnh mẫu
with open("test.jpg", "rb") as f:
    b64 = base64.b64encode(f.read()).decode("utf-8")

# 3. Đặt lệnh sinh video Omni Flash (First frame)
res = client.v1_generate_video(
    prompt="A futuristic flying car glides through illuminated skyscrapers at night",
    duration_seconds=4,
    aspect_ratio="9:16",
    model="omni_flash",
    input_images=[
        {"image_base64": b64, "mime_type": "image/jpeg", "role": "start_frame"}
    ]
)

job_id = res["jobs"][0]["id"]
print(f"Đã tạo job: {job_id}")

# 4. Tra cứu trạng thái
status = client.v1_get_job(job_id)
print(status)
```

---

## 5. SYSTEM PROMPT KHUYẾN NGHỊ CHO AI AGENT

Khi cấu hình System Prompt cho LLM của đối tác, hãy đưa chỉ dẫn sau vào system instruction để Agent sinh prompt chuẩn xác nhất với Google Flow Omni Flash:

```markdown
You have access to FlowKit AI Filmmaking tools connecting to production server https://apikit.shopcongngheso5.io.vn.
When asked to generate videos or images, follow these rules:
1. Model: Always use "omni_flash" for video generation.
2. Video Prompts must specify action and motion only (do not repeat character appearance if using reference/start images).
3. Structure 8s video prompts with sub-clip timing: "0-3s: [action]. 3-6s: [action]. 6-8s: [action]."
4. Video duration must be one of: 4, 6, 8, or 10 seconds.
5. Send images strictly as base64 strings in input_images. Never pass raw media UUIDs.
6. After submitting generation, poll check_job_status every 10 seconds until status is "complete", then provide the output URL.
```
