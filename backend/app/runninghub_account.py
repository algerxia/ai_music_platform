"""RunningHub account status / wallet balance."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import settings


def fetch_runninghub_account(api_key: str | None = None) -> dict:
    key = (api_key or settings.runninghub_api_key or "").strip()
    if not key:
        raise RuntimeError("尚未配置 RunningHub API Key")
    base = (settings.runninghub_base_url or "https://www.runninghub.cn").rstrip("/")
    url = f"{base}/uc/openapi/accountStatus"
    body = json.dumps({"apikey": key}).encode("utf-8")
    req = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"RunningHub 账户查询失败 HTTP {exc.code}: {detail[:240]}") from exc
    except URLError as exc:
        raise RuntimeError(f"RunningHub 账户查询网络错误: {exc.reason}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"RunningHub 返回非 JSON: {raw[:200]}") from exc

    code_raw = payload.get("code")
    try:
        code_ok = int(code_raw) == 0
    except (TypeError, ValueError):
        code_ok = str(code_raw).strip().lower() in {"0", "ok", "success"}
    data = payload.get("data") or {}
    if not code_ok and not (isinstance(data, dict) and ("remainMoney" in data or "remainCoins" in data)):
        raise RuntimeError(payload.get("msg") or "RunningHub 账户查询失败")

    return {
        "remain_money": data.get("remainMoney"),
        "currency": data.get("currency") or "CNY",
        "remain_coins": data.get("remainCoins"),
        "remain_point": data.get("remainPoint"),
        "current_task_counts": data.get("currentTaskCounts"),
        "api_type": data.get("apiType") or "",
        "configured": True,
        "key_masked": f"{key[:4]}…{key[-4:]}" if len(key) > 8 else "****",
        "base_url": base,
    }
