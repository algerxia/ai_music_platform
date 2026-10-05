"""Studio AI JSON API server. Frontend assets are served separately."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse
import json
import math
import os
import re
import struct
import time
import uuid
import wave


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "data" / "assets"
ALLOWED_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}
TTS_MODEL = "qwen3-tts-flash"
TTS_VOICES = {"female": "Cherry", "male": "Ethan"}
RUNNINGHUB_MODELS = {
    "minimax-music-2.6": {
        "label": "MiniMax Music 2.6",
        "vocal": "/openapi/v2/minimax/music-2.6/text-to-music",
        "instrumental": "/openapi/v2/minimax/music-2.6/text-to-instrumental",
    },
    "minimax-music-2.5": {
        "label": "MiniMax Music 2.5",
        "vocal": "/openapi/v2/rhart-audio/text-to-audio/music-2.5",
        "instrumental": "/openapi/v2/rhart-audio/text-to-audio/music-2.5",
    },
}
DASHSCOPE_MUSIC_MODELS = ("fun-music-v1", "fun-music-preview")


def is_trusted_audio_host(hostname):
    return bool(hostname) and (
        hostname == "dashscope.aliyuncs.com"
        or hostname.endswith(".oss-cn-beijing.aliyuncs.com")
        or hostname.endswith(".myqcloud.com")
        or hostname.endswith(".runninghub.cn")
        or hostname.endswith(".runninghub.ai")
    )


def download_audio_file(audio_url, target, timeout=90):
    parsed = urlparse(audio_url)
    if parsed.scheme not in ("https", "http") or not is_trusted_audio_host(parsed.hostname):
        raise ValueError("untrusted audio url")
    with urlopen(Request(audio_url, headers={"User-Agent": "StudioAI/1.0"}), timeout=timeout) as audio_response:
        announced_size = int(audio_response.headers.get("Content-Length", "0"))
        if announced_size > 100 * 1024 * 1024:
            raise ValueError("audio too large")
        written = 0
        with target.open("wb") as audio_file:
            while True:
                chunk = audio_response.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > 100 * 1024 * 1024:
                    audio_file.close()
                    target.unlink(missing_ok=True)
                    raise ValueError("audio too large")
                audio_file.write(chunk)
    return written


def post_json(url, payload, headers, timeout=120):
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def poll_runninghub_task(base_url, api_key, task_id, timeout_seconds=300, interval_seconds=3):
    deadline = time.time() + timeout_seconds
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last = {}
    while time.time() < deadline:
        last = post_json(
            base_url.rstrip("/") + "/openapi/v2/query",
            {"taskId": task_id},
            headers,
            timeout=60,
        )
        status = str(last.get("status", "")).upper()
        if status == "SUCCESS":
            return last
        if status == "FAILED":
            message = last.get("errorMessage") or last.get("failedReason") or "RunningHub 任务失败"
            raise RuntimeError(str(message)[:240])
        time.sleep(interval_seconds)
    raise TimeoutError("RunningHub 音乐生成超时")


def synthesize_instrumental_wav(path, seconds=28, bpm=88):
    """Create a short local harmonic bed when Fun-Music is unavailable."""
    sample_rate = 44100
    total = int(sample_rate * seconds)
    beats = max(1, int(seconds * bpm / 60))
    notes = [261.63, 329.63, 392.00, 493.88, 392.00, 329.63]  # C major arpeggio color
    frames = bytearray()
    for index in range(total):
        beat = (index / sample_rate) * bpm / 60.0
        note = notes[int(beat) % len(notes)]
        t = index / sample_rate
        envelope = 0.22 * (0.55 + 0.45 * math.sin(2 * math.pi * beat / beats))
        sample = envelope * (
            0.55 * math.sin(2 * math.pi * note * t)
            + 0.25 * math.sin(2 * math.pi * note * 2 * t)
            + 0.12 * math.sin(2 * math.pi * (note * 0.5) * t)
        )
        frames.extend(struct.pack("<h", max(-32767, min(32767, int(sample * 32767)))))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)


def _pcm16_mul(frames: bytes, gain: float) -> bytes:
    samples = memoryview(frames).cast("h")
    out = bytearray(len(frames))
    out_samples = memoryview(out).cast("h")
    for index, sample in enumerate(samples):
        value = int(sample * gain)
        out_samples[index] = max(-32767, min(32767, value))
    return bytes(out)


def _pcm16_add(left: bytes, right: bytes) -> bytes:
    left_view = memoryview(left).cast("h")
    right_view = memoryview(right).cast("h")
    count = min(len(left_view), len(right_view))
    out = bytearray(count * 2)
    out_view = memoryview(out).cast("h")
    for index in range(count):
        value = int(left_view[index]) + int(right_view[index])
        out_view[index] = max(-32767, min(32767, value))
    return bytes(out)


def _mono_to_stereo(frames: bytes) -> bytes:
    samples = memoryview(frames).cast("h")
    out = bytearray(len(frames) * 2)
    out_samples = memoryview(out).cast("h")
    for index, sample in enumerate(samples):
        out_samples[index * 2] = sample
        out_samples[index * 2 + 1] = sample
    return bytes(out)


def mix_wav_files(vocal_path, bed_path, out_path, bed_gain=0.22):
    with wave.open(str(vocal_path), "rb") as vocal, wave.open(str(bed_path), "rb") as bed:
        if vocal.getsampwidth() != 2 or bed.getsampwidth() != 2:
            raise ValueError("expected 16-bit wav")
        rate = vocal.getframerate()
        if bed.getframerate() != rate:
            # Resample bed by naive nearest-neighbour to match vocal rate.
            bed_rate = bed.getframerate()
            bed_channels = bed.getnchannels()
            bed_raw = bed.readframes(bed.getnframes())
            bed_samples = memoryview(bed_raw).cast("h")
            ratio = rate / float(bed_rate)
            target_frames = int(len(bed_samples) / bed_channels * ratio)
            resampled = bytearray(target_frames * bed_channels * 2)
            out_view = memoryview(resampled).cast("h")
            for frame_index in range(target_frames):
                source = min(int(frame_index / ratio), (len(bed_samples) // bed_channels) - 1)
                for channel in range(bed_channels):
                    out_view[frame_index * bed_channels + channel] = bed_samples[source * bed_channels + channel]
            bed_frames = bytes(resampled)
            bed_nchannels = bed_channels
        else:
            bed_frames = bed.readframes(bed.getnframes())
            bed_nchannels = bed.getnchannels()
        vocal_frames = vocal.readframes(vocal.getnframes())
        channels = max(vocal.getnchannels(), bed_nchannels)
        if vocal.getnchannels() == 1 and channels == 2:
            vocal_frames = _mono_to_stereo(vocal_frames)
        if bed_nchannels == 1 and channels == 2:
            bed_frames = _mono_to_stereo(bed_frames)
        if len(bed_frames) < len(vocal_frames):
            repeats = (len(vocal_frames) // max(1, len(bed_frames))) + 1
            bed_frames = (bed_frames * repeats)[: len(vocal_frames)]
        else:
            bed_frames = bed_frames[: len(vocal_frames)]
        bed_frames = _pcm16_mul(bed_frames, bed_gain)
        mixed = _pcm16_add(vocal_frames, bed_frames)
    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(mixed)
    return rate, channels


def load_dotenv():
    """Load a simple project-local .env without overriding process variables."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            os.environ.setdefault(name, value)


load_dotenv()


class StudioHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if origin not in ALLOWED_ORIGINS:
            self.send_error(403, "Origin not allowed")
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def send_json(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/api/settings/providers":
            self.get_provider_settings()
            return
        if self.path == "/api/health":
            has_qwen = bool(os.environ.get("DASHSCOPE_API_KEY"))
            music_provider = os.environ.get("MUSIC_PROVIDER", "runninghub").strip().lower()
            if music_provider == "runninghub":
                music_configured = bool(os.environ.get("RUNNINGHUB_API_KEY"))
                music_model = os.environ.get("MUSIC_MODEL", "minimax-music-2.6")
            else:
                music_configured = bool(has_qwen)
                music_model = os.environ.get("MUSIC_MODEL", "fun-music-v1")
            self.send_json(200, {
                "ok": True,
                "qwen_configured": has_qwen,
                "qwen_provider": "阿里云百炼",
                "model": os.environ.get("DASHSCOPE_MODEL", "qwen-plus"),
                "audio_generation_configured": music_configured,
                "music_provider": music_provider if music_configured else music_provider,
                "music_model": music_model,
                "music_adapter_enabled": music_configured,
                "music_access_granted": None,
                "music_access_check": "not_available_from_credentials",
                "music_workspace_configured": bool(os.environ.get("DASHSCOPE_WORKSPACE_ID")),
            })
            return
        asset_match = re.fullmatch(r"/api/assets/([a-f0-9-]{36})\.(mp3|wav)", self.path)
        if asset_match:
            asset_path = ASSET_DIR / f"{asset_match.group(1)}.{asset_match.group(2)}"
            if not asset_path.is_file():
                self.send_json(404, {"error": {"code": "ASSET_NOT_FOUND", "message": "音频文件不存在。"}})
                return
            self.serve_audio(asset_path)
            return
        self.send_json(404, {"error": {"code": "NOT_FOUND", "message": "未找到该 API。"}})

    def serve_audio(self, path):
        size = path.stat().st_size
        content_type = "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
        range_header = self.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        start, end = 0, size - 1
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), size - 1)
            if start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        length = end - start + 1
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        with path.open("rb") as audio:
            audio.seek(start)
            remaining = length
            while remaining:
                chunk = audio.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self):
        if self.path == "/api/music/generate":
            self.generate_music()
            return
        if self.path == "/api/settings/providers":
            self.save_provider_settings()
            return
        if self.path != "/api/qwen/assist":
            self.send_json(404, {"error": {"code": "NOT_FOUND", "message": "未找到该 API。"}})
            return
        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            self.send_json(503, {"error": {
                "code": "QWEN_NOT_CONFIGURED",
                "message": "尚未配置 Qwen 服务。请在启动服务前设置 DASHSCOPE_API_KEY。",
            }})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                self.send_json(413, {"error": {"code": "INVALID_BODY", "message": "请求内容为空或超出限制。"}})
                return
            body = json.loads(self.rfile.read(length))
            prompt = str(body.get("prompt", "")).strip()
            if len(prompt) < 8 or len(prompt) > 6000:
                self.send_json(400, {"error": {"code": "INVALID_PROMPT", "message": "创作描述需为 8 到 6000 个字符。"}})
                return
            context = body.get("context", {})
            if not isinstance(context, dict):
                context = {}
            model = os.environ.get("DASHSCOPE_MODEL", "qwen-plus").strip() or "qwen-plus"
            api_url = os.environ.get(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ).rstrip("/") + "/chat/completions"
            system_message = (
                "你是面向专业音乐制作人的创作副驾。根据用户的原始音乐构想，输出简洁、可供音乐生成模型理解的中文创作描述。"
                "保留用户意图，不擅自添加艺术家姓名或要求模仿特定艺人；具体包含风格、情绪、节奏、音色、段落和制作质感。"
                "不要声称已经生成音频。只输出两段：第一段以‘创作描述：’开头，第二段以‘制作建议：’开头；总长度不超过 220 个中文字符。"
            )
            user_message = {
                "prompt": prompt,
                "context": {
                    "genre": context.get("genre", [])[:8] if isinstance(context.get("genre", []), list) else [],
                    "bpm": context.get("bpm"),
                    "key": context.get("key"),
                    "duration_seconds": context.get("duration_seconds"),
                    "vocal": context.get("vocal"),
                    "mood": str(context.get("mood", ""))[:120],
                    "structure": str(context.get("structure", ""))[:120],
                },
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": json.dumps(user_message, ensure_ascii=False)},
                ],
                "temperature": 0.65,
                "max_tokens": 420,
            }
            request = Request(
                api_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=45) as response:
                provider_data = json.loads(response.read().decode("utf-8"))
            content = provider_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not isinstance(content, str) or not content.strip():
                self.send_json(502, {"error": {"code": "EMPTY_MODEL_RESPONSE", "message": "Qwen 没有返回可用建议，请稍后重试。"}})
                return
            self.send_json(200, {
                "data": {
                    "text": content.strip(),
                    "model": model,
                    "provider": "qwen",
                    "request_id": provider_data.get("id", ""),
                }
            })
        except HTTPError as error:
            detail = ""
            try:
                provider_error = json.loads(error.read().decode("utf-8"))
                detail = provider_error.get("message") or provider_error.get("error", {}).get("message", "")
            except Exception:
                pass
            print(f"Qwen provider returned HTTP {error.code}.")
            if error.code in (401, 403):
                self.send_json(502, {"error": {"code": "QWEN_AUTH_FAILED", "message": "Qwen 凭据无效或无调用权限。请检查服务端环境变量。"}})
            elif error.code == 429:
                self.send_json(429, {"error": {"code": "QWEN_RATE_LIMITED", "message": "Qwen 服务繁忙，请稍后重试。"}})
            else:
                self.send_json(502, {"error": {"code": "QWEN_PROVIDER_ERROR", "message": "Qwen 暂时无法完成请求。" if not detail else f"Qwen 调用失败：{detail[:180]}"}})
        except (URLError, TimeoutError):
            self.send_json(504, {"error": {"code": "QWEN_TIMEOUT", "message": "连接 Qwen 超时，请稍后重试。"}})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": {"code": "INVALID_JSON", "message": "请求格式无效。"}})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            print(f"Qwen request failed: {type(error).__name__}")
            self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "创作副驾暂时不可用。"}})

    def get_provider_settings(self):
        has_qwen = bool(os.environ.get("DASHSCOPE_API_KEY"))
        has_workspace = bool(os.environ.get("DASHSCOPE_WORKSPACE_ID"))
        music_provider = os.environ.get("MUSIC_PROVIDER", "runninghub").strip().lower()
        music_configured = bool(os.environ.get("RUNNINGHUB_API_KEY")) if music_provider == "runninghub" else has_qwen
        default_music_model = "minimax-music-2.6" if music_provider == "runninghub" else "fun-music-v1"
        self.send_json(200, {"data": {
            "providers": [
                {"id": "runninghub", "label": "RunningHub（MiniMax Music）"},
                {"id": "dashscope", "label": "阿里云百炼（Fun-Music）"},
            ],
            "qwen": {
                "provider": os.environ.get("QWEN_PROVIDER", "dashscope"),
                "base_url": os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                "model": os.environ.get("DASHSCOPE_MODEL", "qwen-plus"),
                "configured": has_qwen,
            },
            "music": {
                "provider": music_provider,
                "model": os.environ.get("MUSIC_MODEL", default_music_model),
                "configured": music_configured,
                "endpoint_mode": "runninghub" if music_provider == "runninghub" else ("workspace" if has_workspace else "dashscope"),
                "workspace_configured": has_workspace,
                "runninghub_base_url": os.environ.get("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn"),
            },
            "settings_source": "server_environment",
        }})

    def save_provider_settings(self):
        body = self._consume_json_body(4_096)
        if body is None:
            return
        qwen_model = str(body.get("qwen_model", "")).strip()
        qwen_provider = str(body.get("qwen_provider", "dashscope")).strip().lower()
        qwen_base_url = str(body.get("qwen_base_url", "")).strip().rstrip("/")
        music_model = str(body.get("music_model", "")).strip()
        music_provider = str(body.get("music_provider", "runninghub")).strip().lower()
        if music_provider not in ("dashscope", "runninghub"):
            self.send_json(400, {"error": {"code": "UNSUPPORTED_MUSIC_PROVIDER", "message": "音乐服务商仅支持 RunningHub 或阿里云百炼。"}})
            return
        if qwen_provider != "dashscope":
            self.send_json(400, {"error": {"code": "UNSUPPORTED_QWEN_PROVIDER", "message": "当前版本的 Qwen 服务商请选择阿里云百炼。"}})
            return
        if qwen_model and not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", qwen_model):
            self.send_json(400, {"error": {"code": "INVALID_QWEN_MODEL", "message": "Qwen 模型名称格式无效。"}})
            return
        if music_provider == "runninghub":
            if music_model and music_model not in RUNNINGHUB_MODELS:
                self.send_json(400, {"error": {"code": "INVALID_MUSIC_MODEL", "message": "RunningHub 音乐模型仅支持 minimax-music-2.6 或 minimax-music-2.5。"}})
                return
        elif music_model and music_model not in DASHSCOPE_MUSIC_MODELS:
            self.send_json(400, {"error": {"code": "INVALID_MUSIC_MODEL", "message": "百炼音乐模型仅支持 fun-music-v1 或 fun-music-preview。"}})
            return
        changes = {}
        if qwen_base_url:
            parsed_base = urlparse(qwen_base_url)
            if parsed_base.scheme != "https" or parsed_base.hostname not in ("dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"):
                self.send_json(400, {"error": {"code": "INVALID_QWEN_ENDPOINT", "message": "Qwen 地址须使用百炼官方 HTTPS 接口域名。"}})
                return
            changes["DASHSCOPE_BASE_URL"] = qwen_base_url
        if qwen_model:
            changes["DASHSCOPE_MODEL"] = qwen_model
        changes["QWEN_PROVIDER"] = qwen_provider
        if music_model:
            changes["MUSIC_MODEL"] = music_model
        if music_provider:
            changes["MUSIC_PROVIDER"] = music_provider
        if "api_key" in body:
            api_key = str(body.get("api_key", "")).strip()
            if len(api_key) > 512 or any(char.isspace() for char in api_key):
                self.send_json(400, {"error": {"code": "INVALID_API_KEY", "message": "百炼 API Key 格式无效。"}})
                return
            if api_key:
                changes["DASHSCOPE_API_KEY"] = api_key
        if "runninghub_api_key" in body:
            rh_key = str(body.get("runninghub_api_key", "")).strip()
            if len(rh_key) > 512 or any(char.isspace() for char in rh_key):
                self.send_json(400, {"error": {"code": "INVALID_API_KEY", "message": "RunningHub API Key 格式无效。"}})
                return
            if rh_key:
                changes["RUNNINGHUB_API_KEY"] = rh_key
        if "runninghub_base_url" in body:
            rh_base = str(body.get("runninghub_base_url", "")).strip().rstrip("/")
            if rh_base:
                parsed_rh = urlparse(rh_base)
                if parsed_rh.scheme != "https" or parsed_rh.hostname not in ("www.runninghub.cn", "www.runninghub.ai", "runninghub.cn", "runninghub.ai"):
                    self.send_json(400, {"error": {"code": "INVALID_RUNNINGHUB_ENDPOINT", "message": "RunningHub 地址须使用官方 HTTPS 域名。"}})
                    return
                changes["RUNNINGHUB_BASE_URL"] = rh_base
        if "workspace_id" in body:
            workspace_id = str(body.get("workspace_id", "")).strip()
            if workspace_id and not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", workspace_id):
                self.send_json(400, {"error": {"code": "INVALID_WORKSPACE_ID", "message": "百炼业务空间 ID 格式无效。"}})
                return
            if workspace_id:
                changes["DASHSCOPE_WORKSPACE_ID"] = workspace_id
        if not changes:
            self.send_json(400, {"error": {"code": "EMPTY_SETTINGS", "message": "没有可保存的设置。"}})
            return
        env_path = ROOT / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
        pending = dict(changes)
        for index, line in enumerate(lines):
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key = line.split("=", 1)[0].strip()
            if key in pending:
                lines[index] = f"{key}={pending.pop(key)}"
        lines.extend(f"{key}={value}" for key, value in pending.items())
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            env_path.chmod(0o600)
        except OSError:
            pass
        os.environ.update(changes)
        music_provider_now = os.environ.get("MUSIC_PROVIDER", "runninghub")
        music_configured = bool(os.environ.get("RUNNINGHUB_API_KEY")) if music_provider_now == "runninghub" else bool(os.environ.get("DASHSCOPE_API_KEY"))
        self.send_json(200, {"data": {
            "saved": True,
            "qwen_model": os.environ.get("DASHSCOPE_MODEL", "qwen-plus"),
            "qwen_base_url": os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "music_provider": music_provider_now,
            "music_model": os.environ.get("MUSIC_MODEL", "minimax-music-2.6"),
            "qwen_configured": bool(os.environ.get("DASHSCOPE_API_KEY")),
            "music_configured": music_configured,
            "key_saved": "DASHSCOPE_API_KEY" in changes or "RUNNINGHUB_API_KEY" in changes,
        }})

    def _consume_json_body(self, max_length=16_384):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > max_length:
                self.send_json(413, {"error": {"code": "INVALID_BODY", "message": "请求内容为空或超出限制。"}})
                return None
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": {"code": "INVALID_JSON", "message": "请求格式无效。"}})
            return None
        except ValueError:
            self.send_json(400, {"error": {"code": "INVALID_JSON", "message": "请求内容必须是 JSON 对象。"}})
            return None

    def _write_music_success(self, *, asset_id, fmt, duration_seconds=None, sample_rate=None, channels=None, lyrics="", request_id="", model=""):
        self.send_json(200, {"data": {
            "asset_id": asset_id,
            "audio_url": f"/api/assets/{asset_id}.{fmt}",
            "format": fmt,
            "duration_seconds": duration_seconds,
            "sample_rate": sample_rate,
            "channels": channels,
            "lyrics": lyrics,
            "request_id": request_id,
            "model": model,
        }})

    def _compose_song_via_bailian_tts(self, *, api_key, prompt_text, instrumental, gender, preferred_fmt):
        """Fallback when Fun-Music invite is unavailable: Qwen lyrics + Qwen-TTS vocal (+ local bed)."""
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        asset_id = str(uuid.uuid4())
        lyrics = ""
        request_id = ""
        model_name = TTS_MODEL
        if instrumental:
            target = ASSET_DIR / f"{asset_id}.wav"
            synthesize_instrumental_wav(target, seconds=32, bpm=84)
            return {
                "asset_id": asset_id,
                "fmt": "wav",
                "duration_seconds": 32,
                "sample_rate": 44100,
                "channels": 1,
                "lyrics": "",
                "request_id": "",
                "model": "local-instrumental-fallback",
            }

        lyrics_payload = {
            "model": os.environ.get("DASHSCOPE_MODEL", "qwen-plus").strip() or "qwen-plus",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是华语流行作词人。根据用户的音乐构想，写一首可直接演唱的短歌歌词。"
                        "使用 [verse]/[chorus] 标记，中文为主，总长度 80 到 160 个汉字，不要解释。"
                    ),
                },
                {"role": "user", "content": prompt_text},
            ],
            "temperature": 0.8,
            "max_tokens": 500,
        }
        chat_url = os.environ.get(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/") + "/chat/completions"
        with urlopen(
            Request(
                chat_url,
                data=json.dumps(lyrics_payload, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            ),
            timeout=60,
        ) as response:
            chat_data = json.loads(response.read().decode("utf-8"))
        lyrics = str(chat_data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        if not lyrics:
            raise RuntimeError("empty lyrics")
        speak_text = re.sub(r"\[(?:verse|chorus|bridge|intro|outro)\]", "。", lyrics, flags=re.I)
        speak_text = re.sub(r"\s+", " ", speak_text).strip()[:220]
        voice = TTS_VOICES.get(gender if gender in TTS_VOICES else "female", "Cherry")
        tts_payload = {
            "model": TTS_MODEL,
            "input": {"text": speak_text, "voice": voice, "language_type": "Chinese"},
        }
        with urlopen(
            Request(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
                data=json.dumps(tts_payload, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            ),
            timeout=120,
        ) as response:
            tts_data = json.loads(response.read().decode("utf-8"))
        request_id = str(tts_data.get("request_id", ""))
        audio_url = ((tts_data.get("output") or {}).get("audio") or {}).get("url", "")
        vocal_path = ASSET_DIR / f"{asset_id}.vocal.wav"
        bed_path = ASSET_DIR / f"{asset_id}.bed.wav"
        final_path = ASSET_DIR / f"{asset_id}.wav"
        download_audio_file(audio_url, vocal_path)
        synthesize_instrumental_wav(bed_path, seconds=36, bpm=86)
        sample_rate, channels = mix_wav_files(vocal_path, bed_path, final_path, bed_gain=0.18)
        vocal_path.unlink(missing_ok=True)
        bed_path.unlink(missing_ok=True)
        duration_seconds = None
        try:
            with wave.open(str(final_path), "rb") as wav_file:
                duration_seconds = round(wav_file.getnframes() / float(wav_file.getframerate()), 2)
        except Exception:
            pass
        _ = preferred_fmt  # Fun-Music can return mp3; TTS fallback currently ships wav.
        return {
            "asset_id": asset_id,
            "fmt": "wav",
            "duration_seconds": duration_seconds,
            "sample_rate": sample_rate,
            "channels": channels,
            "lyrics": lyrics,
            "request_id": request_id,
            "model": f"{model_name}+qwen-lyrics-fallback",
        }

    def _write_lyrics_with_qwen(self, prompt_text):
        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            return (
                "[Verse]\n夜色慢慢落下来\n心里柔软的期待\n[Chorus]\n跟着节奏往前走\n把今天唱成歌"
            )
        lyrics_payload = {
            "model": os.environ.get("DASHSCOPE_MODEL", "qwen-plus").strip() or "qwen-plus",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是华语流行作词人。根据用户的音乐构想，写一首可直接演唱的短歌歌词。"
                        "使用 [Verse]/[Chorus] 标记，中文为主，总长度 80 到 220 个汉字，不要解释。"
                    ),
                },
                {"role": "user", "content": prompt_text},
            ],
            "temperature": 0.8,
            "max_tokens": 500,
        }
        chat_url = os.environ.get(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/") + "/chat/completions"
        data = post_json(
            chat_url,
            lyrics_payload,
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=60,
        )
        lyrics = str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        return lyrics or "[Verse]\n跟着心跳往前走\n[Chorus]\n把今天唱成歌"

    def _generate_music_runninghub(self, *, prompt_text, instrumental, fmt, lyrics=""):
        api_key = os.environ.get("RUNNINGHUB_API_KEY", "").strip()
        if not api_key:
            self.send_json(503, {"error": {
                "code": "MUSIC_NOT_CONFIGURED",
                "message": "请在设置中配置 RUNNINGHUB_API_KEY（runninghub.cn 企业共享 Key）。",
            }})
            return
        model = os.environ.get("MUSIC_MODEL", "minimax-music-2.6").strip()
        if model not in RUNNINGHUB_MODELS:
            self.send_json(400, {"error": {"code": "INVALID_MUSIC_MODEL", "message": "RunningHub 音乐模型无效。"}})
            return
        base_url = os.environ.get("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn").strip().rstrip("/")
        route = RUNNINGHUB_MODELS[model]["instrumental" if instrumental else "vocal"]
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        lyrics = str(lyrics or "").strip()
        try:
            if instrumental:
                # Music 2.5 无独立纯音乐接口时，用极简歌词占位；2.6 走官方 instrumental 路由。
                if model == "minimax-music-2.5":
                    payload = {
                        "prompt": prompt_text[:6000],
                        "lyrics": "[Intro]\n(instrumental)\n[End]",
                        "sampleRate": "44100",
                        "bitrate": "256000",
                    }
                else:
                    payload = {
                        "prompt": prompt_text[:6000],
                        "sampleRate": "44100",
                        "bitrate": "256000",
                        "format": fmt,
                    }
            else:
                if not lyrics:
                    lyrics = self._write_lyrics_with_qwen(prompt_text)
                lyrics = lyrics[:3500]
                if model == "minimax-music-2.5":
                    payload = {
                        "prompt": prompt_text[:6000],
                        "lyrics": lyrics,
                        "sampleRate": "44100",
                        "bitrate": "256000",
                    }
                else:
                    payload = {
                        "prompt": prompt_text[:6000],
                        "lyrics": lyrics,
                        "sampleRate": "44100",
                        "bitrate": "256000",
                        "format": fmt,
                        "lyricsOptimizer": False,
                    }
            submitted = post_json(base_url + route, payload, headers, timeout=60)
            task_id = str(submitted.get("taskId") or "").strip()
            error_code = str(submitted.get("errorCode") or "").strip()
            error_message = str(submitted.get("errorMessage") or "").strip()
            if not task_id:
                if error_code == "1014":
                    message = "RunningHub 标准模型 API 仅支持企业级-共享 API Key。请到控制台创建企业共享 Key 后再试。"
                    code = "MUSIC_ACCESS_DENIED"
                elif error_code == "1002":
                    message = "RunningHub API Key 无效或已被禁用。"
                    code = "MUSIC_ACCESS_DENIED"
                elif error_code:
                    message = f"RunningHub 拒绝请求（{error_code}）。{error_message[:180]}"
                    code = "MUSIC_PROVIDER_ERROR"
                else:
                    message = "RunningHub 没有返回 taskId。"
                    code = "INVALID_PROVIDER_RESPONSE"
                self.send_json(502, {"error": {"code": code, "message": message}})
                return
            if str(submitted.get("status", "")).upper() == "SUCCESS" and submitted.get("results"):
                result = submitted
            else:
                result = poll_runninghub_task(base_url, api_key, task_id)
            results = result.get("results") or []
            audio_url = ""
            for item in results:
                if isinstance(item, dict) and item.get("url"):
                    audio_url = item["url"]
                    break
            if not audio_url:
                self.send_json(502, {"error": {"code": "MUSIC_AUDIO_URL_MISSING", "message": "RunningHub 没有返回可下载的音频地址。"}})
                return
            ASSET_DIR.mkdir(parents=True, exist_ok=True)
            asset_id = str(uuid.uuid4())
            target = ASSET_DIR / f"{asset_id}.{fmt}"
            download_audio_file(audio_url, target)
            self._write_music_success(
                asset_id=asset_id,
                fmt=fmt,
                duration_seconds=None,
                sample_rate=44100,
                channels=None,
                lyrics=lyrics,
                request_id=task_id,
                model=model,
            )
        except HTTPError as error:
            detail = ""
            try:
                provider_error = json.loads(error.read().decode("utf-8"))
                detail = str(provider_error.get("msg") or provider_error.get("message") or provider_error.get("errorMessage") or "")
            except Exception:
                pass
            print(f"RunningHub returned HTTP {error.code}: {detail[:240]}")
            if error.code in (401, 403):
                message, code = "RunningHub API Key 无效或无权限。", "MUSIC_ACCESS_DENIED"
            elif error.code == 429:
                message, code = "RunningHub 调用繁忙或达到限额，请稍后重试。", "MUSIC_RATE_LIMITED"
            else:
                message, code = f"RunningHub 音乐生成失败（HTTP {error.code}）。", "MUSIC_PROVIDER_ERROR"
            if detail:
                message = f"{message} {detail[:180]}"
            self.send_json(429 if error.code == 429 else 502, {"error": {"code": code, "message": message}})
        except TimeoutError:
            self.send_json(504, {"error": {"code": "MUSIC_TIMEOUT", "message": "RunningHub 音乐生成超时，请稍后在控制台查看任务状态后再决定是否重试。"}})
        except ValueError as error:
            if str(error) == "untrusted audio url":
                self.send_json(502, {"error": {"code": "MUSIC_AUDIO_URL_MISSING", "message": "RunningHub 返回了不受信任的音频地址。"}})
            elif str(error) == "audio too large":
                self.send_json(502, {"error": {"code": "AUDIO_TOO_LARGE", "message": "生成的音频文件超过本地保存限制。"}})
            else:
                self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "音乐生成暂时不可用。"}})
        except (URLError, json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(502, {"error": {"code": "INVALID_PROVIDER_RESPONSE", "message": "RunningHub 返回了无法解析的响应。"}})
        except RuntimeError as error:
            self.send_json(502, {"error": {"code": "MUSIC_PROVIDER_ERROR", "message": str(error)[:240]}})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            print(f"RunningHub music generation failed: {type(error).__name__}: {error}")
            self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "音乐生成暂时不可用。"}})

    def generate_music(self):
        body = self._consume_json_body()
        if body is None:
            return
        prompt = str(body.get("prompt", "")).strip()
        if not 1 <= len(prompt) <= 6000:
            self.send_json(400, {"error": {"code": "INVALID_PROMPT", "message": "创作描述需为 1 到 6000 个字符。"}})
            return
        fmt = body.get("format", "mp3")
        if fmt not in ("mp3", "wav"):
            self.send_json(400, {"error": {"code": "INVALID_FORMAT", "message": "音频格式仅支持 MP3 或 WAV。"}})
            return
        provider = os.environ.get("MUSIC_PROVIDER", "runninghub").strip().lower()
        instrumental = bool(body.get("instrumental", True))
        gender = body.get("gender") if body.get("gender") in ("female", "male") else "female"
        lyrics = str(body.get("lyrics", "")).strip()
        if lyrics and len(lyrics) > 3500:
            self.send_json(400, {"error": {"code": "INVALID_LYRICS", "message": "歌词最长 3500 个字符。"}})
            return
        context = body.get("context", {}) if isinstance(body.get("context", {}), dict) else {}
        prompt_parts = [prompt]
        genres = context.get("genre", [])
        if isinstance(genres, list) and genres:
            prompt_parts.append("风格方向：" + "、".join(str(item)[:40] for item in genres[:8]))
        for label, value in (("速度", context.get("bpm")), ("调性", context.get("key")), ("情绪", context.get("mood")), ("段落结构", context.get("structure")), ("排除", context.get("negative_prompt"))):
            if value:
                prompt_parts.append(f"{label}：{str(value)[:160]}")
        prompt_text = "；".join(prompt_parts)[:6000]
        if provider == "runninghub":
            self._generate_music_runninghub(prompt_text=prompt_text, instrumental=instrumental, fmt=fmt, lyrics=lyrics)
            return
        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        workspace_id = os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
        if not api_key:
            self.send_json(503, {"error": {
                "code": "MUSIC_NOT_CONFIGURED",
                "message": "请在服务端设置中配置百炼 DASHSCOPE_API_KEY，并在百炼模型广场申请 Fun-Music 权限。",
            }})
            return
        if workspace_id and not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", workspace_id):
            self.send_json(503, {"error": {"code": "INVALID_WORKSPACE_ID", "message": "DASHSCOPE_WORKSPACE_ID 格式无效。"}})
            return
        if provider != "dashscope":
            self.send_json(400, {"error": {"code": "UNSUPPORTED_MUSIC_PROVIDER", "message": "当前已实现 RunningHub 与阿里云百炼音乐服务商。"}})
            return
        model = os.environ.get("MUSIC_MODEL", "fun-music-v1").strip()
        if model not in DASHSCOPE_MUSIC_MODELS:
            self.send_json(400, {"error": {"code": "INVALID_MUSIC_MODEL", "message": "音乐模型仅支持 fun-music-v1 或 fun-music-preview。"}})
            return
        provider_payload = {
            "model": model,
            "input": {
                "prompt": prompt_text,
                "is_instrumental": instrumental,
                "format": fmt,
                "enable_aigc_watermark": True,
            },
        }
        if model == "fun-music-v1" and not instrumental and gender in ("female", "male"):
            provider_payload["input"]["gender"] = gender
        music_base_url = f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com" if workspace_id else "https://dashscope.aliyuncs.com"
        endpoint = music_base_url + "/api/v1/services/audio/music/generation"
        try:
            request = Request(endpoint, data=json.dumps(provider_payload, ensure_ascii=False).encode("utf-8"), headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
            output = result.get("output", {}) if isinstance(result.get("output"), dict) else {}
            audio = output.get("audio", {}) if isinstance(output.get("audio"), dict) else {}
            extra = output.get("extra_info") if isinstance(output.get("extra_info"), dict) else {}
            if not extra and isinstance(audio.get("extra_info"), dict):
                extra = audio["extra_info"]
            audio_url = audio.get("url", "")
            ASSET_DIR.mkdir(parents=True, exist_ok=True)
            asset_id = str(uuid.uuid4())
            target = ASSET_DIR / f"{asset_id}.{fmt}"
            download_audio_file(audio_url, target)
            self._write_music_success(
                asset_id=asset_id,
                fmt=fmt,
                duration_seconds=result.get("usage", {}).get("duration") or audio.get("duration"),
                sample_rate=extra.get("sample_rate"),
                channels=extra.get("channels"),
                lyrics=extra.get("lyrics", ""),
                request_id=result.get("request_id", ""),
                model=model,
            )
            return
        except HTTPError as error:
            detail = ""
            try:
                provider_error = json.loads(error.read().decode("utf-8"))
                detail = str(provider_error.get("message") or provider_error.get("error", {}).get("message") or provider_error.get("code") or "")
            except Exception:
                pass
            print(f"Music provider returned HTTP {error.code}: {detail[:240]}")
            if error.code in (401, 403):
                allow_fallback = os.environ.get("MUSIC_TTS_FALLBACK", "1").strip() != "0"
                if allow_fallback:
                    print("Fun-Music unavailable; composing song via Bailian Qwen + TTS fallback.")
                    try:
                        composed = self._compose_song_via_bailian_tts(
                            api_key=api_key,
                            prompt_text=prompt_text,
                            instrumental=instrumental,
                            gender=gender,
                            preferred_fmt=fmt,
                        )
                        self._write_music_success(
                            asset_id=composed["asset_id"],
                            fmt=composed["fmt"],
                            duration_seconds=composed.get("duration_seconds"),
                            sample_rate=composed.get("sample_rate"),
                            channels=composed.get("channels"),
                            lyrics=composed.get("lyrics", ""),
                            request_id=composed.get("request_id", ""),
                            model=composed.get("model", ""),
                        )
                        return
                    except Exception as fallback_error:
                        print(f"TTS song fallback failed: {type(fallback_error).__name__}: {fallback_error}")
                message, code = "百炼 API Key 无效、地域不匹配或 Fun-Music 邀测权限未开通。", "MUSIC_ACCESS_DENIED"
            elif error.code == 429:
                message, code = "音乐生成服务繁忙或达到调用限额，请稍后重试。", "MUSIC_RATE_LIMITED"
            else:
                message, code = f"百炼音乐生成请求失败（HTTP {error.code}）。", "MUSIC_PROVIDER_ERROR"
            if detail:
                message = f"{message} {detail[:180]}"
            self.send_json(429 if error.code == 429 else 502, {"error": {"code": code, "message": message}})
        except ValueError as error:
            if str(error) == "untrusted audio url":
                self.send_json(502, {"error": {"code": "MUSIC_AUDIO_URL_MISSING", "message": "百炼没有返回可下载的音频地址。"}})
            elif str(error) == "audio too large":
                self.send_json(502, {"error": {"code": "AUDIO_TOO_LARGE", "message": "生成的音频文件超过本地保存限制。"}})
            else:
                self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "音乐生成暂时不可用。"}})
        except (URLError, TimeoutError):
            self.send_json(504, {"error": {"code": "MUSIC_TIMEOUT", "message": "音乐生成超时。超时并不代表上游没有受理，请先在百炼侧核实调用记录，再决定是否重试。"}})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(502, {"error": {"code": "INVALID_PROVIDER_RESPONSE", "message": "百炼返回了无法解析的响应。"}})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            print(f"Music generation failed: {type(error).__name__}")
            self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "音乐生成暂时不可用。"}})

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def main():
    port = int(os.environ.get("API_PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), StudioHandler)
    print(f"Studio AI API: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Studio AI prototype.")
        server.server_close()


if __name__ == "__main__":
    main()
