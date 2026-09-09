# HƯỚNG DẪN DÀNH CHO AI AGENT: TÍCH HỢP & GỌI TÍNH NĂNG FLOWKIT

> **Gói sản phẩm:** FlowKit AI Filmmaking Automation Engine  
> **Mục tiêu:** Cung cấp đầy đủ hướng dẫn, công cụ và quy chuẩn để bất kỳ AI Agent nào (Antigravity, Claude Code, Gemini CLI, Cursor, OpenAI Codex, AutoGen, CrewAI...) có thể tự động gọi và vận hành toàn bộ quy trình sản xuất video AI.

---

## 1. TỔNG QUAN KIẾN TRÚC FLOWKIT

FlowKit là hệ thống tự động hóa làm phim AI đa phương thức dựa trên Google Labs Flow (Veo 3, Imagen, Gemini Omni Flash), ElevenLabs TTS, Suno Music và YouTube API.

Hệ thống gồm 3 tầng liên kết chặt chẽ:
1. **Tầng Backend Server (`agent/`):** FastAPI chạy tại `http://127.0.0.1:8100`, quản lý cơ sở dữ liệu SQLite (`flow_agent.db`), worker hàng đợi tự động throttle (tối đa 5 request đồng thời, cooldown 10s), dịch vụ TTS, đánh giá video và YouTube.
2. **Tầng Kết nối Trình duyệt (`extension/`):** Chrome Extension Manifest V3 kết nối với Backend qua WebSocket `ws://127.0.0.1:8100/ws`. Extension giữ vai trò trung chuyển các RPC tới giao diện Google Labs Flow với cookie đăng nhập của người dùng.
3. **Tầng Giao tiếp Agent (Agent Interface Layer):**
   - **Agent Skills:** 36 skills theo chuẩn Agent Skills Open Standard (`.agents/skills/fk-*/SKILL.md`), Claude commands (`.claude/commands/`), Gemini commands (`.gemini/commands/`).
   - **Python Client SDK (`flowkit_client.py`):** Thư viện Python cấp cao cho Agent gọi hàm trực tiếp.
   - **MCP Server (`flowkit_mcp.py`):** Model Context Protocol server cho phép Agent gọi các chức năng như native tools.
   - **REST API:** Các endpoint `/api/*` và `/v1/*`.

---

## 2. QUY TRÌNH 3 BƯỚC KHỞI CHẠY (PRE-FLIGHT)

Trước khi Agent thực hiện bất kỳ tác vụ nào, hệ thống phải được bật sẵn sàng:

### Bước 1: Cài đặt thư viện
```bash
pip install -r requirements.txt
```

### Bước 2: Khởi động Backend Server
```bash
python -m agent.main
# Server sẽ lắng nghe tại: http://127.0.0.1:8100
# WebSocket phục vụ Extension: ws://127.0.0.1:8100/ws
```

### Bước 3: Tải Chrome Extension & Đăng nhập Google Flow
1. Mở trình duyệt Google Chrome, truy cập `chrome://extensions`.
2. Bật công tắc **Developer mode** ở góc phải trên.
3. Bấm **Load unpacked** và chọn thư mục `extension/` của FlowKit.
4. Mở tab mới truy cập `https://labs.google/fx/tools/flow` và đăng nhập tài khoản Google.
5. Kiểm tra biểu tượng Extension FlowKit góc trên trình duyệt hiển thị trạng thái kết nối màu xanh.

### Kiểm tra sức khỏe (Health check):
Agent kiểm tra endpoint:
```bash
curl -s http://127.0.0.1:8100/health
```
Kết quả hợp lệ: `{"status": "ok", "extension_connected": true, ...}`.

---

## 3. 4 CÁCH ĐỂ AI AGENT GỌI TÍNH NĂNG FLOWKIT

### CÁCH 1: Gọi qua Agent Skills (`.agents/skills/` hoặc `/fk-<name>`)
Dành cho: Antigravity IDE, Claude Code, Gemini CLI, Cursor, Codex.

- Trong gói này đã đóng gói sẵn **36 Skills**:
  - Đối với Antigravity / Cursor / Codex: Thư mục `.agents/skills/fk-<name>/SKILL.md` tuân thủ chuẩn mở Agent Skills có YAML frontmatter. Agent tự động nhận diện kỹ năng khi làm việc trong project.
  - Đối với Claude Code: Gõ `/fk-<name>` (ví dụ `/fk-pipeline`, `/fk-create-project`).
  - Đối với Gemini CLI: Gõ `/fk-<name>` (được cấu hình trong `.gemini/commands/fk/`).

**Ví dụ lệnh trong Claude / Gemini CLI:**
```bash
/fk-create-project
/fk-pipeline --upscale --tts --download
/fk-thumbnail
```

---

### CÁCH 2: Gọi qua Python SDK Client (`flowkit_client.py`)
Dành cho: Python scripts, AutoGen, CrewAI, LangChain, hoặc khi Agent viết script điều phối.

```python
from flowkit_client import FlowKitClient

client = FlowKitClient()

# 1. Kiểm tra kết nối
print(client.health())

# 2. Tạo dự án mới
proj = client.create_project(
    name="Cyberpunk 2099",
    material="realistic",
    story="Một thám tử tư điều tra vụ án mất tích bí ẩn tại Neo-Saigon."
)
pid = proj["id"]

# 3. Tạo nhân vật
char = client.create_character(
    project_id=pid,
    name="Kaelen",
    entity_type="character",
    image_prompt="Portrait of cybernetic detective with glowing blue optic implant, dark trench coat, rain-soaked neon street",
    description="Thám tử tư lạnh lùng, mắt phải cấy ghép quang học màu xanh.",
    voice_description="Trầm ấm, điềm tĩnh, nam tính"
)

# 4. Tạo video tập phim
vid = client.create_video(project_id=pid, title="Tap 1: Man dem Neo-Saigon", aspect_ratio="VERTICAL")
vid_id = vid["id"]

# 5. Tạo các phân cảnh
sc1 = client.create_scene(
    video_id=vid_id,
    scene_number=1,
    prompt="A detective walks slowly through a dark alleyway lit by flickering neon signs, rain falling",
    video_prompt="0-3s: The detective walks forward. 3-6s: He stops and looks up at a flickering sign. 6-8s: Rain drips from his coat.",
    narrator_text="Neo-Saigon chua bao gio ngu, va toi cung vay.",
    character_names=["Kaelen"]
)

# 6. Sinh ảnh tham chiếu cho nhân vật
client.batch_generate_refs(pid)
client.poll_batch(project_id=pid, req_type="GENERATE_REF_IMAGE", interval=5)

# 7. Sinh ảnh phân cảnh
client.batch_generate_scene_images(vid_id)
client.poll_batch(video_id=vid_id, req_type="GENERATE_IMAGE", interval=10)

# 8. Sinh video các cảnh
client.batch_generate_scene_videos(vid_id)
client.poll_batch(video_id=vid_id, req_type="GENERATE_VIDEO", interval=15)

# 9. Lồng tiếng AI (TTS)
client.generate_narrator(vid_id)
print("Pipeline hoan tat xuat sac!")
```

---

### CÁCH 3: Gọi qua MCP Server (`flowkit_mcp.py`)
Dành cho: Claude Desktop, Antigravity, Cursor, Windsurf, Zed, Continue.dev hoặc bất kỳ client nào hỗ trợ **Model Context Protocol**.

#### Cấu hình MCP Client (ví dụ trong file cấu hình MCP):
```json
{
  "mcpServers": {
    "flowkit": {
      "command": "python",
      "args": ["C:/duong_dan_den_repo/flowkit_mcp.py"]
    }
  }
}
```

#### Danh sách công cụ (Tools) được expose tự động:
| Tên Tool | Mô tả |
|---|---|
| `flowkit_health` | Kiểm tra server backend và kết nối extension |
| `flowkit_list_materials` | Lấy danh sách style mỹ thuật (realistic, 3d_pixar, anime...) |
| `flowkit_create_project` | Khởi tạo dự án kịch bản mới |
| `flowkit_create_character` | Thêm nhân vật / địa điểm / đạo cụ |
| `flowkit_create_video` | Tạo video tập phim (VERTICAL hoặc HORIZONTAL) |
| `flowkit_create_scene` | Tạo phân cảnh (prompt hành động + sub-clip timing) |
| `flowkit_batch_generate_refs` | Đẩy hàng loạt request sinh ảnh tham chiếu |
| `flowkit_batch_generate_scene_images` | Đẩy hàng loạt request sinh ảnh scene |
| `flowkit_batch_generate_scene_videos` | Đẩy hàng loạt request sinh video |
| `flowkit_get_batch_status` | Tra cứu trạng thái hàng đợi và tiến độ |
| `flowkit_poll_batch` | Chờ hàng loạt tác vụ hoàn tất tự động |
| `flowkit_generate_narrator` | Sinh giọng đọc TTS cho các cảnh |
| `flowkit_v1_generate_video` | Gọi trực tiếp Omni Flash video generation |

Agent chỉ việc gọi tool như hàm native mà không cần biết chi tiết HTTP request bên dưới.

---

### CÁCH 4: Gọi trực tiếp qua REST API (curl / HTTP)
Dành cho: CLI bash script hoặc HTTP client tiêu chuẩn.

#### Nộp Batch Request (Bắt buộc dùng batch, không loop thủ công):
```bash
curl -X POST http://127.0.0.1:8100/api/requests/batch \
  -H "Content-Type: application/json" \
  -d '{
    "requests": [
      {
        "type": "GENERATE_IMAGE",
        "video_id": "<VID>",
        "scene_id": "<SID>",
        "orientation": "VERTICAL"
      }
    ]
  }'
```

#### Kiểm tra tiến độ Batch:
```bash
curl -s "http://127.0.0.1:8100/api/requests/batch-status?video_id=<VID>&type=GENERATE_IMAGE"
# Phản hồi: {"total": 10, "pending": 5, "processing": 2, "completed": 3, "failed": 0, "done": false}
# Khi done=true: toàn bộ request đã xử lý xong.
```

---

## 4. BẢNG TRA CỨU 36 SKILLS FLOWKIT

| Nhóm | Lệnh | File Skill | Chức năng |
|---|---|---|---|
| **Pipeline cốt lõi** | `/fk-pipeline` | `skills/fk-pipeline.md` | Điều phối tự động toàn bộ pipeline từ A-Z |
| | `/fk-create-project` | `skills/fk-create-project.md` | Khởi tạo dự án, nhân vật, bối cảnh, cảnh quay |
| | `/fk-gen-refs` | `skills/fk-gen-refs.md` | Sinh ảnh mẫu nhân vật & địa điểm nhất quán |
| | `/fk-gen-images` | `skills/fk-gen-images.md` | Sinh ảnh frame đầu cho các cảnh |
| | `/fk-gen-videos` | `skills/fk-gen-videos.md` | Sinh video từ ảnh cho toàn bộ các cảnh |
| | `/fk-concat` | `skills/fk-concat.md` | Tải và nối tất cả video phân cảnh thành 1 file MP4 |
| **Kỹ thuật Điện ảnh** | `/fk-gen-chain-videos` | `skills/fk-gen-chain-videos.md` | Chuyển cảnh mượt mà bằng Start+End Frame |
| | `/fk-insert-scene` | `skills/fk-insert-scene.md` | Chèn thêm cảnh quay (cận cảnh, góc cận, đổi góc) |
| | `/fk-creative-mix` | `skills/fk-creative-mix.md` | Phối kỹ thuật quay phim tạo chất lượng điện ảnh |
| | `/fk-camera-guide` | `skills/fk-camera-guide.md` | Cẩm nang chuyển động camera, góc máy, ánh sáng |
| **Âm thanh & Lồng tiếng** | `/fk-gen-narrator` | `skills/fk-gen-narrator.md` | Viết kịch bản lời dẫn & sinh audio giọng đọc TTS |
| | `/fk-concat-fit-narrator` | `skills/fk-concat-fit-narrator.md` | Cắt video vừa khít lời dẫn TTS & burn text phụ đề |
| | `/fk-gen-text-overlays` | `skills/fk-gen-text-overlays.md` | Tạo phụ đề text overlay động |
| | `/fk-gen-tts-template` | `skills/fk-gen-tts-template.md` | Tạo voice template mới cho ElevenLabs/TTS |
| | `/fk-import-voice` | `skills/fk-import-voice.md` | Nhập file âm thanh mẫu thành giọng đọc mới |
| | `/fk-gen-music` | `skills/fk-gen-music.md` | Sinh bài hát/nhạc nền bằng Suno AI theo template |
| **Mỹ thuật & Cấu hình** | `/fk-add-material` | `skills/fk-add-material.md` | Quản lý phong cách hình ảnh (realistic, pixar...) |
| | `/fk-brand-logo` | `skills/fk-brand-logo.md` | Chèn logo kênh, intro, outro, huy hiệu 4K |
| | `/fk-change-model` | `skills/fk-change-model.md` | Xem và đổi model (Omni Flash, Veo 3) |
| | `/fk-change-provider` | `skills/fk-change-provider.md` | Đổi AI provider cho review (Claude/Gemini/OpenAI) |
| **Giám sát & Sửa lỗi** | `/fk-doctor` | `skills/fk-doctor.md` | Chẩn đoán toàn bộ lỗi hệ thống và kê đơn sửa |
| | `/fk-monitor` | `skills/fk-monitor.md` | Giám sát tiến độ pipeline và báo cáo Telegram |
| | `/fk-review-video` | `skills/fk-review-video.md` | AI chấm điểm chất lượng từng cảnh qua Vision |
| | `/fk-review-board` | `skills/fk-review-board.md` | Mở webapp Scene Review Board để duyệt ảnh/video |
| | `/fk-status` | `skills/fk-status.md` | Xem dashboard trạng thái chi tiết của dự án |
| | `/fk-switch-project` | `skills/fk-switch-project.md` | Đổi dự án đang làm việc |
| | `/fk-refresh-urls` | `skills/fk-refresh-urls.md` | Cấp lại chữ ký URL media khi bị hết hạn |
| | `/fk-fix-uuids` | `skills/fk-fix-uuids.md` | Sửa lỗi media_id định dạng CAMS... về UUID |
| | `/fk-dashboard` | `skills/fk-dashboard.md` | Xem trạng thái Google Labs Flow trên statusline |
| **YouTube & Xuất bản** | `/fk-thumbnail` | `skills/fk-thumbnail.md` | Tạo 4 biến thể thumbnail YouTube tối ưu CTR |
| | `/fk-thumbnail-guide` | `skills/fk-thumbnail-guide.md` | Hướng dẫn bố cục thumbnail câu view hiệu quả |
| | `/fk-youtube-seo` | `skills/fk-youtube-seo.md` | Tối ưu tiêu đề, mô tả, thẻ tags chuẩn SEO |
| | `/fk-youtube-upload` | `skills/fk-youtube-upload.md` | Tải video tự động lên YouTube (Shorts/Long-form) |
| | `/fk-upload-image` | `skills/fk-upload-image.md` | Upload ảnh từ máy tính lên Google Flow lấy UUID |
| | `/fk-research` | `skills/fk-research.md` | Nghiên cứu dữ kiện và fact-check trước khi viết kịch bản |
| | `/fk-capture-flow-payload`| `skills/fk-capture-flow-payload.md` | Bắt RPC payload của Google Flow khi có tính năng mới |

---

## 5. 13 QUY TẮC BẤT BIẾN (CRITICAL RULES) BẮT BUỘC AGENT PHẢI THEO

1. **Media ID luôn là UUID:** Định dạng chuẩn `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`. Tuyệt đối không dùng chuỗi `CAMS...` hay base64.
2. **Scene Prompts = CHỈ HÀNH ĐỘNG (Action only):** Không mô tả ngoại hình nhân vật trong prompt cảnh. Hình ảnh nhân vật do các ảnh tham chiếu (`imageInputs`) quyết định.
3. **Ảnh tham chiếu phải tồn tại trước:** Tất cả nhân vật/địa điểm phải có `media_id` trước khi sinh ảnh cảnh.
4. **Không viết throwaway loop scripts:** Server FlowKit tự động throttle tối đa 5 request song song và 10 giây cooldown. Bắt buộc nộp toàn bộ request qua `POST /api/requests/batch` và poll `/api/requests/batch-status`.
5. **Định hướng ảnh tham chiếu:** Nhân vật dùng khổ dọc (Portrait - VERTICAL), Địa điểm/Bối cảnh dùng khổ ngang (Landscape - HORIZONTAL).
6. **Trích xuất UUID:** Nếu API trả về chuỗi `CAMS...`, trích xuất UUID từ đường dẫn `fifeUrl`: `/image/{UUID}?...`.
7. **Hiệu ứng dòng thác khi tạo lại (Cascade):** Sinh lại ảnh cảnh sẽ tự động xóa video và bản 4K của cảnh đó.
8. **GENERATE vs REGENERATE:** `GENERATE_*` tự động bỏ qua nếu cảnh đã hoàn tất. `REGENERATE_*` luôn chạy và sinh mới.
9. **Bắt buộc có Image Material:** Dự án luôn phải khai báo `material` (ví dụ `realistic`, `3d_pixar`, `anime`).
10. **Cấu trúc Sub-clip Timing cho Video:** Video 8s phải chia thành các phân đoạn thời gian:  
    `0-3s: [hành động]. 3-6s: [hành động]. 6-8s: [hành động].`
11. **Thoại nhân vật trong ngoặc kép:** Đặt lời thoại trong dấu ngoặc kép: `Luna says "Goodnight."` Tối đa 10-15 từ mỗi phân đoạn 2-3s.
12. **Cập nhật cảnh bằng PATCH:** Sử dụng `PATCH /api/scenes/{id}` để cập nhật prompt, video_prompt, narrator_text. Không xóa đi tạo lại.
13. **Tránh trigger bộ lọc Google Safety đối với nhân vật nổi tiếng:** Khi làm phim tài liệu về nhân vật có thật, đặt tên nhân vật dạng bí danh tiếng Anh (ví dụ: `The Commander`, `The Diplomat`), không dùng tên thật hoặc chức danh chính trị nhạy cảm trong prompt.

---

## 6. ĐỒNG BỘ CẤU HÌNH BẰNG SETUP.PY

Khi thêm skill mới hoặc thay đổi môi trường:
```bash
# Đồng bộ cho tất cả AI Tools (Claude, Gemini, Codex, Agent Skills)
python setup.py --tool all

# Hoặc đồng bộ lại cấu hình đã lưu
python setup.py sync
```
Toàn bộ file commands và Agent Skills trong `.agents/skills/`, `.claude/commands/`, `.gemini/commands/` sẽ tự động cập nhật đồng bộ.
