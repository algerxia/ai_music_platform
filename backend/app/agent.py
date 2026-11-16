"""Conversational music creation agents with confirmation gate (no charge until confirm)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import AgentSession, Asset, GenerationJob, Project, User, utcnow
from app.prompt_library import agent_knowledge_pack, apply_template_to_draft, enrich_prompt_for_engine
from app.providers import parse_json_object, qwen_chat, qwen_chat_stream


AGENT_KINDS = ("general", "melody", "lyrics")

SYSTEM_GENERAL = (
    "你是 Studio AI 的对话式音乐创作助手。全程用简体中文与用户交流（专有名词可保留英文）。"
    "审美目标：先写出可唱、有意境的歌词，再按歌词的意象、换气与节奏感去谱曲；忌「念词」式人声。"
    "规则："
    "1) 一次最多追问 1–2 个高价值问题（想对谁唱/场景意象/曲风情绪）。默认先作词，不要先出纯音乐。"
    "2) 默认两步法："
    "第一步输出确认卡 workflow=\"lyrics_only\"、instrumental=false、lyrics 必填（可唱短句+结构标签），prompt 写歌词情绪与演唱气质；"
    "第二步在用户确认歌词后，再输出 workflow=\"compose_from_lyrics\" 的谱曲确认卡：沿用歌词，prompt 写配器/律动/钩子如何贴合词意与节奏。"
    "3) 仅当用户明确只要纯音乐配乐、不要歌词时，才可 instrumental=true、lyrics 空、workflow=\"melody_only\"。"
    "4) 信息足够时输出 ```confirm ... ``` JSON 确认卡："
    "title, engine(minimax|suno|mureka), mode(text|custom|parametric), prompt, lyrics, tags, "
    "instrumental(bool), genre(array), bpm, mood, estimated_credits(int), summary, workflow, "
    "template_id(可选，导师模板 id), quality_boost(可选 bool，苏诺高音质)。"
    "5) 面向用户字段用中文；summary 1–3 句中文。默认引擎 minimax。"
    "6) 用户点名导师模板/曲风时，prompt/tags 应对齐提示词库；苏诺可开 quality_boost。"
    "7) 确认前说明：作词确认不计费；谱曲确认后才会调用 RunningHub 计费。禁止承诺版权/克隆在世艺人。"
)

SYSTEM_MELODY = (
    "你是 Studio AI「谱曲 Agent」。全程简体中文。"
    "你根据已有歌词的意境、换气与节奏感来谱曲编配，并生成带词人声成品；你不重写整首歌词。"
    "规则："
    "1) 必须先有歌词。若会话草稿已有 lyrics / source_lyrics，直接使用；若没有，请用户粘贴歌词或先去「作词 Agent」。"
    "2) 可追问：曲风、主奏乐器、BPM、空间感、人声气质；一次最多 1–2 个问题。"
    "3) 信息足够时输出确认卡 ```confirm ... ```，且必须："
    "workflow=\"compose_from_lyrics\"；instrumental=false；lyrics 沿用已有歌词（可微调标点/结构标签，不改核心意象）；"
    "prompt 描述如何按词意与节奏谱曲：配器、律动、钩子、高潮落点、人声贴唱方式。"
    "4) 确认卡字段：title, engine(优先 minimax), mode(parametric|custom), prompt, lyrics, tags, "
    "instrumental=false, genre(array), bpm, mood, estimated_credits(int), summary, workflow=\"compose_from_lyrics\", template_id(可选)。"
    "summary 用中文说明将按歌词意境谱曲。"
    "5) 仅当用户明确要求纯音乐时，才可 instrumental=true 并清空 lyrics（仍要根据词意写情绪配乐）。"
    "6) 确认前不计费；默认引擎 minimax；禁止版权承诺与声音克隆。"
)

SYSTEM_LYRICS = (
    "你是 Studio AI「作词 Agent」。全程简体中文。"
    "你负责先写出稀疏、可唱、有意境的口语歌词；不谱曲、不生成音频。忌朗诵念词腔。"
    "规则："
    "1) 可追问：想对谁唱、场景意象、一两句必须出现的词、大概情绪；不要堆砌口号。"
    "2) 不需要旋律 Take。信息足够时输出确认卡 ```confirm ... ```，字段："
    "title, engine=minimax, mode=parametric, prompt(歌词情绪/演唱气质，短), lyrics(必填，带 [Verse]/[Chorus] 等标签), "
    "tags, instrumental=false, genre, bpm(建议演唱节奏感), mood, estimated_credits=0, summary, "
    "workflow=\"lyrics_only\"。"
    "3) 歌词：短句、留白、可换气；主歌字少；总字数尽量 ≤700；禁止密密麻麻朗诵长句；遵守提示词库作词准则。"
    "4) 说明：确认后只锁定歌词，不计费；用户再去「谱曲 Agent」按词谱曲。"
    "5) 禁止版权承诺与声音克隆。"
)


def _system_for_kind(kind: str) -> str:
    key = (kind or "general").strip().lower()
    if key not in AGENT_KINDS:
        key = "general"
    base = {
        "general": SYSTEM_GENERAL,
        "melody": SYSTEM_MELODY,
        "lyrics": SYSTEM_LYRICS,
    }[key]
    return f"{base}\n\n{agent_knowledge_pack(key)}"


# Backward-compatible map (raw systems without library pack). Prefer _system_for_kind().
SYSTEM_BY_KIND = {
    "general": SYSTEM_GENERAL,
    "melody": SYSTEM_MELODY,
    "lyrics": SYSTEM_LYRICS,
}

WELCOME_BY_KIND = {
    "general": "你好。我是对话式创作助手。默认先写歌词，再按歌词意境与节奏谱曲。先说：想对谁唱、什么意象，或一句灵感即可。作词确认不计费。",
    "melody": "你好，我是「谱曲 Agent」。请先有歌词（可从作词 Agent 交过来），再说曲风、乐器与节奏偏好。确认后按词意谱曲并生成人声。",
    "lyrics": "你好，我是「作词 Agent」。先告诉我想对谁唱、什么场景意象；确认后只锁定歌词，再交给谱曲 Agent 按词谱曲。",
}

TITLE_BY_KIND = {
    "general": "对话创作",
    "melody": "按词谱曲",
    "lyrics": "先作词",
}


DELIVERY_RE = re.compile(
    r"(发我|发给我|发过来|发一首|发一下|听听|听一下|播放|放一下|给我听|我想听|直接听|发来听听)",
    re.I,
)


def normalize_agent_kind(kind: str | None) -> str:
    value = (kind or "general").strip().lower()
    return value if value in AGENT_KINDS else "general"


def _mostly_english(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    letters = sum(1 for ch in s if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return letters >= 24 and letters > cjk * 2


def _chinese_summary(data: dict) -> str:
    summary = str(data.get("summary") or "").strip()
    if summary and not _mostly_english(summary):
        return summary[:500]
    title = str(data.get("title") or "对话创作").strip() or "对话创作"
    engine = str(data.get("engine") or "minimax").lower()
    engine_label = {"minimax": "MiniMax", "suno": "苏诺", "mureka": "Mureka"}.get(engine, engine)
    mode = str(data.get("mode") or "parametric")
    mode_label = {"text": "文生", "custom": "自定义", "parametric": "精细参数"}.get(mode, mode)
    instrumental = bool(data.get("instrumental", True))
    vocal = "纯音乐" if instrumental else "含人声"
    genre = data.get("genre") if isinstance(data.get("genre"), list) else []
    genre_text = "、".join(str(g) for g in genre[:4] if g) or "未指定风格"
    mood = str(data.get("mood") or "").strip()
    bpm = int(data.get("bpm") or 90)
    bits = [f"《{title}》", f"{engine_label}·{mode_label}", vocal, genre_text, f"{bpm} BPM"]
    if mood and not _mostly_english(mood):
        bits.append(mood)
    return "；".join(bits)[:500]


def _extract_confirm(text: str, *, kind: str = "general") -> dict | None:
    match = re.search(r"```confirm\s*([\s\S]*?)```", text or "", re.I)
    if not match:
        match = re.search(r"```json\s*(\{[\s\S]*?" "estimated_credits" r"[\s\S]*?\})\s*```", text or "", re.I)
    if not match:
        return None
    try:
        data = parse_json_object(match.group(1))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    kind = normalize_agent_kind(kind)
    engine = str(data.get("engine") or "minimax").lower()
    if engine not in {"minimax", "suno", "mureka"}:
        engine = "minimax"
    mode = str(data.get("mode") or "parametric").lower()
    raw_lyrics = str(data.get("lyrics") or data.get("source_lyrics") or "").strip()
    melody_asset_id = str(data.get("melody_asset_id") or data.get("parent_asset_id") or "").strip()
    requested_workflow = str(data.get("workflow") or "").strip().lower()

    if kind == "lyrics":
        instrumental = False
        workflow = "lyrics_only"
        credits = 0
        engine = "minimax"
        if not raw_lyrics:
            return None
    elif kind == "melody":
        # 谱曲：默认按词生成带词人声；仅用户明确只要纯音乐时走 melody_only
        if requested_workflow == "melody_only" or (bool(data.get("instrumental")) and not raw_lyrics):
            instrumental = True
            raw_lyrics = ""
            workflow = "melody_only"
        else:
            instrumental = False
            workflow = "compose_from_lyrics"
            if not raw_lyrics:
                return None
        credits = {"minimax": 10, "suno": 20, "mureka": 50}.get(engine, 10)
    else:
        if requested_workflow == "lyrics_only" or (
            raw_lyrics and requested_workflow not in {"compose_from_lyrics", "vocal_direct", "melody_only"}
            and not bool(data.get("compose", False))
        ):
            # 综合助手默认先锁词
            if not raw_lyrics:
                return None
            instrumental = False
            workflow = "lyrics_only"
            credits = 0
        elif requested_workflow == "melody_only" or (bool(data.get("instrumental")) and not raw_lyrics):
            instrumental = True
            raw_lyrics = ""
            workflow = "melody_only"
            credits = {"minimax": 10, "suno": 20, "mureka": 50}.get(engine, 10)
        else:
            instrumental = False if raw_lyrics else bool(data.get("instrumental", False))
            if raw_lyrics:
                instrumental = False
                workflow = "compose_from_lyrics"
            else:
                instrumental = True
                workflow = "melody_only"
            credits = {"minimax": 10, "suno": 20, "mureka": 50}.get(engine, 10)

    title = str(data.get("title") or TITLE_BY_KIND.get(kind, "对话创作"))[:120]
    prompt = str(data.get("prompt") or data.get("summary") or "")[:6000]
    tags = str(data.get("tags") or "")[:1000]
    template_id = str(data.get("template_id") or "").strip()
    quality_boost = bool(data.get("quality_boost"))
    if template_id:
        merged = apply_template_to_draft(
            {
                "title": title,
                "prompt": prompt,
                "tags": tags,
                "instrumental": instrumental,
                "lyrics": raw_lyrics,
            },
            template_id,
            instrumental=instrumental,
        )
        title = str(merged.get("title") or title)[:120]
        prompt = str(merged.get("prompt") or prompt)[:6000]
        tags = str(merged.get("tags") or tags)[:1000]
        if instrumental:
            raw_lyrics = ""
    if instrumental:
        raw_lyrics = ""
        if "纯音乐" not in prompt and "instrumental" not in prompt.lower() and "无人声" not in prompt:
            prompt = f"{prompt}\n纯音乐，instrumental，无人声，突出可记忆旋律钩子。".strip()
    elif workflow == "lyrics_only" and not prompt.strip():
        prompt = "口语可唱，短句留白，先锁定歌词意象与节奏感。"
    elif workflow == "compose_from_lyrics" and not prompt.strip():
        prompt = "按歌词意境与换气节奏谱曲；旋律跟着词走，短句贴唱，禁止念词腔。"

    enriched = enrich_prompt_for_engine(
        engine=engine,
        prompt=prompt,
        tags=tags,
        lyrics=raw_lyrics,
        quality_boost=quality_boost,
        template_id=template_id,
    )
    prompt = enriched["prompt"]
    tags = enriched["tags"]
    raw_lyrics = enriched["lyrics"] if not instrumental else ""

    summary_src = {
        **data,
        "title": title,
        "engine": engine,
        "mode": mode,
        "instrumental": instrumental,
    }
    summary = _chinese_summary(summary_src)
    if workflow == "lyrics_only" and "歌词" not in summary:
        summary = f"{summary}；先锁定歌词，确认后再谱曲。"[:500]
    if workflow == "compose_from_lyrics" and "谱曲" not in summary:
        summary = f"{summary}；按歌词意境与节奏谱曲。"[:500]
    if workflow == "melody_only" and "纯音乐" not in summary:
        summary = f"{summary}；纯音乐配乐。"[:500]

    out = {
        "title": title,
        "engine": engine,
        "mode": mode if mode in {"text", "custom", "parametric"} else "parametric",
        "prompt": prompt,
        "lyrics": raw_lyrics[:5000],
        "tags": tags[:1000],
        "instrumental": instrumental,
        "genre": data.get("genre") if isinstance(data.get("genre"), list) else [],
        "bpm": int(data.get("bpm") or 90),
        "mood": str(data.get("mood") or "")[:120],
        "estimated_credits": int(data.get("estimated_credits") or credits),
        "summary": summary,
        "workflow": workflow,
        "agent_kind": kind,
        "quality_boost": quality_boost,
    }
    if template_id:
        out["template_id"] = template_id
    if melody_asset_id:
        out["melody_asset_id"] = melody_asset_id
    if raw_lyrics:
        out["source_lyrics"] = raw_lyrics[:5000]
    return out


def asset_to_media(asset) -> dict:
    kind = (getattr(asset, "asset_type", "") or "audio").lower()
    fmt = (getattr(asset, "format", "") or "").lower()
    preview = "video" if kind == "mv" or fmt in {"mp4", "webm", "mov"} else "audio"
    if kind in {"lyrics", "analysis"}:
        preview = "text"
    elif kind in {"midi", "score"}:
        preview = "file"
    meta = getattr(asset, "meta", None) or {}
    return {
        "id": str(asset.id),
        "title": asset.title,
        "duration": asset.duration_seconds,
        "format": asset.format,
        "assetType": kind or "audio",
        "fileSize": getattr(asset, "file_size_bytes", 0) or 0,
        "audioUrl": f"/api/assets/{asset.id}/content",
        "url": f"/api/assets/{asset.id}/content",
        "preview": preview,
        "instrumental": bool(meta.get("instrumental")) or meta.get("origin") == "instrumental",
        "origin": meta.get("origin") or "",
    }


def is_delivery_request(text: str) -> bool:
    return bool(DELIVERY_RE.search(text or ""))


def resolve_playable_assets(db: Session, session: AgentSession, user: User, limit: int = 4) -> list[dict]:
    from sqlalchemy import select

    draft = session.draft or {}
    title = (draft.get("title") or session.title or "").strip()
    cached = draft.get("result_assets") if isinstance(draft.get("result_assets"), list) else []
    if cached:
        return cached[:limit]

    assets: list = []
    if session.confirmed_job_id:
        job = db.get(GenerationJob, session.confirmed_job_id)
        if job and job.status == "succeeded":
            assets = list(
                db.scalars(
                    select(Asset)
                    .where(
                        Asset.job_id == job.id,
                        Asset.deleted_at.is_(None),
                        Asset.asset_type.in_(("audio", "stem", "mv")),
                    )
                    .order_by(Asset.version.asc())
                    .limit(limit)
                )
            )
    if not assets and session.project_id:
        assets = list(
            db.scalars(
                select(Asset)
                .where(
                    Asset.project_id == session.project_id,
                    Asset.user_id == user.id,
                    Asset.deleted_at.is_(None),
                    Asset.asset_type.in_(("audio", "stem", "mv")),
                )
                .order_by(Asset.created_at.desc())
                .limit(limit)
            )
        )
    if not assets and title:
        assets = list(
            db.scalars(
                select(Asset)
                .where(
                    Asset.user_id == user.id,
                    Asset.deleted_at.is_(None),
                    Asset.title.ilike(f"%{title[:40]}%"),
                    Asset.asset_type.in_(("audio", "stem", "mv")),
                )
                .order_by(Asset.created_at.desc())
                .limit(limit)
            )
        )
    if not assets:
        assets = list(
            db.scalars(
                select(Asset)
                .where(
                    Asset.user_id == user.id,
                    Asset.deleted_at.is_(None),
                    Asset.asset_type.in_(("audio", "stem", "mv")),
                )
                .order_by(Asset.created_at.desc())
                .limit(limit)
            )
        )
    return [asset_to_media(a) for a in assets]


def deliver_assets_reply(db: Session, session: AgentSession, user: User, user_text: str) -> AgentSession:
    assets = resolve_playable_assets(db, session, user)
    messages = list(session.messages or [])
    messages.append({"role": "user", "content": user_text[:4000], "at": datetime.now(timezone.utc).isoformat()})
    if assets:
        title = (session.draft or {}).get("title") or session.title or assets[0].get("title") or "作品"
        content = f"已附上《{title}》，可直接点播放。"
        messages.append(
            {
                "role": "assistant",
                "content": content,
                "at": datetime.now(timezone.utc).isoformat(),
                "assets": assets,
                "asset_id": assets[0]["id"],
                "project_id": str(session.project_id) if session.project_id else None,
            }
        )
        draft = dict(session.draft or {})
        draft["result_assets"] = assets
        draft["result_asset_id"] = assets[0]["id"]
        session.draft = draft
        job = db.get(GenerationJob, session.confirmed_job_id) if session.confirmed_job_id else None
        if job and job.status == "succeeded" and session.state in {"generating", "awaiting_confirm"}:
            session.state = "ready"
    else:
        messages.append(
            {
                "role": "assistant",
                "content": "这轮还没有可播放的成品。生成完成后再说「发我」即可。",
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        session.state = "clarifying"
    session.messages = messages
    flag_modified(session, "messages")
    flag_modified(session, "draft")
    session.updated_at = utcnow()
    db.commit()
    db.refresh(session)
    return session


def session_public(session: AgentSession, *, result_assets: list[dict] | None = None) -> dict:
    draft = dict(session.draft or {})
    if result_assets:
        draft["result_assets"] = result_assets
        draft["result_asset_id"] = result_assets[0]["id"] if result_assets else draft.get("result_asset_id")
    kind = normalize_agent_kind(getattr(session, "kind", None) or draft.get("agent_kind"))
    return {
        "id": str(session.id),
        "title": session.title,
        "kind": kind,
        "state": session.state,
        "messages": session.messages or [],
        "draft": draft,
        "project_id": str(session.project_id) if session.project_id else None,
        "confirmed_job_id": str(session.confirmed_job_id) if session.confirmed_job_id else None,
        "result_assets": result_assets or draft.get("result_assets") or [],
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


def create_session(
    db: Session,
    user: User,
    *,
    kind: str = "general",
    title: str | None = None,
    seed_draft: dict | None = None,
) -> AgentSession:
    kind = normalize_agent_kind(kind)
    draft = dict(seed_draft or {})
    draft["agent_kind"] = kind
    seed_lyrics = str(draft.get("source_lyrics") or draft.get("lyrics") or "").strip()
    welcome = WELCOME_BY_KIND[kind]
    if kind == "melody" and seed_lyrics:
        preview = seed_lyrics.replace("\n", " / ")[:80]
        welcome = (
            f"已带入歌词：「{preview}…」。"
            if len(seed_lyrics) > 80
            else f"已带入歌词：「{preview}」。"
        ) + "说说想要的曲风、乐器与节奏，确认后按词意谱曲。"
    elif kind == "lyrics" and seed_lyrics:
        welcome = "已带入一段歌词草稿。可以说要怎么改意象或节奏感；确认后锁定歌词，再交给谱曲 Agent。"
    session = AgentSession(
        user_id=user.id,
        title=(title or TITLE_BY_KIND[kind])[:120],
        kind=kind,
        state="clarifying",
        messages=[
            {
                "role": "assistant",
                "content": welcome,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        draft=draft,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _build_history(messages: list[dict], *, kind: str = "general") -> list[dict]:
    kind = normalize_agent_kind(kind)
    history = [{"role": "system", "content": _system_for_kind(kind)}]
    for item in messages[-12:]:
        role = item.get("role")
        if role in {"user", "assistant"}:
            history.append({"role": role, "content": str(item.get("content") or "")[:4000]})
    # Remind lyrics agent of bound melody id
    return history


def _visible_reply(reply: str) -> str:
    visible = re.sub(r"```confirm[\s\S]*?```", "", reply or "", flags=re.I).strip()
    visible = re.sub(r"```json[\s\S]*?estimated_credits[\s\S]*?```", "", visible, flags=re.I).strip()
    visible = re.sub(r"\n{3,}", "\n\n", visible)
    return visible


def append_user_message(db: Session, session: AgentSession, content: str) -> list[dict]:
    text = (content or "").strip()
    if not text:
        raise ValueError("消息不能为空")
    messages = list(session.messages or [])
    messages.append({"role": "user", "content": text[:4000], "at": datetime.now(timezone.utc).isoformat()})
    session.messages = messages
    session.state = "streaming"
    session.updated_at = utcnow()
    db.commit()
    db.refresh(session)
    kind = normalize_agent_kind(getattr(session, "kind", None))
    history = _build_history(messages, kind=kind)
    # Inject bound lyrics for compose agent / continue-from-lyrics
    draft = session.draft or {}
    seed_lyrics = str(draft.get("source_lyrics") or draft.get("lyrics") or "").strip()
    if kind == "melody" and seed_lyrics:
        history.insert(
            1,
            {
                "role": "system",
                "content": (
                    "当前绑定歌词如下，谱曲确认卡必须沿用（可微调结构标签，不改核心意象）：\n"
                    f"{seed_lyrics[:3500]}"
                ),
            },
        )
    elif kind == "general" and draft.get("workflow_stage") == "lyrics_ready" and seed_lyrics:
        history.insert(
            1,
            {
                "role": "system",
                "content": (
                    "用户已确认歌词（workflow_stage=lyrics_ready）。下一步应输出 "
                    "workflow=\"compose_from_lyrics\" 的谱曲确认卡，沿用歌词：\n"
                    f"{seed_lyrics[:3500]}"
                ),
            },
        )
    return history


def finalize_assistant_reply(db: Session, session: AgentSession, reply: str) -> AgentSession:
    kind = normalize_agent_kind(getattr(session, "kind", None))
    confirm = _extract_confirm(reply, kind=kind)
    # Preserve bound lyrics for compose cards if model omitted them
    if confirm and kind in {"melody", "general"}:
        prev = session.draft or {}
        prev_lyrics = str(prev.get("source_lyrics") or prev.get("lyrics") or "").strip()
        if prev_lyrics and not str(confirm.get("lyrics") or "").strip():
            confirm["lyrics"] = prev_lyrics
            confirm["source_lyrics"] = prev_lyrics
            if confirm.get("workflow") != "melody_only":
                confirm["instrumental"] = False
                confirm["workflow"] = "compose_from_lyrics"
        # 综合助手：歌词已锁定后，下一张确认卡应进入谱曲
        if (
            kind == "general"
            and prev.get("workflow_stage") == "lyrics_ready"
            and confirm.get("workflow") == "lyrics_only"
            and str(confirm.get("lyrics") or prev_lyrics).strip()
        ):
            confirm["workflow"] = "compose_from_lyrics"
            confirm["instrumental"] = False
            confirm["lyrics"] = str(confirm.get("lyrics") or prev_lyrics).strip()
            confirm["source_lyrics"] = confirm["lyrics"]
    visible = _visible_reply(reply)
    assistant_msg = {
        "role": "assistant",
        "content": visible or "我整理了一份方案，请确认。",
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if confirm:
        assistant_msg["confirm"] = confirm
        session.state = "awaiting_confirm"
        # Merge previous draft keys we want to keep
        prev = dict(session.draft or {})
        merged = {**prev, **confirm, "agent_kind": kind}
        if prev.get("melody_asset_id") and not merged.get("melody_asset_id"):
            merged["melody_asset_id"] = prev["melody_asset_id"]
        keep_lyrics = str(merged.get("lyrics") or prev.get("source_lyrics") or prev.get("lyrics") or "").strip()
        if keep_lyrics:
            merged["source_lyrics"] = keep_lyrics
            if not merged.get("lyrics"):
                merged["lyrics"] = keep_lyrics
        session.draft = merged
        if confirm.get("title"):
            session.title = str(confirm["title"])[:120]
    else:
        session.state = "clarifying"
    messages = list(session.messages or [])
    messages.append(assistant_msg)
    session.messages = messages
    session.updated_at = utcnow()
    db.commit()
    db.refresh(session)
    return session


def stream_display_delta(buffer: str, delta: str) -> tuple[str, str | None]:
    new_buf = buffer + delta
    fence = re.search(r"```confirm|```json", new_buf, flags=re.I)
    if not fence:
        return new_buf, delta
    cut = fence.start()
    already = len(buffer)
    if cut <= already:
        return new_buf, None
    return new_buf, new_buf[already:cut]


def post_message(db: Session, session: AgentSession, content: str) -> AgentSession:
    history = append_user_message(db, session, content)
    reply = qwen_chat(history, temperature=0.6)
    return finalize_assistant_reply(db, session, reply)


def iter_assistant_stream(history: list[dict]):
    yield from qwen_chat_stream(history, temperature=0.6)


def ensure_agent_project(db: Session, user: User, draft: dict) -> Project:
    name = str(draft.get("title") or "对话创作")[:120]
    project = Project(
        owner_user_id=user.id,
        name=name,
        purpose="对话式生成",
        prompt=str(draft.get("prompt") or draft.get("summary") or "")[:6000],
        lyrics=str(draft.get("lyrics") or ""),
        genre=draft.get("genre") if isinstance(draft.get("genre"), list) else [],
        bpm=int(draft.get("bpm") or 90),
        mood=str(draft.get("mood") or ""),
        vocal_mode="instrumental" if draft.get("instrumental", True) else "vocal",
        tone="lilac",
        icon="◈",
    )
    db.add(project)
    db.flush()
    return project
