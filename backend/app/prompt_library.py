"""Curated AI music prompt library (from Feishu pack) for agents, API, and generation enrichment."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


DATA_PATH = Path(__file__).resolve().parent / "data" / "prompt_library.json"


@lru_cache(maxsize=1)
def load_library() -> dict[str, Any]:
    if not DATA_PATH.exists():
        return {"version": 0, "templates": [], "structure_tags": [], "genre_dictionary": []}
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def agent_knowledge_pack(kind: str = "general", *, max_chars: int = 2800) -> str:
    """Compact reference injected into Agent system prompts."""
    lib = load_library()
    kind = (kind or "general").lower()
    lines: list[str] = [
        "【Studio 提示词库 · 创作参考｜来自内部提示词大礼包】",
        "写 prompt / tags / lyrics 时优先使用下列规范；不要整段照抄冗长列表，按需挑选。",
    ]

    rules = lib.get("lyrics_rules") or []
    if kind in {"lyrics", "general"} and rules:
        lines.append("作词准则：" + "；".join(str(r) for r in rules[:5]))

    structs = lib.get("structure_tags") or []
    if structs:
        keys = " ".join(str(s.get("key") or "") for s in structs[:14] if s.get("key"))
        lines.append(f"结构标签：{keys}")

    mentors = lib.get("mentor_styles") or []
    if mentors:
        lines.append(
            "导师风格标签（可写入 tags/prompt）："
            + "；".join(f"{m.get('tag')}（{m.get('bias')}）" for m in mentors)
        )

    quality = lib.get("quality_boost") or {}
    if quality.get("style_tag"):
        lines.append(
            "苏诺音质增强（engine=suno 且用户要高音质时）：style/tags 追加 "
            + str(quality["style_tag"])
            + "；歌词可追加 "
            + str(quality.get("lyrics_tag") or "")
        )

    prod = lib.get("production_tags") or []
    if kind in {"melody", "general"} and prod:
        lines.append("制作质感标签（择 3–6 个写入 prompt/tags）：" + " ".join(prod[:10]))

    templates = lib.get("templates") or []
    if templates:
        lines.append("可用导师模板 id（确认卡可用 template_id 引用）：")
        for t in templates:
            lines.append(f"- {t.get('id')}: {t.get('title')}")

    featured = lib.get("featured_genres") or []
    if featured:
        lines.append(
            "常用曲风："
            + "、".join(
                f"{g.get('label')}({', '.join((g.get('tags') or [])[:2])})" for g in featured[:10]
            )
        )

    if kind == "melody":
        lines.append(
            "旋律 Agent：prompt 用英文或中英混写描述配器/律动/钩子/空间；必须标明 instrumental / 纯音乐 / 无人声；"
            "可借鉴导师模板的乐器与编曲段，但去掉人声演唱指令。"
        )
    elif kind == "lyrics":
        lines.append(
            "作词 Agent：lyrics 必含 [Verse]/[Chorus] 等；短句留白；prompt 只写人声情绪与贴唱方式；"
            "可加 [Vocal Emotion: …] / [Vocal Style: …]。"
        )
    else:
        lines.append("综合助手：默认先纯音乐；用户要人声时再带稀疏歌词与结构标签。")

    pack = "\n".join(lines)
    return _clip(pack, max_chars)


def get_template(template_id: str) -> dict[str, Any] | None:
    tid = (template_id or "").strip()
    if not tid:
        return None
    for t in load_library().get("templates") or []:
        if str(t.get("id")) == tid or str(t.get("title")) == tid:
            return t
    return None


def search_genres(query: str = "", *, limit: int = 30) -> list[dict[str, str]]:
    lib = load_library()
    q = (query or "").strip().lower()
    items = lib.get("genre_dictionary") or []
    if not q:
        # featured first as pseudo results
        out = []
        for g in lib.get("featured_genres") or []:
            tags = g.get("tags") or []
            out.append({"en": tags[0] if tags else str(g.get("label")), "zh": str(g.get("label"))})
        return out[:limit]
    hits = []
    for item in items:
        en = str(item.get("en") or "")
        zh = str(item.get("zh") or "")
        if q in en.lower() or q in zh.lower():
            hits.append({"en": en, "zh": zh})
            if len(hits) >= limit:
                break
    return hits


def public_summary() -> dict[str, Any]:
    lib = load_library()
    templates = [
        {
            "id": t.get("id"),
            "title": t.get("title"),
            "vocal": t.get("vocal"),
            "prompt_preview": _clip(str(t.get("prompt") or ""), 160),
        }
        for t in (lib.get("templates") or [])
    ]
    return {
        "version": lib.get("version", 1),
        "source": lib.get("source"),
        "quality_boost": lib.get("quality_boost") or {},
        "mentor_styles": lib.get("mentor_styles") or [],
        "featured_genres": lib.get("featured_genres") or [],
        "structure_tags": lib.get("structure_tags") or [],
        "style_moods": (lib.get("style_moods") or [])[:40],
        "song_types": lib.get("song_types") or [],
        "production_tags": (lib.get("production_tags") or [])[:16],
        "vocal_prompts": lib.get("vocal_prompts") or {},
        "sfx_tags": lib.get("sfx_tags") or [],
        "rap_styles": lib.get("rap_styles") or [],
        "lyrics_rules": lib.get("lyrics_rules") or [],
        "templates": templates,
        "agent_quick": lib.get("agent_quick") or {},
        "counts": {
            "templates": len(lib.get("templates") or []),
            "structure_tags": len(lib.get("structure_tags") or []),
            "genre_dictionary": len(lib.get("genre_dictionary") or []),
            "genre_categories": len(lib.get("genre_categories") or []),
            "style_moods": len(lib.get("style_moods") or []),
        },
    }


def apply_template_to_draft(draft: dict, template_id: str, *, instrumental: bool | None = None) -> dict:
    """Merge a mentor template into confirm/draft fields."""
    tpl = get_template(template_id)
    if not tpl:
        return draft
    out = dict(draft or {})
    prompt = str(tpl.get("prompt") or "").strip()
    if instrumental is True or (instrumental is None and bool(out.get("instrumental"))):
        # strip vocal-forward lines lightly for melody drafts
        kept = []
        for line in prompt.splitlines():
            low = line.lower()
            if any(k in low for k in ("female vocal", "male vocal", "weiblicher", "gesang auf")):
                continue
            kept.append(line)
        prompt = "\n".join(kept).strip() or prompt
        if "instrumental" not in prompt.lower() and "纯音乐" not in prompt and "无人声" not in prompt:
            prompt = f"{prompt}\n纯音乐，instrumental，无人声，突出可记忆旋律钩子。"
        out["instrumental"] = True
        out["lyrics"] = ""
    out["prompt"] = prompt[:6000]
    out["template_id"] = tpl.get("id")
    if not out.get("title"):
        out["title"] = str(tpl.get("title") or "模板创作")[:120]
    tags = str(out.get("tags") or "")
    mentor = " ".join(m.get("tag") or "" for m in (load_library().get("mentor_styles") or []))
    if mentor and mentor.split()[0] not in tags:
        out["tags"] = (tags + " " + mentor).strip()[:1000]
    return out


def enrich_prompt_for_engine(
    *,
    engine: str,
    prompt: str,
    tags: str = "",
    lyrics: str = "",
    quality_boost: bool = False,
    template_id: str = "",
) -> dict[str, str]:
    """Optionally fold library quality/production tags into generation fields."""
    engine = (engine or "").lower()
    prompt = (prompt or "").strip()
    tags = (tags or "").strip()
    lyrics = (lyrics or "").strip()
    lib = load_library()

    if template_id and not prompt:
        tpl = get_template(template_id)
        if tpl:
            prompt = str(tpl.get("prompt") or "")

    if quality_boost:
        qb = lib.get("quality_boost") or {}
        style_tag = str(qb.get("style_tag") or "")
        lyrics_tag = str(qb.get("lyrics_tag") or "")
        if style_tag and style_tag not in prompt and style_tag not in tags:
            # Suno custom prefers tags; text/minimax keep boost in prompt.
            if engine == "suno" and tags:
                tags = f"{tags}, {style_tag}".strip(", ")
            else:
                prompt = f"{prompt}\n{style_tag}".strip()
        if lyrics_tag and lyrics and lyrics_tag not in lyrics:
            lyrics = f"{lyrics}\n{lyrics_tag}".strip()

    # Light production spice for suno if tags still thin
    if engine == "suno" and len(tags) < 40:
        prod = lib.get("production_tags") or []
        pick = [
            p
            for p in prod
            if p
            in (
                "[clear vocal]",
                "[Condenser Mic]",
                "[catchy chord details]",
                "[Sophisticated sound design]",
            )
        ]
        if pick:
            tags = (", ".join([tags] + pick) if tags else ", ".join(pick)).strip(", ")

    return {
        "prompt": prompt[:6000],
        "tags": tags[:1000],
        "lyrics": lyrics[:5000],
    }
