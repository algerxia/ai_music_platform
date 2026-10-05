import json
import re
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.config import settings


RUNNINGHUB_MODELS = {
    "minimax-music-2.6": {
        "vocal": "/openapi/v2/minimax/music-2.6/text-to-music",
        "instrumental": "/openapi/v2/minimax/music-2.6/text-to-instrumental",
    },
    "minimax-music-2.5": {
        "vocal": "/openapi/v2/rhart-audio/text-to-audio/music-2.5",
        "instrumental": "/openapi/v2/rhart-audio/text-to-audio/music-2.5",
    },
}

# MiniMax / RunningHub Music 2.5 hard limits (studio 创作描述 can be longer).
MUSIC_PROMPT_MAX = 2000
MUSIC_LYRICS_MAX = 3500
INSTRUMENTAL_LYRICS = "[Intro]\n[Inst]\n[Outro]"


def compact_music_prompt(prompt: str, context: dict | None = None) -> str:
    text = re.sub(r"\*+", "", prompt or "")
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    extras = []
    ctx = context or {}
    if ctx.get("instrumental"):
        extras.append("纯音乐, instrumental, no vocals")
    genres = ctx.get("genre") or []
    if isinstance(genres, list) and genres:
        extras.append("、".join(str(item) for item in genres[:4] if item))
    if ctx.get("bpm"):
        extras.append(f"{ctx['bpm']}BPM")
    if ctx.get("key"):
        extras.append(str(ctx["key"]))
    if ctx.get("mood"):
        extras.append(str(ctx["mood"])[:40])
    if ctx.get("negative_prompt"):
        extras.append(f"避免: {str(ctx['negative_prompt'])[:80]}")
    prefix = "，".join(part for part in extras if part).strip()
    if prefix:
        budget = MUSIC_PROMPT_MAX - len(prefix) - 1
        body = text[: max(0, budget)]
        combined = f"{prefix}\n{body}".strip() if body else prefix
    else:
        combined = text
    if len(combined) <= MUSIC_PROMPT_MAX:
        return combined
    cut = combined[:MUSIC_PROMPT_MAX]
    if "\n" in cut[800:]:
        cut = cut.rsplit("\n", 1)[0]
    return cut.strip()


def compact_music_lyrics(lyrics: str, instrumental: bool) -> str:
    text = (lyrics or "").strip()
    if instrumental:
        return text[:MUSIC_LYRICS_MAX] if text else INSTRUMENTAL_LYRICS
    if len(text) > MUSIC_LYRICS_MAX:
        text = text[:MUSIC_LYRICS_MAX]
        if "\n" in text:
            text = text.rsplit("\n", 1)[0]
    return text


def runninghub_failure_message(raw: str) -> str:
    text = str(raw or "")
    if "Invalid parameters" in text or "请求参数校验失败" in text:
        return (
            "MiniMax 拒绝了当前参数：风格描述最长 2000 字；普通生成歌词最长 3500 字；Music Cover 歌词最长 1000 字。"
            "创作台可写更长文案，提交时会自动压缩。"
        )
    return text[:240]


COVER_LYRICS_MIN = 10
COVER_LYRICS_MAX = 1000
MUSIC_COVER_ROUTE = "/openapi/v2/minimax/music-cover"
MUSIC_UPLOAD_ROUTE = "/openapi/v2/media/upload/binary"
MAX_COVER_AUDIO_BYTES = 50 * 1024 * 1024


def compact_cover_lyrics(lyrics: str) -> str:
    text = compact_music_lyrics(lyrics or "", False).strip()
    if len(text) > COVER_LYRICS_MAX:
        text = text[:COVER_LYRICS_MAX]
        if "\n" in text:
            text = text.rsplit("\n", 1)[0]
    if len(text) < COVER_LYRICS_MIN:
        raise RuntimeError(f"Music Cover 歌词需要 {COVER_LYRICS_MIN}–{COVER_LYRICS_MAX} 字")
    return text


def runninghub_auth_headers(api_key: str, json_body: bool = True) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def post_json(url: str, payload: dict, headers: dict, timeout: int = 120) -> dict:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_multipart_file(url: str, api_key: str, path: Path, content_type: str = "audio/mpeg") -> dict:
    data = path.read_bytes()
    if len(data) > MAX_COVER_AUDIO_BYTES:
        raise RuntimeError("参考音频超过 50MB，RunningHub 无法作为 Music Cover 输入")
    boundary = f"----StudioRH{uuid.uuid4().hex}"
    filename = path.name or "reference.mp3"
    preamble = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    body = preamble + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_runninghub_audio(path: Path) -> str:
    api_key = settings.runninghub_api_key.strip()
    if not api_key:
        raise RuntimeError("RUNNINGHUB_API_KEY 未配置")
    base = settings.runninghub_base_url.rstrip("/")
    try:
        payload = post_multipart_file(base + MUSIC_UPLOAD_ROUTE, api_key, path)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")[:200]
        raise RuntimeError(f"RunningHub 上传失败 HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接 RunningHub 上传接口: {error}") from error
    code = payload.get("code")
    if code not in (0, "0", None) and payload.get("data") is None:
        raise RuntimeError(str(payload.get("message") or "RunningHub 上传被拒绝")[:200])
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    url = str((nested or {}).get("download_url") or "").strip()
    if url and not url.startswith("http"):
        url = f"{base}/{url.lstrip('/')}"
    if not url:
        raise RuntimeError("RunningHub 上传成功但未返回 download_url")
    return url


def submit_runninghub_task(route: str, payload: dict) -> dict:
    api_key = settings.runninghub_api_key.strip()
    if not api_key:
        raise RuntimeError("RUNNINGHUB_API_KEY 未配置")
    base = settings.runninghub_base_url.rstrip("/")
    headers = runninghub_auth_headers(api_key)
    try:
        submitted = post_json(base + route, payload, headers, timeout=60)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")[:200]
        raise RuntimeError(f"RunningHub HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接 RunningHub: {error}") from error

    task_id = str(submitted.get("taskId") or "").strip()
    error_code = str(submitted.get("errorCode") or "").strip()
    error_message = str(submitted.get("errorMessage") or "").strip()
    if not task_id:
        if error_code == "1014":
            raise RuntimeError("RunningHub 标准模型 API 需要企业级-共享 API Key")
        raise RuntimeError(f"RunningHub 拒绝请求（{error_code or 'unknown'}）{error_message[:160]}")
    if str(submitted.get("status", "")).upper() == "SUCCESS" and submitted.get("results"):
        result = submitted
    else:
        result = poll_runninghub_task(api_key, task_id)
    audio_url = ""
    for item in result.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            audio_url = item["url"]
            break
    if not audio_url:
        raise RuntimeError("RunningHub 没有返回可下载的音频地址")
    return {"task_id": task_id, "audio_url": audio_url}


def cover_music_runninghub(*, prompt: str, lyrics: str, audio_path: Path, fmt: str = "mp3") -> dict:
    ctx = {"instrumental": False}
    style = compact_music_prompt(prompt, ctx)
    cover_prompt = compact_music_prompt(
        f"{style}\n保留参考音频的旋律与编曲骨架，加入贴合歌词的人声演唱，不要改成纯伴奏。",
        ctx,
    )
    cover_lyrics = compact_cover_lyrics(lyrics)
    source_url = upload_runninghub_audio(audio_path)
    payload = {
        "prompt": cover_prompt,
        "audioUrl": source_url,
        "lyrics": cover_lyrics,
        "sampleRate": "44100",
        "format": "mp3" if fmt == "mp3" else "mp3",
        "bitrate": "256000",
    }
    submitted = submit_runninghub_task(MUSIC_COVER_ROUTE, payload)
    return {
        "task_id": submitted["task_id"],
        "audio_url": submitted["audio_url"],
        "model": "minimax-music-cover",
        "lyrics": cover_lyrics,
        "source_audio_url": source_url,
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def is_trusted_audio_host(hostname: str | None) -> bool:
    return bool(hostname) and (
        hostname.endswith(".myqcloud.com")
        or hostname.endswith(".runninghub.cn")
        or hostname.endswith(".runninghub.ai")
        or hostname.endswith(".aliyuncs.com")
    )


def download_url(url: str, target: Path, timeout: int = 120) -> int:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not is_trusted_audio_host(parsed.hostname):
        raise ValueError("untrusted audio url")
    with urlopen(Request(url, headers={"User-Agent": "StudioAI/2.0"}), timeout=timeout) as response:
        written = 0
        with target.open("wb") as handle:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > 100 * 1024 * 1024:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise ValueError("audio too large")
                handle.write(chunk)
    return written


def poll_runninghub_task(api_key: str, task_id: str, timeout_seconds: int = 600) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    base = settings.runninghub_base_url.rstrip("/")
    deadline = time.time() + timeout_seconds
    last = {}
    while time.time() < deadline:
        last = post_json(f"{base}/openapi/v2/query", {"taskId": task_id}, headers, timeout=60)
        status = str(last.get("status", "")).upper()
        if status == "SUCCESS":
            return last
        if status == "FAILED":
            raise RuntimeError(runninghub_failure_message(last.get("errorMessage") or last.get("failedReason") or "RunningHub 任务失败"))
        time.sleep(3)
    raise TimeoutError("RunningHub 音乐生成超时")


def generate_music_runninghub(*, prompt: str, lyrics: str, instrumental: bool, fmt: str = "mp3", context: dict | None = None) -> dict:
    api_key = settings.runninghub_api_key.strip()
    if not api_key:
        raise RuntimeError("RUNNINGHUB_API_KEY 未配置")
    model = settings.music_model.strip() or "minimax-music-2.5"
    if model not in RUNNINGHUB_MODELS:
        model = "minimax-music-2.5"
    route = RUNNINGHUB_MODELS[model]["instrumental" if instrumental else "vocal"]
    ctx = dict(context or {})
    ctx["instrumental"] = instrumental
    prompt = compact_music_prompt(prompt, ctx)
    lyrics = compact_music_lyrics(lyrics, instrumental)
    if instrumental and model == "minimax-music-2.5":
        payload = {
            "prompt": prompt,
            "lyrics": lyrics,
            "sampleRate": "44100",
            "bitrate": "256000",
        }
    elif instrumental:
        payload = {
            "prompt": prompt,
            "sampleRate": "44100",
            "bitrate": "256000",
            "format": fmt,
        }
    else:
        if not lyrics.strip():
            raise RuntimeError("人声模式需要歌词")
        if model == "minimax-music-2.5":
            payload = {
                "prompt": prompt,
                "lyrics": lyrics,
                "sampleRate": "44100",
                "bitrate": "256000",
            }
        else:
            payload = {
                "prompt": prompt,
                "lyrics": lyrics,
                "sampleRate": "44100",
                "bitrate": "256000",
                "format": fmt,
                "lyricsOptimizer": False,
            }
    submitted = submit_runninghub_task(route, payload)
    return {
        "task_id": submitted["task_id"],
        "audio_url": submitted["audio_url"],
        "model": model,
        "lyrics": lyrics,
    }


def qwen_chat(messages: list[dict], temperature: float = 0.7) -> str:
    api_key = settings.dashscope_api_key.strip()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")
    payload = {
        "model": settings.dashscope_model,
        "messages": messages,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = post_json(f"{settings.dashscope_base_url.rstrip('/')}/chat/completions", payload, headers, timeout=90)
    return str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


def write_lyrics(
    prompt: str,
    *,
    duration_seconds: int = 90,
    bpm: int | None = None,
    key: str = "",
    mood: str = "",
    structure: str = "",
    genre: list | str = "",
    existing_lyrics: str = "",
    instruction: str = "",
    title: str = "",
) -> str:
    genre_text = "、".join(genre) if isinstance(genre, list) else str(genre or "")
    duration = max(30, min(int(duration_seconds or 90), 240))
    verse_hint = "两段主歌 + 副歌反复 + 桥段" if duration >= 120 else "主歌 + 副歌即可"
    system = (
        "你是华语影视/流行作词人。只输出歌词正文，不要标题、不要解说、不要 markdown。"
        "必须使用结构标签，优先：[Intro]、[Verse]、[Pre Chorus]、[Chorus]、[Bridge]、[Outro]。"
        "歌词要能跟着已完成的器乐走，句式长短适合演唱，避免口号堆砌，不要抄袭成名作品。"
        "总字数尽量不超过 900，以便后续 Music Cover 提交（上限 1000 字）。"
        f"目标时长约 {duration} 秒，结构用{verse_hint}。"
    )
    user = (
        f"曲名/项目：{title or '未命名'}\n"
        f"风格：{genre_text or '未指定'}\n"
        f"BPM：{bpm or '未知'}；调性：{key or '未知'}；情绪：{mood or '未指定'}；段落：{structure or '未指定'}\n"
        f"补充要求：{instruction.strip() or '无'}\n"
        f"已有歌词（若有则在其基础上改写，没有则全新创作）：\n{existing_lyrics.strip() or '无'}\n\n"
        f"音乐描述：\n{prompt[:6000]}"
    )
    text = qwen_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.75,
    )
    cleaned = re.sub(r"^```(?:\w+)?\s*", "", (text or "").strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if len(cleaned) > MUSIC_LYRICS_MAX:
        cleaned = cleaned[:MUSIC_LYRICS_MAX]
        if "\n" in cleaned:
            cleaned = cleaned.rsplit("\n", 1)[0]
    return cleaned or "[Verse]\n跟着心跳往前走\n[Chorus]\n把今天唱成歌"


ALLOWED_KEYS = ["D minor", "C major", "A minor", "G major", "E minor", "F major", "自由调性"]
ALLOWED_DURATIONS = [30, 60, 90, 120, 180]
ALLOWED_SIGNATURES = ["4/4", "3/4", "6/8"]


def _nearest_duration(value: int) -> int:
    return min(ALLOWED_DURATIONS, key=lambda item: abs(item - int(value or 90)))


def _match_key(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "D minor"
    lowered = text.lower().replace("小调", " minor").replace("大调", " major")
    for key in ALLOWED_KEYS:
        if key.lower() == lowered or key.lower() in lowered:
            return key
    if "自由" in text or "atonal" in lowered:
        return "自由调性"
    if "minor" in lowered or "小调" in (raw or ""):
        for key in ALLOWED_KEYS:
            if "minor" in key.lower() and key[0].lower() in lowered:
                return key
        return "D minor"
    if "major" in lowered or "大调" in (raw or ""):
        for key in ALLOWED_KEYS:
            if "major" in key.lower() and key[0].lower() in lowered:
                return key
        return "C major"
    return "D minor"


def clamp_controls(raw: dict) -> dict:
    bpm = int(raw.get("bpm") or 90)
    bpm = max(40, min(240, bpm))
    vocal = "vocal" if str(raw.get("vocal") or "").lower() in {"vocal", "sung", "人声", "演唱"} else "instrumental"
    if str(raw.get("instrumental")).lower() in {"true", "1"}:
        vocal = "instrumental"
    genres = raw.get("genre") or []
    if isinstance(genres, str):
        genres = [part.strip() for part in re.split(r"[,/、，]", genres) if part.strip()]
    genres = [str(item).strip()[:24] for item in genres if str(item).strip()][:6]
    signature = str(raw.get("time_signature") or "4/4")
    if signature not in ALLOWED_SIGNATURES:
        signature = "4/4"
    lyrics = str(raw.get("lyrics") or "")[:3500]
    if vocal == "vocal" and not lyrics.strip() and raw.get("lyrics_draft"):
        lyrics = str(raw.get("lyrics_draft"))[:3500]
    return {
        "genre": genres or ["Cinematic"],
        "bpm": bpm,
        "key": _match_key(str(raw.get("key") or "")),
        "time_signature": signature,
        "duration_seconds": _nearest_duration(int(raw.get("duration_seconds") or 90)),
        "vocal": vocal,
        "mood": str(raw.get("mood") or "")[:120],
        "structure": str(raw.get("structure") or "")[:255],
        "negative_prompt": str(raw.get("negative_prompt") or "")[:500],
        "lyrics": lyrics,
        "rationale": str(raw.get("rationale") or "")[:400],
    }


def heuristic_controls(prompt: str) -> dict:
    text = prompt or ""
    bpm_match = re.search(r"(\d{2,3})\s*(?:bpm|BPM|拍)", text)
    bpm = int(bpm_match.group(1)) if bpm_match else 86
    vocal = "instrumental"
    if re.search(r"人声|演唱|歌词|戏腔|合唱|女声|男声|vocal", text, re.I):
        vocal = "vocal"
    if re.search(r"纯音乐|器乐|无人声|instrumental", text, re.I):
        vocal = "instrumental"
    genres = []
    mapping = [
        (r"国风|戏曲|京剧|昆曲|戏腔", "国风"),
        (r"电影|cinematic|配乐", "Cinematic"),
        (r"ambient|氛围", "Ambient"),
        (r"soul|rhodes|放松", "Neo-soul"),
        (r"电子|pulse|synth", "实验电子"),
    ]
    for pattern, label in mapping:
        if re.search(pattern, text, re.I):
            genres.append(label)
    duration = 90
    if re.search(r"3\s*分钟|180", text):
        duration = 180
    elif re.search(r"2\s*分钟|120", text):
        duration = 120
    elif re.search(r"1\s*分钟|60\s*秒", text):
        duration = 60
    signature = "6/8" if "6/8" in text else "3/4" if "3/4" in text else "4/4"
    lyrics = ""
    tagged = re.search(r"(\[(?:Intro|Verse|Chorus|Bridge|Outro|End)\][\s\S]{20,})", text, re.I)
    if tagged:
        lyrics = tagged.group(1)[:3500]
        vocal = "vocal"
    return clamp_controls(
        {
            "genre": genres,
            "bpm": bpm,
            "key": "D minor" if re.search(r"小调|哀|悲|痛", text) else "C major" if re.search(r"大调|明亮|欢", text) else "D minor",
            "time_signature": signature,
            "duration_seconds": duration,
            "vocal": vocal,
            "mood": "克制、叙事" if "叙事" in text else "",
            "structure": "Intro / Verse / Chorus / Bridge / Outro" if vocal == "vocal" else "Intro / Build / Outro",
            "negative_prompt": "不要念白、不要突然炸点" if "念白" in text or "戏腔" in text else "",
            "lyrics": lyrics,
            "rationale": "未配置 Qwen，已按文案关键词做规则推断。",
        }
    )


def parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object")
    return json.loads(cleaned[start : end + 1])


def parse_prompt_controls(prompt: str) -> dict:
    if not settings.dashscope_api_key.strip():
        data = heuristic_controls(prompt)
        data["source"] = "heuristic"
        data["model"] = ""
        return data
    raw_text = qwen_chat(
        [
            {
                "role": "system",
                "content": (
                    "你是音乐制作人的参数助理。只输出一个 JSON 对象，不要 markdown。"
                    "字段：genre(字符串数组,1-4个), bpm(40-240整数), key(必须是 "
                    "D minor/C major/A minor/G major/E minor/F major/自由调性 之一), "
                    "time_signature(4/4或3/4或6/8), duration_seconds(只能是30/60/90/120/180), "
                    "vocal(vocal或instrumental), mood, structure, negative_prompt, lyrics, rationale。"
                    "人声时 lyrics 必须是带 [Verse]/[Chorus] 等标签的完整歌词；纯音乐 lyrics 为空字符串。"
                    "negative_prompt 写应避免的元素。rationale 用一句中文说明判断依据。"
                ),
            },
            {"role": "user", "content": f"根据这段创作描述配置最合适的生成参数：\n{prompt[:6000]}"},
        ],
        temperature=0.2,
    )
    try:
        parsed = parse_json_object(raw_text)
    except Exception:
        parsed = heuristic_controls(prompt)
        parsed["rationale"] = (parsed.get("rationale") or "") + "（模型未返回合法 JSON，已回退规则推断）"
        parsed["source"] = "heuristic"
        parsed["model"] = settings.dashscope_model
        return parsed
    data = clamp_controls(parsed)
    data["source"] = "qwen"
    data["model"] = settings.dashscope_model
    return data
