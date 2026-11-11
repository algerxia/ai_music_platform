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
    "审美目标：先做出好听、有记忆点的旋律与编曲，再贴贴合旋律的口语歌词；忌「念词」式人声。"
    "规则："
    "1) 一次最多追问 1–2 个高价值问题（曲风/情绪/场景/想对谁唱）。默认不要一上来就写整首歌词。"
    "2) 默认两步法：第一步确认卡 instrumental=true、lyrics 空；第二步用户听过后交给作词 Agent 或点填词。"
    "3) 仅当用户明确要求一次出带词人声时，才可 instrumental=false 并附稀疏歌词。"
    "4) 信息足够时输出 ```confirm ... ``` JSON 确认卡："
    "title, engine(minimax|suno|mureka), mode(text|custom|parametric), prompt, lyrics, tags, "
    "instrumental(bool), genre(array), bpm, mood, estimated_credits(int), summary, "
    "template_id(可选，导师模板 id), quality_boost(可选 bool，苏诺高音质)。"
    "5) 面向用户字段用中文；summary 1–3 句中文。默认引擎 minimax。"
    "6) 用户点名导师模板/曲风时，prompt/tags 应对齐提示词库；苏诺可开 quality_boost。"
    "7) 确认前说明：点确认才扣积分。禁止承诺版权/克隆在世艺人。"
)

SYSTEM_MELODY = (
    "你是 Studio AI「旋律创作 Agent」。全程简体中文。"
    "你只负责创作好听、可记忆的纯音乐旋律与编曲，不做歌词、不生成带词人声。"
    "规则："
    "1) 一次最多追问 1–2 个问题：曲风、情绪、主奏乐器、时长/BPM、使用场景。"
    "2) 信息足够时输出确认卡 ```confirm ... ```，且必须："
    "instrumental=true；lyrics 必须是空字符串 \"\"；"
    "prompt 只写乐器、旋律动机、律动、钩子、空间感、高潮落点，并写明「纯音乐 / instrumental / 无人声」；"
    "不要写演唱法或歌词意向；可引用 template_id 并把模板编曲改写成无人声。"
    "3) 确认卡字段：title, engine(优先 minimax), mode(parametric|text), prompt, lyrics=\"\", tags, "
    "instrumental=true, genre(array), bpm, mood, estimated_credits(int, minimax 约 10), summary, template_id(可选)。"
    "summary 用中文说明这是旋律草案，满意后再去「作词 Agent」填词。"
    "4) 用户若要求写词：礼貌说明你专攻旋律，请切换到「作词 Agent」，并把当前 Take 交给对方。"
    "5) 确认前不扣积分；默认引擎 minimax；禁止版权承诺与声音克隆。"
)

SYSTEM_LYRICS = (
    "你是 Studio AI「作词 Agent」。全程简体中文。"
    "你根据已有好听旋律写稀疏、可唱的口语歌词，并产出可贴人声的确认方案；忌朗诵念词腔。"
    "规则："
    "1) 必须先确认用户选中的旋律 Take（melody_asset_id）。若会话草稿或上下文已有 melody_asset_id，直接使用，勿再追问无关问题。"
    "若没有，请用户从旋律 Agent 的 Take 点「交给作词」，或粘贴 asset id / 说明曲名。"
    "2) 可追问：想对谁唱、场景意象、一两句必须出现的词；不要堆砌口号。"
    "3) 信息足够时输出确认卡 ```confirm ... ```，字段："
    "title, engine=minimax, mode=parametric, prompt(人声风格/情绪，短), lyrics(必填，带 [Verse]/[Chorus] 等标签), "
    "tags, instrumental=false, genre, bpm, mood, estimated_credits(约 15), summary, "
    "melody_asset_id(必填 UUID 字符串), workflow=\"lyrics_on_melody\"。"
    "4) 歌词：短句、留白、跟着腔走；主歌字少；总字数尽量 ≤700；禁止密密麻麻朗诵长句；遵守提示词库作词准则。"
    "5) 说明：确认后会按该旋律贴人声（Music Cover，失败则同风格短句人声保底）。确认前不扣积分。"
    "6) 禁止版权承诺与声音克隆。"
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
    "general": "你好。我是对话式创作助手。先告诉我：曲风/情绪，还是只有一句灵感？默认先出纯音乐旋律，满意再填词。确认前不扣积分。",
    "melody": "你好，我是「旋律创作 Agent」。只出纯音乐：说下曲风、情绪、主奏乐器或场景即可。确认后生成无人声 Take，满意再交给作词 Agent。",
    "lyrics": "你好，我是「作词 Agent」。请先指定一首旋律 Take（可从旋律 Agent 点「交给作词」），再说想对谁唱、什么意象。确认后按旋律填词并贴人声。",
}

TITLE_BY_KIND = {
    "general": "对话创作",
    "melody": "旋律创作",
    "lyrics": "作词填词",
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
    raw_lyrics = str(data.get("lyrics") or "").strip()
    melody_asset_id = str(data.get("melody_asset_id") or data.get("parent_asset_id") or "").strip()

    if kind == "melody":
        instrumental = True
        raw_lyrics = ""
        workflow = "melody_only"
        credits = {"minimax": 10, "suno": 20, "mureka": 50}.get(engine, 10)
    elif kind == "lyrics":
        instrumental = False
        workflow = "lyrics_on_melody"
        credits = 15
        engine = "minimax"
        if not raw_lyrics:
            return None
    else:
        if "instrumental" in data:
            instrumental = bool(data.get("instrumental"))
        else:
            instrumental = not bool(raw_lyrics)
        if not raw_lyrics:
            instrumental = True
        workflow = "lyrics_on_melody" if (melody_asset_id and raw_lyrics) else ("melody_first" if instrumental else "vocal_direct")
        credits = {"minimax": 10, "suno": 20, "mureka": 50}.get(engine, 10)
        if workflow == "lyrics_on_melody":
            credits = 15

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
    elif kind == "lyrics" and not prompt.strip():
        prompt = "保留参考旋律骨架；短句换气，跟着腔唱，禁止念词。"

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
    if kind == "melody" and "旋律" not in summary:
        summary = f"{summary}；纯音乐旋律草案，满意后交给作词 Agent。"[:500]
    if kind == "lyrics" and "填词" not in summary:
        summary = f"{summary}；按选定旋律填词并贴人声。"[:500]

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
    melody_id = str(draft.get("melody_asset_id") or "").strip()
    welcome = WELCOME_BY_KIND[kind]
    if kind == "lyrics" and melody_id:
        welcome = (
            f"已带入旋律 Take `{melody_id}`。"
            "说说想对谁唱、什么意象即可；确认后按这首旋律填词并贴人声。确认前不扣积分。"
        )
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
    # Inject melody binding for lyrics agent
    draft = session.draft or {}
    melody_id = str(draft.get("melody_asset_id") or "").strip()
    if kind == "lyrics" and melody_id:
        history.insert(
            1,
            {
                "role": "system",
                "content": f"当前绑定旋律 melody_asset_id={melody_id}。确认卡必须带上该字段。",
            },
        )
    return history


def finalize_assistant_reply(db: Session, session: AgentSession, reply: str) -> AgentSession:
    kind = normalize_agent_kind(getattr(session, "kind", None))
    confirm = _extract_confirm(reply, kind=kind)
    # Preserve bound melody id if model omitted it
    if confirm and kind == "lyrics":
        bound = str((session.draft or {}).get("melody_asset_id") or "").strip()
        if bound and not confirm.get("melody_asset_id"):
            confirm["melody_asset_id"] = bound
    visible = _visible_reply(reply)
    assistant_msg = {
        "role": "assistant",
        "content": visible or "我整理了一份可生成方案，请确认后开始。",
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
