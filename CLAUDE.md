# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

KingVoice/MaxVOICE headless backend: a FastAPI REST API, independent of the PyQt6 desktop app it was cloned from, that automatically dubs video into other languages (cut → Faster-Whisper transcription → translation → neural TTS → mux/merge) and generates standalone AI voice (TTS) and subtitles. Designed to run in Docker and be called by external systems (web, mobile, n8n, Make, Zapier, webhooks).

The `backend/` directory is fully self-contained — it has no import dependency on the sibling desktop app (`../app`, `../build.spec`) and can be copied to any other repo/VPS on its own.

## Commands

```bash
# Run with Docker (recommended) — includes Redis, persists /data and the HuggingFace model cache
cp .env.example .env
docker compose up -d --build

# Run directly with Python (dev/fast iteration, no Docker/Redis — falls back to an in-memory queue)
pip install fastapi "uvicorn[standard]" python-multipart edge-tts soundfile
python run_server.py          # reads HOST/PORT/RELOAD env vars, defaults to 0.0.0.0:8686

# Swagger UI / API docs once running
http://localhost:8686/docs
http://localhost:8686/health

# Lighter image/build variant
docker compose -f docker-compose.light.yml up -d --build
```

There is no test suite, linter, or formatter configured in this directory — don't invent commands for them.

## Architecture

### Request flow

`app/server.py` is the single FastAPI app containing all routes, Pydantic request/response models, and voice/country/language catalog logic. There is no router-splitting — everything lives in this one file (~1600 lines). Endpoints only ever do three things: validate input, write/read SQLite via `app/db.py`, and hand off long-running work to `PipelineWorker.submit(...)` (`app/pipeline/worker.py`). Nothing CPU-heavy runs on the request thread except the synchronous one-shot `/api/v1/tts` endpoint and voice preview.

### Job / Clip / Segment model (app/db.py)

Three SQLite tables (WAL mode) form the state machine for every unit of work:
- **jobs**: one row per user-visible task (`type` is `"video"`, `"audio"`, or `"tts"`). Carries `stage` (queued → cutting → transcribing → generating_voice → ready/failed) and `finalVideoStatus` (processing/done/failed) plus `finalVideoPath`, target language, sub mode, webhook URL.
- **clips**: only for `type="video"` jobs — a project can have multiple clips (uploaded files or downloaded URLs), ordered by `orderIndex`.
- **segments**: every clip (or non-video job) is split into segments. Critically, `segments.jobId` is overloaded to mean "the owning clip's id" for video projects, or "the job's own id" for audio/tts jobs — see the comment block above `_cut_clip` in `app/pipeline/tasks.py`. This lets every segment-level function (transcribe, TTS) stay unaware of whether clips/projects exist at all.

`app/pipeline/tasks.py::all_segments_for_job(job)` / `project_segments_in_order(project_id)` are the canonical way to flatten a job's segments across this clip/segment indirection — reuse them instead of querying `clips`/`segments` directly when the job type isn't known.

### Pipeline worker (app/pipeline/worker.py)

`PipelineWorker` is a small home-grown task queue, not Celery/RQ: it tries Redis (`REDIS_URL`) first for durable, cross-restart queuing, and transparently falls back to an in-memory `queue.Queue` if Redis is unreachable (checked via `worker.queue_backend` / `/health`). Tasks submitted via `worker.submit(fn, *args, **kwargs)` are plain functions from `app/pipeline/tasks.py`; when backed by Redis they're serialized as `{module, fn, args, kwargs}` JSON and re-imported by name on pop, so **task functions must stay importable top-level functions in `tasks.py`** (don't make them closures/lambdas/methods). `AppConfig` instances are specially serialized/reconstructed (`_serialize_arg`/`_deserialize_arg`) since config isn't JSON-safe otherwise. The `_Signal` class is a drop-in replacement for PyQt6's `pyqtSignal` so `tasks.py` code (originally written for the desktop app) can call `.emit(...)` unchanged in this headless context; Redis-backed signal emissions are also recorded to a bounded Redis list for cross-process event visibility.

### Pipeline stages (app/pipeline/tasks.py)

The orchestrator is `handle_start_project` (the "Bắt đầu"/Start flow): it runs cut → transcribe → TTS → merge synchronously, step by step, directly on the worker thread (not re-enqueuing itself), for every clip/segment in the project. Each stage has both a standalone entry point (for incremental/manual use from the step-by-step API) and is called inline by the orchestrator:
- `_cut_clip` / `handle_cut_clip`: splits a clip into fixed-length chunks via `ffmpeg_service.split_video_into_segments` (stream-copy, so cuts snap to keyframes).
- `handle_transcribe_segment`: extracts WAV audio, runs Faster-Whisper (`whisper_service`), then `_build_cues_and_write_srt` translates cues (`translation_service`) and writes a per-segment `subtitle.srt`.
- `handle_tts_segment`: resolves what language/text the chosen voice should actually speak (`_prepare_cues_for_voice` — handles voice-language mismatches and on-the-fly translation), groups cues for natural-sounding TTS boundaries (`text_splitter.group_cues_for_tts`), synthesizes per-cue audio with caching by content hash (`cue-<sha1>.wav`, skipped if already rendered), and time-stretches/pads each cue to its subtitle timing window (`merge_service.create_timed_audio_track`).
- `handle_merge_project`: muxes each segment's video with its TTS audio (`merge_service.mux_video_with_voice`), concatenates all segments, optionally adds background music and burns subtitles, and writes `final-dubbed.mp4` + `final-dubbed.srt` + a standalone `final-voice.wav`.
- `handle_retranslate_segment` / `handle_retranslate_project`: re-translate already-transcribed cues into a new target language/sub mode without re-running Whisper.
- `handle_tts_standalone_job` / `synthesize_long_text_to_file`: the plain Text→Voice path (`type="tts"` jobs and `/api/v1/tts`), chunking long text and concatenating.
- `handle_audio_dub_job`: Speech→Voice one-shot (transcribe then re-synthesize in a new voice, no video involved).

Every orchestrated/job-ending task calls `_notify_webhook(job_id)` at the end (success or failure) if the job has a `webhookUrl`, and updates `db` + emits `worker.job_changed`/`segment_changed` signals so progress is observable via `GET /api/v1/jobs/{id}` (percent computed in `server.py::_compute_job_progress`).

### Services (app/services/)

Stateless helpers called by `tasks.py`, mostly thin wrappers around `ffmpeg`/`ffprobe` subprocess calls or ML inference:
- `ffmpeg_service.py` / `merge_service.py`: all video/audio subprocess manipulation (cut, mux, concat, background music, subtitle burn-in).
- `whisper_service.py`: Faster-Whisper transcription; caches the loaded model per `(size, device, compute_type)`.
- `local_tts_service.py`: the primary TTS backend — hardcodes the catalog of multilingual voices (`MULTILINGUAL_VOICES`, edge-tts-backed) plus VieNeu offline Vietnamese presets and voice cloning.
- `resona_service.py`: alternative paid cloud TTS backend (`tts_backend="resona"`), with quality-check retry logic in `tasks._synthesize_chunk`.
- `translation_service.py`: language catalogs (`TARGET_LANGUAGES`, `SUBTITLE_MODES`, `COMMON_SOURCE_LANGUAGES`) and the actual translation calls.
- `text_splitter.py`: splits/groups text and subtitle cues to stay within TTS request limits and produce natural-sounding segment boundaries.
- `srt_service.py`: SRT parsing/formatting and `target`/`bilingual`/`original` subtitle-mode rendering.
- `cpu_lock.py`: a single process-wide `heavy_inference_lock` serializing Faster-Whisper and local TTS calls — both already parallelize internally across all CPU cores, so running two concurrently on separate worker threads is slower, not faster, due to CPU oversubscription. Only gate heavy *inference* calls on this; ffmpeg and network calls (Resona) intentionally aren't gated by it.

### Config & storage (app/config.py, app/paths.py)

`AppConfig` merges environment variables with a JSON file (`$MAXVOICE_DATA_DIR/config.json`, default `~/.voicetext_studio`) — env vars set defaults, `save_config()` persists runtime admin changes (e.g. `require_api_key` toggle via `/api/v1/admin/settings`). `storage_dir` roots all upload/output/music paths (see `app/paths.py`: `job_upload_dir`, `job_output_dir`, `chunks_dir`, `segment_dir`, `music_dir`), all keyed by job/clip id.

### Auth

`verify_api_key` (in `server.py`) accepts `X-API-Key`, `Authorization: Bearer`, or `?api_key=` against the `api_keys` SQLite table (or an env master key `KINGVOICE_API_KEY`/`MAXVOICE_API_KEY`). The web dashboard bypasses this via a special `X-KingVoice-Dashboard: 1` header. A default key is auto-seeded on first run if the table is empty. `require_api_key=false` (admin setting or `KINGVOICE_REQUIRE_API_KEY=false`) makes the key optional.

### Auto-cleanup

A daemon thread (`_periodic_cleanup_loop` in `server.py`) runs `cleanup_expired_files_and_jobs` every `cleanup_interval_seconds` (default 300s), deleting jobs/storage older than `file_retention_hours` (default 2h) plus orphaned temp files — this is deliberate (VPS disk is limited), not a bug, so don't "fix" jobs disappearing after a couple of hours.

## Gotchas worth knowing before touching this code

- Don't add new routers/files for endpoints — the codebase's convention is a single `server.py`; follow it unless asked to refactor.
- Task functions passed to `worker.submit` must remain plain, importable module-level functions in `tasks.py` because Redis-mode re-imports them by `module`+`fn` name.
- `segments.jobId` is not always a `jobs.id` — for video projects it's a `clips.id`. Always go through `all_segments_for_job`/`project_segments_in_order` rather than assuming a flat `jobs → segments` relationship.
- TTS cue audio is cached on disk by content hash (`cue-<sha1>.wav`); changing the hashed inputs (voice, language, speed, text) in `_synthesize_cue` changes cache keys, so be deliberate about what's included in the hash.
- Never run Faster-Whisper transcription and local TTS synthesis concurrently without `cpu_lock.heavy_inference_lock` — see `cpu_lock.py`'s comment for the measured regression.
