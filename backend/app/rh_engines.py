"""RunningHub multi-engine adapters: Suno / Mureka / stem separation."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import settings
from app.providers import (
    download_url,
    poll_runninghub_task,
    runninghub_auth_headers,
    submit_runninghub_task,
    upload_runninghub_audio,
)


SUNO_SINGLE = "/openapi/v2/rhart-audio/suno-v5.5/single"
SUNO_CUSTOM = "/openapi/v2/rhart-audio/suno-v5.5/custom"
MUREKA_GENERATE = "/openapi/v2/mureka-ai/mureka-v9/generate-song"
MUREKA_EXTEND = "/openapi/v2/mureka-ai/mureka-v7.6/extend-song"
MUREKA_UPLOAD = "/openapi/v2/mureka-ai/files-upload"
EXTRACT_VOCAL = "/openapi/v2/rhart-audio/extract-vocal"
EXTRACT_BG = "/openapi/v2/rhart-audio/extract-background"
SOURCE_SEP = "/openapi/v2/rhart-audio/source-separation"


def _require_key() -> str:
    key = settings.runninghub_api_key.strip()
    if not key:
        raise RuntimeError("RUNNINGHUB_API_KEY 未配置")
    return key


def _collect_audio_urls(result: dict) -> list[str]:
    urls = []
    for item in result.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    return urls


def _suno_base() -> str:
    return (settings.runninghub_ai_base_url or "https://www.runninghub.ai").rstrip("/")


def generate_suno_single(*, description: str, title: str = "", instrumental: bool = False) -> dict:
    from app.providers import enrich_human_expression

    description = enrich_human_expression(
        description or "",
        {"instrumental": instrumental},
        lang="en",
    )
    payload = {
        "description": description[:400],
        "make_instrumental": "true" if instrumental else "false",
    }
    if title:
        payload["title"] = title[:80]
    submitted = submit_runninghub_task(SUNO_SINGLE, payload, base_url=_suno_base())
    api_key = _require_key()
    used = submitted.get("base_url") or _suno_base()
    full = poll_runninghub_task(api_key, submitted["task_id"], base_url=used)
    urls = _collect_audio_urls(full) or [submitted["audio_url"]]
    return {
        "task_id": submitted["task_id"],
        "audio_urls": urls,
        "audio_url": urls[0],
        "model": "suno-v5.5",
        "lyrics": "",
    }


def generate_suno_custom(*, title: str, lyrics: str, tags: str) -> dict:
    human_tags = (
        "intimate human vocal, emotional phrasing, breathy storytelling, dynamic dynamics, "
        "natural diction, avoid robotic AI vocal"
    )
    base_tags = (tags or "pop, melodic").strip()
    if "human vocal" not in base_tags.lower() and "intimate" not in base_tags.lower():
        base_tags = f"{base_tags}, {human_tags}"
    payload = {
        "title": (title or "未命名")[:80],
        "prompt": (lyrics or "")[:5000],
        "tags": base_tags[:1000],
    }
    if not payload["prompt"].strip():
        raise RuntimeError("Suno 自定义模式需要歌词")
    submitted = submit_runninghub_task(SUNO_CUSTOM, payload, base_url=_suno_base())
    api_key = _require_key()
    used = submitted.get("base_url") or _suno_base()
    full = poll_runninghub_task(api_key, submitted["task_id"], base_url=used)
    urls = _collect_audio_urls(full) or [submitted["audio_url"]]
    return {
        "task_id": submitted["task_id"],
        "audio_urls": urls,
        "audio_url": urls[0],
        "model": "suno-v5.5-custom",
        "lyrics": lyrics,
    }


def generate_mureka_v9(
    *,
    lyrics: str,
    prompt: str = "",
    n: int = 2,
    reference_id: str = "",
    vocal_id: str = "",
    melody_id: str = "",
) -> dict:
    payload: dict = {
        "lyrics": (lyrics or "")[:3000],
        "n": max(1, min(int(n or 2), 3)),
        "stream": False,
    }
    if prompt:
        from app.providers import enrich_human_expression

        payload["prompt"] = enrich_human_expression(prompt, {"instrumental": False}, lang="auto")[:1024]
    if reference_id:
        payload["referenceId"] = reference_id
    if vocal_id:
        payload["vocalId"] = vocal_id
    if melody_id:
        payload["melodyId"] = melody_id
    if not payload["lyrics"].strip():
        raise RuntimeError("Mureka 需要歌词")
    submitted = submit_runninghub_task(MUREKA_GENERATE, payload)
    api_key = _require_key()
    full = poll_runninghub_task(api_key, submitted["task_id"])
    urls = _collect_audio_urls(full) or [submitted["audio_url"]]
    return {
        "task_id": submitted["task_id"],
        "audio_urls": urls,
        "audio_url": urls[0],
        "model": "mureka-v9",
        "lyrics": lyrics,
    }


def extend_mureka(*, lyrics: str, file_url: str, extend_at_ms: int = 0) -> dict:
    payload = {
        "lyrics": (lyrics or "")[:3000],
        "fileUrl": file_url,
        "extendAt": max(0, int(extend_at_ms or 0)),
    }
    submitted = submit_runninghub_task(MUREKA_EXTEND, payload)
    api_key = _require_key()
    full = poll_runninghub_task(api_key, submitted["task_id"])
    urls = _collect_audio_urls(full) or [submitted["audio_url"]]
    return {
        "task_id": submitted["task_id"],
        "audio_urls": urls,
        "audio_url": urls[0],
        "model": "mureka-v7.6-extend",
        "lyrics": lyrics,
    }


def separate_audio_runninghub(audio_path: Path, mode: str = "vocal") -> dict:
    """mode: vocal | background | full.

    RH extract-* 要求 ``videoUrl``（MP4/MOV）。纯音频会先经 audio-worker 封成短视频再上传。
    """
    upload_path = audio_path
    if audio_path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        wrapped = audio_path.with_suffix(".rh-wrap.mp4")
        result = audio_worker_post(
            "/edit",
            {
                "op": "to_video",
                "input_path": str(audio_path),
                "output_path": str(wrapped),
                "options": {},
            },
            timeout=600,
        )
        upload_path = Path(result["output_path"])
    source_url = upload_runninghub_audio(upload_path)
    if mode == "background":
        route = EXTRACT_BG
    elif mode == "full":
        route = SOURCE_SEP
    else:
        route = EXTRACT_VOCAL
    payload = {"videoUrl": source_url}
    submitted = submit_runninghub_task(route, payload)
    api_key = _require_key()
    used = submitted.get("base_url") or settings.runninghub_base_url
    full = poll_runninghub_task(api_key, submitted["task_id"], base_url=used)
    urls = _collect_audio_urls(full) or [submitted["audio_url"]]
    return {
        "task_id": submitted["task_id"],
        "audio_urls": urls,
        "audio_url": urls[0],
        "model": f"rhart-audio-{mode}",
        "source_url": source_url,
    }


def audio_worker_post(path: str, payload: dict, timeout: int = 600) -> dict:
    base = settings.audio_worker_url.rstrip("/")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")[:240]
        raise RuntimeError(f"audio-worker HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接 audio-worker: {error}") from error


def audio_worker_health() -> bool:
    base = settings.audio_worker_url.rstrip("/")
    try:
        with urlopen(f"{base}/health", timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            return bool(data.get("ok"))
    except Exception:  # noqa: BLE001
        return False


def download_many(urls: list[str], directory: Path, prefix: str = "out") -> list[Path]:
    paths = []
    for index, url in enumerate(urls):
        target = directory / f"{prefix}-{index}.mp3"
        download_url(url, target)
        paths.append(target)
    return paths
