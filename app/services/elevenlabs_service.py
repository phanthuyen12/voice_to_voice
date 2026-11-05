import os

import requests

# ElevenLabs cloud TTS backend (tts_backend="elevenlabs"): paid API, but by far the
# most expressive/"nhấn nhá" neural voices available to this app — unlike VieNeu
# (local, limited prosody range) or the edge-tts-backed `multi:...` voices (flaky
# NoAudioReceived failures that silently fall back to the robotic macOS `say`).


class ElevenLabsError(Exception):
    def __init__(self, message, status=None, http_status=None):
        super().__init__(message)
        self.status = status
        self.http_status = http_status


class MissingApiKeyError(ElevenLabsError):
    def __init__(self, message="ELEVENLABS_API_KEY chưa được cấu hình trong Cài đặt"):
        super().__init__(message, status="missing_api_key")


def _require_key(api_key):
    if not api_key:
        raise MissingApiKeyError()


def _headers(api_key, accept=None):
    headers = {"xi-api-key": api_key}
    if accept:
        headers["Accept"] = accept
    return headers


def _raise_for_response(resp):
    if resp.ok:
        return
    detail = None
    try:
        data = resp.json()
        detail = data.get("detail")
    except ValueError:
        pass
    if isinstance(detail, dict):
        raise ElevenLabsError(
            detail.get("message", "ElevenLabs API error"),
            status=detail.get("status"),
            http_status=resp.status_code,
        )
    if isinstance(detail, str) and detail:
        raise ElevenLabsError(detail, http_status=resp.status_code)
    raise ElevenLabsError(f"ElevenLabs API error: HTTP {resp.status_code}", http_status=resp.status_code)


def list_voices(api_base, api_key, timeout=30):
    """Returns the raw `{"voices": [...]}` payload from GET /v1/voices — every
    voice the account has access to (ElevenLabs's own library + any cloned ones).
    """
    _require_key(api_key)
    resp = requests.get(f"{api_base}/v1/voices", headers=_headers(api_key), timeout=timeout)
    _raise_for_response(resp)
    return resp.json()


def get_voice(api_base, api_key, voice_id, timeout=30):
    _require_key(api_key)
    resp = requests.get(f"{api_base}/v1/voices/{voice_id}", headers=_headers(api_key), timeout=timeout)
    _raise_for_response(resp)
    return resp.json()


def text_to_speech_bytes(
    api_base,
    api_key,
    text,
    voice_id,
    model_id="eleven_multilingual_v2",
    stability=0.5,
    similarity_boost=0.75,
    style=0.35,
    use_speaker_boost=True,
    output_format="mp3_44100_128",
    timeout=120,
):
    """Synthesizes `text` with ElevenLabs's `voice_id` and returns raw audio bytes
    (mp3, per `output_format`). `style` > 0 pushes the model toward exaggerated,
    more varied delivery (more "nhấn nhá") at some cost to voice consistency —
    0.3-0.5 is a good starting range for narration/dubbing.
    """
    _require_key(api_key)
    resp = requests.post(
        f"{api_base}/v1/text-to-speech/{voice_id}",
        headers={**_headers(api_key, accept="audio/mpeg"), "Content-Type": "application/json"},
        params={"output_format": output_format},
        json={
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": stability,
                "similarity_boost": similarity_boost,
                "style": style,
                "use_speaker_boost": use_speaker_boost,
            },
        },
        timeout=timeout,
    )
    _raise_for_response(resp)
    return resp.content


def text_to_speech_to_file(api_base, api_key, text, voice_id, dest_path, **kwargs):
    audio_bytes = text_to_speech_bytes(api_base, api_key, text, voice_id, **kwargs)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(audio_bytes)
    return dest_path


def clone_voice(api_base, api_key, name, ref_audio_paths, description="", timeout=120):
    """Clones a new ElevenLabs voice from 1+ reference audio files (Instant Voice
    Cloning). Returns the created voice's `voice_id`.
    """
    _require_key(api_key)
    files = []
    opened = []
    try:
        for path in ref_audio_paths:
            fh = open(path, "rb")
            opened.append(fh)
            files.append(("files", (os.path.basename(path), fh)))
        data = {"name": name}
        if description:
            data["description"] = description
        resp = requests.post(
            f"{api_base}/v1/voices/add",
            headers=_headers(api_key),
            data=data,
            files=files,
            timeout=timeout,
        )
    finally:
        for fh in opened:
            fh.close()
    _raise_for_response(resp)
    return resp.json()
