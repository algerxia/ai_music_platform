"""Poll Fun-Music access, then generate one vocal song via the local API."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8003"
OUT = ROOT / "data" / "first-song.json"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DASHSCOPE_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("DASHSCOPE_API_KEY missing")


def probe_access(api_key: str) -> tuple[bool, str]:
    payload = json.dumps(
        {
            "model": "fun-music-v1",
            "input": {
                "prompt": "夏日清新民谣，木吉他与口琴伴奏，轻快节奏，女声演唱",
                "gender": "female",
                "format": "mp3",
                "is_instrumental": False,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    workspace = ""
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DASHSCOPE_WORKSPACE_ID="):
            workspace = line.split("=", 1)[1].strip()
    base = (
        f"https://{workspace}.cn-beijing.maas.aliyuncs.com"
        if workspace
        else "https://dashscope.aliyuncs.com"
    )
    req = urllib.request.Request(
        base + "/api/v1/services/audio/music/generation",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            body = response.read().decode("utf-8")
        return True, body
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        return False, f"HTTP {error.code}: {detail[:300]}"
    except Exception as error:  # noqa: BLE001
        return False, f"{type(error).__name__}: {error}"


def generate_via_local_api() -> dict:
    body = json.dumps(
        {
            "prompt": "一首完整的夏日清新民谣，木吉他与轻柔女声，温柔叙事，适合傍晚散步",
            "format": "mp3",
            "instrumental": False,
            "gender": "female",
            "context": {
                "genre": ["民谣"],
                "mood": "温柔清新",
                "structure": "主歌-副歌-主歌-副歌",
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/api/music/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    api_key = load_key()
    print("Polling Fun-Music access...", flush=True)
    deadline = time.time() + 60 * 30
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        ok, detail = probe_access(api_key)
        if ok:
            print("Direct Fun-Music call succeeded; generating via local API...", flush=True)
            # Prefer saving through local API so asset lands in data/assets
            try:
                result = generate_via_local_api()
            except Exception:
                # Fallback: persist direct provider response
                OUT.parent.mkdir(parents=True, exist_ok=True)
                OUT.write_text(detail, encoding="utf-8")
                print("Saved provider response to", OUT, flush=True)
                print(detail[:500], flush=True)
                return 0
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0
        print(f"[{attempt}] still denied/unavailable: {detail[:160]}", flush=True)
        # Reload key/workspace each loop in case user updates .env
        api_key = load_key()
        time.sleep(20)
    print("Timed out waiting for Fun-Music access.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
