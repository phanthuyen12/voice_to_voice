# KingVoice Backend — Tài liệu API chi tiết (dành cho hệ thống bên ngoài tích hợp)

Tài liệu này mô tả đầy đủ cách một hệ thống khác (Web, Mobile App, n8n, Make, Zapier, microservice nội bộ...) gọi vào **KingVoice Backend API** để: lồng tiếng video tự động, chuyển văn bản thành giọng nói (TTS), chuyển giọng nói thành giọng nói khác (voice dubbing), chuyển giọng nói thành văn bản + phụ đề (speech-to-text), và quản lý tiến độ job theo thời gian thực.

Tài liệu bổ sung, chi tiết hơn `README.md` — dùng file này khi bạn cần biết chính xác từng tham số, định dạng response, mã lỗi, vòng đời job, và cách xử lý webhook.

> Base URL mặc định: `http://<host>:8686` (hoặc cổng bạn đặt qua biến môi trường `PORT`).
> Swagger UI (thử API trực tiếp trên trình duyệt): `GET /docs`

---

## 1. Xác thực (Authentication)

Hầu hết endpoint (trừ các endpoint Admin, `/health`, `/api/v1/audio/transcribe`) yêu cầu **API Key** hợp lệ. Có 3 cách gửi key, backend chấp nhận cách nào đến trước:

| Cách | Ví dụ |
|---|---|
| Header `X-API-Key` | `X-API-Key: kv_live_xxxxxxxx` |
| Header `Authorization: Bearer` | `Authorization: Bearer kv_live_xxxxxxxx` |
| Query param | `?api_key=kv_live_xxxxxxxx` |

- Khi container khởi động lần đầu, một API Key mặc định tên **"KingVoice Default Key"** được tự sinh (lấy từ biến môi trường `KINGVOICE_API_KEY` / `MAXVOICE_API_KEY` nếu có, nếu không sẽ random). Xem key này qua `GET /api/v1/admin/keys` hoặc trong Dashboard (`/dashboard`).
- Nếu admin tắt yêu cầu key (`require_api_key = false`, xem mục Admin), mọi endpoint public có thể gọi không cần key.
- Key sai hoặc thiếu (khi đang yêu cầu) → `401 Unauthorized`:
  ```json
  { "detail": "Invalid or missing KingVoice API Key. Pass header 'X-API-Key: kv_live_...'" }
  ```
- Mỗi lần key được dùng hợp lệ, backend tăng `requestCount` và cập nhật `lastUsedAt` (xem được qua `GET /api/v1/admin/keys`).

---

## 2. Mô hình dữ liệu & vòng đời Job

Mọi tác vụ (dù là video, audio hay TTS) đều là một **Job**, có `id` (UUID) duy nhất, trả về ngay khi tạo (`202 Accepted` / `201 Created`) và được xử lý **bất đồng bộ** trong hàng đợi nền. Bạn luôn poll `GET /api/v1/jobs/{job_id}` hoặc nhận **webhook** để biết kết quả.

### 2.1. `job.type`
| type | Ý nghĩa |
|---|---|
| `video` | Project lồng tiếng video, có thể gồm nhiều `clips` |
| `audio` | Job nhận diện giọng nói / dub giọng nói từ 1 file audio/video duy nhất |
| `tts` | Job Text → Voice thuần (không có file đầu vào) |

### 2.2. `job.stage` (giai đoạn xử lý)
`queued` → `cutting` → `transcribing` → `generating_voice` → `ready` (xong) hoặc `failed` (lỗi ở bước nào đó, xem `job.error`).

### 2.3. `job.finalVideoStatus`
`processing` → `done` (file kết quả đã sẵn sàng tải) hoặc `failed`.

### 2.4. Cấu trúc trả về của `GET /api/v1/jobs/{job_id}` (`_serialize_job_detail`)

```json
{
  "id": "8e35c4d2-...",
  "type": "video",
  "stage": "generating_voice",
  "originalFilename": "Video Review Công Nghệ",
  "targetLanguage": "vi",
  "subMode": "target",
  "burnSubtitles": true,
  "defaultVoiceId": "multi:vi-VN-HoaiMy",
  "finalVideoStatus": "processing",
  "finalVideoPath": null,
  "error": "",
  "createdAt": "2026-11-03T10:00:00+00:00",
  "updatedAt": "2026-11-03T10:02:11+00:00",
  "progress": {
    "progress_percent": 62,
    "total_segments": 5,
    "transcribed_segments": 5,
    "tts_done_segments": 3,
    "failed_segments": 0
  },
  "clips": [
    {
      "id": "clip-uuid",
      "orderIndex": 0,
      "originalFilename": "original.mp4",
      "stage": "ready",
      "segments": [
        {
          "index": 0,
          "status": "tts_done",
          "startSec": 0.0,
          "endSec": 60.0,
          "transcriptText": "...",
          "transcriptCues": [
            { "start": 0.0, "end": 3.2, "text": "Xin chào", "originalText": "Hello", "translatedText": "Xin chào", "targetLanguage": "vi" }
          ],
          "ttsAudioPath": "/data/storage/outputs/clip-uuid/segments/0/tts-audio.wav",
          "error": ""
        }
      ]
    }
  ],
  "segments": [],
  "urls": {
    "self": "/api/v1/jobs/8e35c4d2-...",
    "download_video": null,
    "download_srt": null
  }
}
```

Lưu ý quan trọng:
- **Job `type=video`**: dữ liệu segment nằm trong `clips[].segments[]` (một project có thể nhiều clip, mỗi clip bị cắt thành nhiều segment ~60s). Trường `segments` cấp cao nhất sẽ luôn là `[]`.
- **Job `type=audio` hoặc `type=tts`**: dữ liệu segment nằm trực tiếp ở `segments[]` cấp cao nhất (luôn chỉ có 1 segment, index `0`). Trường `clips` sẽ luôn là `[]`.
- `urls.download_video` / `urls.download_srt` chỉ khác `null` khi file đã sẵn sàng — không cần tự đoán URL, dùng đúng giá trị trả về.
- `progress.progress_percent`: 0–100, tính dựa trên `finalVideoStatus` và trạng thái từng segment (xem công thức ở mục 6 nếu cần debug chính xác).

### 2.5. `segment.status`
`cut` → `extracting_audio` → `transcribing` → `transcribed` → `tts_queued` → `tts_processing` → `tts_done` (hoặc `failed` / `tts_failed` ở bất kỳ bước nào).

---

## 3. Webhook

Bất kỳ endpoint tạo job nào nhận tham số `webhook_url` đều sẽ **tự động POST JSON** tới URL đó khi job hoàn tất xử lý tổng thể (cả thành công và lỗi) — bạn không cần polling nếu dùng webhook.

**Request mà backend gửi đến `webhook_url` của bạn:**
```http
POST <your webhook_url>
Content-Type: application/json
```
```json
{
  "event": "job.finished",
  "job_id": "8e35c4d2-...",
  "type": "video",
  "stage": "ready",
  "finalVideoStatus": "done",
  "error": "",
  "targetLanguage": "vi",
  "subMode": "target",
  "download_video_path": "/api/v1/jobs/8e35c4d2-.../download/video",
  "download_srt_path": "/api/v1/jobs/8e35c4d2-.../download/srt",
  "updatedAt": "2026-11-03T10:05:00+00:00"
}
```
- `event` là `"job.finished"` nếu `finalVideoStatus == "done"`, ngược lại là `"job.failed"`.
- `download_video_path` / `download_srt_path` là **đường dẫn tương đối** (path) — bạn phải tự nối với base URL của backend (`http://<host>:8686` + path) để tải file.
- Timeout gửi webhook: 15 giây. Nếu endpoint của bạn lỗi/timeout, backend **không retry** — chỉ log cảnh báo. Vẫn nên chủ động `GET /api/v1/jobs/{job_id}` để double-check nếu không chắc đã nhận được webhook.
- Webhook được bắn ở: `handle_merge_project` (video), `handle_tts_standalone_job` (tts), `handle_audio_dub_job` (audio dub). Endpoint `POST /api/v1/audio/transcribe` **không hỗ trợ webhook** — chỉ polling.

---

## 4. Danh sách Endpoint đầy đủ

### 4.1. System & Discovery

#### `GET /health`
Không cần API Key. Dùng để healthcheck / kiểm tra trạng thái hàng đợi.
```json
{
  "status": "ok",
  "ffmpeg_available": true,
  "redis_connected": true,
  "queue_backend": "redis",
  "queue_size": 2,
  "active_tasks": 1,
  "port": 8686,
  "file_retention_hours": 2.0,
  "whisper_model": "small",
  "tts_backend": "vieneu",
  "storage_dir": "/data/storage"
}
```

#### `GET /api/v1/languages` *(cần API Key)*
Danh sách ngôn ngữ đích, chế độ phụ đề, ngôn ngữ nguồn hỗ trợ.
```json
{
  "target_languages": [ { "code": "vi", "label": "Tiếng Việt (Vietnamese)" }, ... ],
  "subtitle_modes": [ { "code": "target", "label": "Phụ đề ngôn ngữ đích (Đã dịch)" }, { "code": "bilingual", "label": "..." }, { "code": "original", "label": "..." } ],
  "source_languages": [ { "code": "en", "label": "English" }, ... ]
}
```
Mã `target_language` hỗ trợ: `vi`, `en`, `zh`, `ja`, `ko`, `fr`, `de`, `es`, `th`, `ru`, `id`, `pt`, hoặc `original` (giữ nguyên ngôn ngữ gốc, không dịch).

#### `GET /api/v1/countries` *(cần API Key)*
Query params: `lang`, `gender` (`male`/`female`), `search`, `include_vieneu_presets` (bool, default `true`).
Trả về danh sách quốc gia có giọng đọc, kèm `languages`, `genders`, `voice_count` mỗi quốc gia — dùng để dựng dropdown lọc theo quốc gia trước khi gọi `/api/v1/voices`.

#### `GET /api/v1/voices/filters` *(cần API Key)*
Trả về toàn bộ option filter trong 1 lần gọi (`countries`, `languages`, `genders`, `voice_types`) kèm số lượng — tối ưu cho UI client dựng nhiều dropdown cùng lúc mà không cần gọi nhiều API.

#### `GET /api/v1/voices` *(cần API Key)*
Query params: `country`, `lang`, `gender`, `type` (`multilingual` | `vieneu_offline` | `cloned`), `search`, `include_vieneu_presets`.
```json
{
  "total_voices": 84,
  "applied_filters": { "country": "VN", "lang": "vi", "gender": null, "type": null, "search": null },
  "default_female_voice_by_language": { "vi": "multi:vi-VN-HoaiMy", "en": "multi:en-US-Ava", ... },
  "default_male_voice_by_language": { "vi": "multi:vi-VN-NamMinh", "en": "multi:en-US-Andrew", ... },
  "by_language": { "vi": [ /* voice objects */ ] },
  "by_country": { "VN": [ /* voice objects */ ] },
  "voices": [ /* tất cả voice objects, đã lọc */ ],
  "multilingual_voices": [...],
  "vieneu_preset_voices": [...],
  "cloned_voices": [...]
}
```
Mỗi **voice object**:
```json
{
  "id": "multi:vi-VN-NamMinh",
  "name": "Nam Minh (Vietnamese — Nam trầm ấm)",
  "subtitle": "🇻🇳 Tiếng Việt • Nam • ...",
  "lang": "vi",
  "country": "VN",
  "country_name": "Vietnam",
  "locale": "vi-VN",
  "gender": "male",
  "type": "multilingual",
  "demo": "Xin chào, tôi là Nam Minh..."
}
```
`voice_id` lấy từ trường `id` này — dùng trực tiếp làm giá trị `voice_id` ở mọi endpoint TTS/dubbing khác.

---

### 4.2. Voices — Clone & Preview

#### `POST /api/v1/voices/clone` *(cần API Key, `multipart/form-data`)*
| Field | Loại | Bắt buộc | Ghi chú |
|---|---|---|---|
| `name` | form text | ✅ | Tên duy nhất cho giọng nhân bản |
| `description` | form text | ❌ | |
| `gender` | form text | ❌ | |
| `file` | file | ✅ | Mẫu âm thanh sạch, **3–8 giây**, `.wav`/`.mp3` |

Response: `{"status": "ok", "voice_id": "<name>", "name": "<name>"}`. Dùng `voice_id` này ở các endpoint TTS khác như một `voice_id` bình thường (loại `cloned`).

#### `POST /api/v1/voices/preview` *(cần API Key, JSON)*
```json
{ "voice_id": "multi:vi-VN-HoaiMy", "text": "Câu mẫu muốn nghe thử (tùy chọn)", "speed": 1.0 }
```
Nếu không truyền `text`, backend tự chọn câu demo mặc định của giọng đó. Trả về **trực tiếp file audio** `audio/wav` (không phải JSON) — dùng để phát thử ngay trên UI.

---

### 4.3. Text to Speech (Văn bản → Giọng nói)

#### `POST /api/v1/tts` *(cần API Key, JSON — đồng bộ, trả file ngay)*
```json
{
  "text": "Nội dung cần đọc...",
  "voice_id": "multi:vi-VN-HoaiMy",
  "language": "vi",
  "voice_gender": null,
  "translate_text": false,
  "speed": 1.0,
  "output_format": "mp3",
  "title": "TTS Voice Task"
}
```
- `voice_id = "auto"` → backend tự chọn giọng mặc định theo `language` (và `voice_gender` nếu có).
- Nếu `translate_text=true`, văn bản được dịch sang `language` trước khi đọc.
- Trả về trực tiếp file `audio/mpeg` hoặc `audio/wav` (không có `job_id`) — **dùng cho văn bản ngắn**, vì request giữ kết nối mở tới khi TTS xong (không timeout, không theo dõi tiến độ được).
- Lỗi văn bản rỗng → `400 { "detail": "Field \`text\` cannot be empty" }`.

#### `POST /api/v1/tts/jobs` *(cần API Key, JSON — bất đồng bộ, có job_id)*
Cùng schema `TextToSpeechRequest` như trên, cộng thêm `webhook_url` tùy chọn. **Dùng cho văn bản dài** hoặc khi cần lưu lịch sử/theo dõi % tiến độ qua Dashboard.
```json
{
  "status": "queued",
  "job_id": "uuid",
  "type": "tts",
  "voice_id": "multi:vi-VN-HoaiMy",
  "output_format": "mp3",
  "urls": {
    "status": "/api/v1/jobs/uuid",
    "download_audio": "/api/v1/jobs/uuid/download/audio"
  }
}
```
Khi `finalVideoStatus == "done"`, gọi `GET` vào `download_audio` để lấy file.

---

### 4.4. Video Pipeline — One-shot (Lồng tiếng Video tự động A→Z)

#### `POST /api/v1/videos/generate` *(cần API Key, `multipart/form-data`)*

| Field | Loại | Bắt buộc | Default | Ghi chú |
|---|---|---|---|---|
| `files` | file[] | ❌* | — | 1 hoặc nhiều video upload trực tiếp |
| `video_urls` | form text | ❌* | — | CSV / newline / JSON array các URL video — backend tự tải về |
| `music_file` | file | ❌ | — | Nhạc nền |
| `music_url` | form text | ❌ | — | URL nhạc nền (tự tải nếu không có `music_file`) |
| `title` | form text | ❌ | `"API Video Project"` | |
| `voice_id` | form text | ❌ | `"auto"` | Hoặc `male`/`female`/`"<lang>-male"` dạng rút gọn |
| `voice_gender` | form text | ❌ | — | `female`/`male`, dùng khi `voice_id="auto"` |
| `target_language` | form text | ❌ | `"vi"` | |
| `sub_mode` | form text | ❌ | `"target"` | `target` / `bilingual` / `original` |
| `burn_subtitles` | form bool | ❌ | `false` | Ghi cứng phụ đề vào video |
| `webhook_url` | form text | ❌ | — | |

*Phải có ít nhất 1 trong `files` hoặc `video_urls`, ngược lại `400`.

Response ngay (`202`):
```json
{
  "status": "queued",
  "job_id": "uuid",
  "voice_id": "multi:vi-VN-HoaiMy",
  "target_language": "vi",
  "sub_mode": "target",
  "burn_subtitles": true,
  "urls": {
    "status": "/api/v1/jobs/uuid",
    "download_video": "/api/v1/jobs/uuid/download/video",
    "download_srt": "/api/v1/jobs/uuid/download/srt"
  }
}
```

Backend sẽ tự động: lưu/tải mọi video thành các `clip` của 1 project → chạy toàn bộ pipeline (`handle_start_project`): cắt → Whisper transcribe → dịch → TTS từng đoạn → mux từng clip → ghép toàn bộ → (tùy chọn) chèn nhạc nền → (tùy chọn) burn phụ đề → lưu `final-dubbed.mp4` + gọi webhook.

**Ví dụ curl (upload file trực tiếp):**
```bash
curl -X POST "http://localhost:8686/api/v1/videos/generate" \
  -H "X-API-Key: kv_live_xxxx" \
  -F "files=@/duong/dan/video_goc.mp4" \
  -F "title=Video Review Công Nghệ" \
  -F "target_language=vi" \
  -F "voice_id=multi:vi-VN-NamMinh" \
  -F "sub_mode=bilingual" \
  -F "burn_subtitles=true" \
  -F "webhook_url=https://your-system.com/webhook/kingvoice"
```

---

### 4.5. Voice to Voice (Speech → New Voice, không cần video)

#### `POST /api/v1/audio/dub` *(cần API Key, `multipart/form-data`)*

| Field | Bắt buộc | Default | Ghi chú |
|---|---|---|---|
| `file` | ❌* | — | File audio/video đầu vào |
| `media_url` | ❌* | — | URL thay thế `file` |
| `voice_id` | ❌ | `"auto"` | Giọng mới muốn tạo |
| `voice_gender` | ❌ | — | |
| `target_language` | ❌ | `"vi"` | |
| `sub_mode` | ❌ | `"target"` | |
| `webhook_url` | ❌ | — | |

*Phải có `file` hoặc `media_url`.

Pipeline (`handle_audio_dub_job`): nhận diện giọng nói (Faster-Whisper) → dịch → tổng hợp giọng mới → lưu kết quả vào `finalVideoPath` (file audio, không phải video) kèm `.srt`.

```json
{
  "status": "queued",
  "job_id": "uuid",
  "voice_id": "multi:ru-RU-Dmitry",
  "target_language": "ru",
  "urls": {
    "status": "/api/v1/jobs/uuid",
    "download_voice": "/api/v1/jobs/uuid/download/voice",
    "download_srt": "/api/v1/jobs/uuid/download/srt"
  }
}
```

---

### 4.6. Voice to Text (Speech → Transcript + SRT, không tạo giọng mới)

#### `POST /api/v1/audio/transcribe` *(KHÔNG cần API Key, `multipart/form-data`)*

| Field | Bắt buộc | Default |
|---|---|---|
| `file` | ❌* | — |
| `media_url` | ❌* | — |
| `target_language` | ❌ | `"vi"` |
| `sub_mode` | ❌ | `"target"` |

*Phải có `file` hoặc `media_url`. Không hỗ trợ `webhook_url` — chỉ polling `GET /api/v1/jobs/{job_id}` rồi tải `download/srt`.

```json
{
  "status": "queued",
  "job_id": "uuid",
  "urls": { "status": "/api/v1/jobs/uuid", "download_srt": "/api/v1/jobs/uuid/download/srt" }
}
```

---

### 4.7. Jobs — Quản lý theo từng bước (Step-by-step, dành cho UI cần chỉnh sửa giữa chừng)

Nhóm endpoint này cho phép tạo project rỗng, thêm clip dần, sửa transcript tay, rồi mới chạy pipeline — khác với "one-shot" (`/videos/generate`) chạy A→Z ngay lập tức.

#### `GET /api/v1/jobs?limit=50` *(cần API Key)*
Danh sách job gần nhất (mặc định 50, tối đa 200), mỗi job kèm `progress`.

#### `POST /api/v1/jobs` *(cần API Key, JSON)* — Tạo project rỗng
```json
{
  "title": "Project mới",
  "target_language": "vi",
  "sub_mode": "target",
  "burn_subtitles": false,
  "default_voice_id": "multi:vi-VN-HoaiMy",
  "webhook_url": null
}
```
Trả về chi tiết job đầy đủ (giống `GET /api/v1/jobs/{id}`) nhưng chưa có clip nào.

#### `GET /api/v1/jobs/{job_id}` *(cần API Key)* — Chi tiết job (xem mục 2.4)

#### `DELETE /api/v1/jobs/{job_id}` *(cần API Key)* — Xoá job + toàn bộ file liên quan (upload, output) trên đĩa. **Không thể hoàn tác.**

#### `POST /api/v1/jobs/{job_id}/clips` *(cần API Key, `multipart/form-data`)*
| Field | Ghi chú |
|---|---|
| `files` (file[], bắt buộc) | Thêm 1+ clip video vào project |
| `auto_cut_transcribe` (bool, default `true`) | Nếu `true`, tự động cắt + transcribe ngay sau khi thêm (không cần gọi `/start`) |

#### `POST /api/v1/jobs/{job_id}/music` *(cần API Key, `multipart/form-data`, field `file`)* — Gắn nhạc nền cho project.

#### `POST /api/v1/jobs/{job_id}/start` *(cần API Key, JSON)* — Chạy toàn bộ pipeline cho project đã có clip.
```json
{ "voice_id": null, "voice_gender": null, "target_language": null, "sub_mode": null, "burn_subtitles": null, "webhook_url": null }
```
Mọi field đều tùy chọn — field nào `null` sẽ giữ giá trị đã lưu trên job. Lỗi `400` nếu project chưa có clip nào.

#### `POST /api/v1/jobs/{job_id}/retranslate` *(cần API Key, JSON)*
```json
{ "target_language": "en", "sub_mode": "bilingual" }
```
Dịch lại toàn bộ segment đã transcribe sang ngôn ngữ/chế độ mới **không cần chạy lại Whisper** (nhanh hơn nhiều so với chạy lại từ đầu).

#### `POST /api/v1/jobs/{job_id}/merge?burn_subtitles=true` *(cần API Key)*
Ghép lại video cuối cùng (dùng khi đã sửa tay transcript/TTS từng đoạn qua các endpoint Segment ở mục 4.8, và muốn build lại `final-dubbed.mp4`). Query `burn_subtitles` tùy chọn, nếu không truyền sẽ giữ giá trị đã lưu.

---

### 4.8. Segments — Sửa transcript / tạo lại giọng cho 1 đoạn cụ thể

#### `PUT /api/v1/clips/{clip_id}/segments/{index}` *(cần API Key, JSON)*
```json
{ "edited_text": "Nội dung lời thoại đã sửa tay" }
```
Ghi đè `transcriptText`/`editedText` của đúng 1 segment. **Lưu ý**: `clip_id` ở đây với job `type=audio`/`tts` chính là `job_id` (vì `segments.jobId` trỏ tới job trực tiếp khi không có clip — xem mục 2.4).

#### `POST /api/v1/clips/{clip_id}/segments/{index}/tts` *(cần API Key, JSON)*
```json
{ "voice_id": "multi:vi-VN-NamMinh", "target_language": null }
```
Tạo lại giọng nói **chỉ cho 1 đoạn này** (ví dụ sau khi sửa `edited_text` hoặc muốn đổi giọng riêng đoạn đó). Sau khi tất cả đoạn đã `tts_done`, gọi `POST /api/v1/jobs/{job_id}/merge` để ghép lại video cuối.

---

### 4.9. Downloads

| Endpoint | Điều kiện | Ghi chú |
|---|---|---|
| `GET /api/v1/jobs/{job_id}/download/video` | `finalVideoStatus=="done"` | File `.mp4` lồng tiếng hoàn chỉnh |
| `GET /api/v1/jobs/{job_id}/download/audio` | `finalVideoStatus=="done"` | Alias — tự nhận diện trả `.mp3`/`.wav` nếu `finalVideoPath` là audio (job `tts`) |
| `GET /api/v1/jobs/{job_id}/download/voice` | Giọng đã tạo tồn tại | Tải **riêng track giọng AI** (không kèm video gốc), kể cả với job video |
| `GET /api/v1/jobs/{job_id}/download/srt?mode=bilingual&target_language=en` | Có ít nhất 1 cue phụ đề | `mode`: `target`/`bilingual`/`original` (override `subMode` đã lưu); `target_language`: dịch phụ đề on-the-fly nếu khác ngôn ngữ đã cache |

Chưa sẵn sàng → `409 Conflict` kèm `detail` giải thích đang ở `stage`/`finalVideoStatus` nào. Job không tồn tại → `404`.

---

### 4.10. Admin & API Keys *(hiện không bị chặn bởi `verify_api_key` trừ `/admin/cleanup` — nên tự giới hạn truy cập ở tầng mạng/reverse proxy nếu public)*

| Endpoint | Mô tả |
|---|---|
| `GET /api/v1/admin/keys` | Danh sách toàn bộ API Key + `require_api_key` hiện tại |
| `POST /api/v1/admin/keys` `{"name": "...", "custom_key": "..."}` | Tạo key mới (bỏ `custom_key` để tự sinh `kv_live_<random>`) |
| `PATCH /api/v1/admin/keys/{key_id}` `{"is_active": false, "name": "..."}` | Khoá/đổi tên key |
| `DELETE /api/v1/admin/keys/{key_id}` | Xoá key |
| `POST /api/v1/admin/settings` `{"require_api_key": false}` | Bật/tắt yêu cầu API Key toàn hệ thống |
| `POST /api/v1/admin/cleanup?retention_hours=1` *(cần API Key)* | Dọn ngay job/file cũ hơn N giờ (không chờ vòng dọn tự động) |

---

## 5. Mã lỗi (HTTP Status) chung

| Status | Khi nào |
|---|---|
| `400 Bad Request` | Thiếu field bắt buộc (text rỗng, thiếu `files`/`video_urls`...), tải URL ngoài thất bại, dịch thất bại |
| `401 Unauthorized` | API Key sai/thiếu khi `require_api_key=true` |
| `404 Not Found` | `job_id`/`clip_id`/`key_id` không tồn tại |
| `409 Conflict` | Tải file kết quả khi job chưa `done` (xem `detail` để biết đang ở giai đoạn nào) |
| `202 Accepted` | Job đã được đưa vào hàng đợi (các endpoint tạo job bất đồng bộ) |
| `201 Created` | Tạo project / API key thành công |

Luôn kiểm tra field `detail` trong body response lỗi (chuẩn FastAPI) để biết nguyên nhân cụ thể.

---

## 6. Theo dõi tiến độ (`progress.progress_percent`)

Công thức tính trong `_compute_job_progress` (server.py):
- `finalVideoStatus == "done"` → **100%**
- `finalVideoStatus == "processing"` → **90%** (đang ghép video cuối — bước nặng cuối cùng)
- Chưa có segment nào (`total_segments == 0`) → 0% (hoặc 5% nếu đã ở stage `cutting`/`transcribing`/`generating_voice`)
- Ngược lại: điểm trung bình theo trạng thái từng segment (`cut`=0.1, `extracting_audio`/`transcribing`=0.25, `transcribed`/`tts_queued`=0.5, `tts_processing`=0.75, `tts_done`=1.0), quy đổi về khoảng 5–88%, rồi nhảy lên 90%/100% khi merge xong.

→ Nếu bạn cần UI progress bar mượt, hiểu rằng **88% → 90% → 100%** là 3 bước nhảy cố định tương ứng với giai đoạn merge cuối, không tuyến tính theo thời gian thực.

---

## 7. Giới hạn & lưu ý vận hành

- **Tự động dọn file/job cũ**: mặc định sau `KINGVOICE_RETENTION_HOURS` giờ (default 2h), job + toàn bộ file upload/output liên quan sẽ **bị xoá vĩnh viễn** khỏi server. Nếu hệ thống của bạn cần lưu lâu hơn, hãy tải file về lưu trữ riêng ngay khi `finalVideoStatus=="done"`, đừng phụ thuộc server lưu giúp.
- **Không có rate limit** ở tầng API — tải lên nhiều job đồng thời sẽ xếp hàng xử lý tuần tự theo `MAXVOICE_WORKER_THREADS` (default 2 luồng); việc nhận diện giọng nói (Whisper) và TTS nội bộ (VieNeu) luôn chạy **tuần tự với nhau** dù có nhiều luồng worker, để tránh tranh CPU (xem `cpu_lock.py`) — nghĩa là throughput tổng của các job "nặng" (video/audio) bị giới hạn bởi CPU của máy chủ, không scale tuyến tính theo số job gửi lên.
- **Giới hạn kích thước file upload**: không giới hạn ở tầng code — nhưng phụ thuộc cấu hình reverse proxy/server đứng trước (nếu có) và dung lượng đĩa `storage_dir`.
- **`voice_id` rút gọn** được hỗ trợ ở mọi nơi cần `voice_id`: `"auto"`, `"male"`/`"female"` (hoặc `"nam"`/`"nữ"`), hoặc `"<lang>-male"`/`"<lang>-female"` (ví dụ `"en-male"`) — backend tự map sang voice mặc định tương ứng (`tasks.resolve_default_voice`).
- **`target_language = "original"`**: giữ nguyên ngôn ngữ gốc phát hiện được, không dịch — phụ đề và giọng đọc sẽ dùng ngôn ngữ nguồn.

---

## 8. Ví dụ tích hợp end-to-end (Python)

```python
import requests
import time

BASE = "http://localhost:8686"
HEADERS = {"X-API-Key": "kv_live_xxxxxxxx"}

# 1. Gửi video, nhận job_id ngay
resp = requests.post(
    f"{BASE}/api/v1/videos/generate",
    headers=HEADERS,
    files={"files": open("video_goc.mp4", "rb")},
    data={
        "title": "Demo",
        "target_language": "vi",
        "voice_id": "multi:vi-VN-HoaiMy",
        "sub_mode": "bilingual",
        "burn_subtitles": "true",
    },
)
job_id = resp.json()["job_id"]

# 2. Poll tiến độ (hoặc dùng webhook_url thay vòng lặp này)
while True:
    job = requests.get(f"{BASE}/api/v1/jobs/{job_id}", headers=HEADERS).json()
    print(job["stage"], job["progress"]["progress_percent"])
    if job["finalVideoStatus"] in ("done", "failed"):
        break
    time.sleep(5)

# 3. Tải kết quả
if job["finalVideoStatus"] == "done":
    video = requests.get(f"{BASE}{job['urls']['download_video']}", headers=HEADERS)
    open("output-dubbed.mp4", "wb").write(video.content)
    srt = requests.get(f"{BASE}{job['urls']['download_srt']}?mode=bilingual", headers=HEADERS)
    open("output.srt", "wb").write(srt.content)
```

---

## 9. Tham khảo thêm

- `README.md` — hướng dẫn chạy nhanh bằng Docker, ví dụ curl ngắn gọn.
- `openapi.json` / `GET /docs` — schema OpenAPI đầy đủ, luôn đồng bộ với code (nguồn chính xác nhất nếu tài liệu này lỗi thời).
- `CLAUDE.md` — tài liệu kiến trúc nội bộ (dành cho AI agent/dev sửa code backend, không dành cho người tích hợp API).
