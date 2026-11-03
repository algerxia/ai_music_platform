"""Agent, tools, discover, mine, uploads, MV routes."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.agent import (
    append_user_message,
    create_session,
    deliver_assets_reply,
    ensure_agent_project,
    finalize_assistant_reply,
    is_delivery_request,
    iter_assistant_stream,
    normalize_agent_kind,
    post_message,
    session_public,
    stream_display_delta,
)
from app.db import SessionLocal
from app.capabilities import CAPABILITY_SCHEMA_VERSION, CREDIT_COSTS
from app.config import settings
from app.credits import hold_credits
from app.db import get_db
from app.models import (
    Album,
    AlbumItem,
    Asset,
    GenerationJob,
    Like,
    MvProject,
    Project,
    Team,
    TeamMember,
    Upload,
    User,
)
from app.queue import enqueue_job
from app.rh_engines import audio_worker_health
from app.schemas import job_public
from app.security import get_current_user
from app.storage import data_root, upload_file

router = APIRouter()


class AgentMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class AgentConfirmBody(BaseModel):
    idempotency_key: str | None = None


class ToolJobBody(BaseModel):
    tool: str
    prompt: str = "tool"
    storage_key: str = ""
    storage_keys: list[str] = Field(default_factory=list)
    midi_key: str = ""
    options: dict = Field(default_factory=dict)
    lyrics: str = ""
    title: str = ""
    sep_mode: str = "vocal"
    backend: str = "runninghub"
    project_id: UUID | None = None
    idempotency_key: str | None = None
    parent_asset_id: UUID | None = None
    asset_id: UUID | None = None
    asset_ids: list[UUID] = Field(default_factory=list)


def resolve_owned_asset(db: Session, user: User, asset_id: UUID | None) -> Asset | None:
    if not asset_id:
        return None
    asset = db.get(Asset, asset_id)
    if not asset or asset.deleted_at or asset.user_id != user.id:
        raise HTTPException(404, "素材不存在")
    if not asset.storage_key:
        raise HTTPException(400, "该素材没有可处理的音频文件")
    path = data_root() / asset.storage_key
    if not path.exists():
        raise HTTPException(400, "素材文件缺失")
    return asset


class AlbumCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = ""
    is_public: bool = False


class TeamCreate(BaseModel):
    name: str = Field(min_length=2, max_length=20)
    description: str = ""
    industry: str = ""


class TeamJoin(BaseModel):
    invite_code: str = Field(min_length=6, max_length=16)


class MvCreate(BaseModel):
    asset_id: UUID | None = None
    title: str = "未命名 MV"
    lyrics: str = ""
    storage_key: str = ""


def toolbox_project(db: Session, user: User) -> Project:
    project = db.scalar(
        select(Project).where(
            Project.owner_user_id == user.id,
            Project.purpose == "工具箱",
            Project.deleted_at.is_(None),
        )
    )
    if project:
        return project
    project = Project(
        owner_user_id=user.id,
        name="工具箱",
        purpose="工具箱",
        prompt="工具任务容器",
        tone="mint",
        icon="🧰",
    )
    db.add(project)
    db.flush()
    return project


def cost_for_tool(tool: str, backend: str = "") -> int:
    mapping = {
        "ai_lyrics": CREDIT_COSTS["lyrics"],
        "lyrics": CREDIT_COSTS["lyrics"],
        "separation": CREDIT_COSTS["stem_demucs"] if backend == "demucs" else CREDIT_COSTS["stem_rh"],
        "stem_separate": CREDIT_COSTS["stem_demucs"] if backend == "demucs" else CREDIT_COSTS["stem_rh"],
        "music_to_midi": CREDIT_COSTS["midi"],
        "midi_convert": CREDIT_COSTS["midi"],
        "midi_to_score": CREDIT_COSTS["score"],
        "score_convert": CREDIT_COSTS["score"],
        "denoise": CREDIT_COSTS["denoise"],
        "improve": CREDIT_COSTS["improve"],
        "crop": CREDIT_COSTS["crop"],
        "merge": CREDIT_COSTS["merge"],
        "convert": CREDIT_COSTS["convert"],
        "audio_convert": CREDIT_COSTS["convert"],
        "music_analysis": CREDIT_COSTS["analyze"],
        "analyze": CREDIT_COSTS["analyze"],
        "mv_render": CREDIT_COSTS["mv_render"],
        "sfx": CREDIT_COSTS["sfx"],
        "ai_cover": CREDIT_COSTS["vocal_cover"],
        "auto_lyrics": CREDIT_COSTS["vocal_cover"],
    }
    return int(mapping.get(tool, 10))


@router.get("/api/system/audio-worker")
def audio_worker_status():
    return {"data": {"ok": audio_worker_health(), "url": settings.audio_worker_url}}


@router.post("/api/uploads")
async def upload_audio(
    file: UploadFile = File(...),
    purpose: str = Query("tool"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(400, "文件过大（上限 200MB）")
    suffix = Path(file.filename or "upload.bin").suffix or ".bin"
    tmp = data_root() / "uploads" / f"{uuid.uuid4()}{suffix}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw)
    key = upload_file(tmp, content_type=file.content_type or "application/octet-stream")
    row = Upload(
        user_id=user.id,
        filename=file.filename or tmp.name,
        content_type=file.content_type or "",
        storage_key=key,
        file_size_bytes=len(raw),
        purpose=purpose[:64],
    )
    db.add(row)
    db.commit()
    return {
        "data": {
            "id": str(row.id),
            "storage_key": key,
            "filename": row.filename,
            "size": len(raw),
        }
    }


class AgentCreateBody(BaseModel):
    kind: str = "general"
    title: str = ""
    melody_asset_id: UUID | None = None


@router.post("/api/agent/sessions")
def agent_create(
    body: AgentCreateBody | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not settings.dashscope_api_key:
        raise HTTPException(503, "对话式生成需要配置百炼 Qwen Key")
    payload = body or AgentCreateBody()
    kind = normalize_agent_kind(payload.kind)
    seed: dict = {"agent_kind": kind}
    if payload.melody_asset_id:
        parent = resolve_owned_asset(db, user, payload.melody_asset_id)
        seed["melody_asset_id"] = str(parent.id)
        seed["title"] = (parent.title or "").split("—")[0].strip() or payload.title or "按旋律填词"
    session = create_session(
        db,
        user,
        kind=kind,
        title=payload.title or None,
        seed_draft=seed,
    )
    return {"data": session_public(session)}


@router.get("/api/agent/sessions")
def agent_list(
    kind: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models import AgentSession

    q = select(AgentSession).where(AgentSession.user_id == user.id)
    if kind:
        q = q.where(AgentSession.kind == normalize_agent_kind(kind))
    rows = db.scalars(q.order_by(AgentSession.updated_at.desc()).limit(limit)).all()
    return {
        "data": [
            {
                "id": str(s.id),
                "title": s.title,
                "kind": normalize_agent_kind(getattr(s, "kind", None)),
                "state": s.state,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in rows
        ]
    }


@router.get("/api/agent/sessions/{session_id}")
def agent_get(session_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models import AgentSession

    session = db.get(AgentSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    result_assets = []
    job_id = session.confirmed_job_id
    if job_id:
        job = db.get(GenerationJob, job_id)
        if job and job.status == "succeeded":
            takes = db.scalars(
                select(Asset)
                .where(
                    Asset.job_id == job.id,
                    Asset.deleted_at.is_(None),
                    Asset.asset_type.in_(("audio", "stem", "mv")),
                )
                .order_by(Asset.version.asc())
            ).all()
            result_assets = [
                {
                    "id": str(a.id),
                    "title": a.title,
                    "duration": a.duration_seconds,
                    "format": a.format,
                    "assetType": a.asset_type or "audio",
                    "fileSize": a.file_size_bytes or 0,
                    "audioUrl": f"/api/assets/{a.id}/content",
                    "url": f"/api/assets/{a.id}/content",
                    "preview": "video"
                    if (a.asset_type == "mv" or (a.format or "").lower() in {"mp4", "webm", "mov"})
                    else "audio",
                }
                for a in takes
            ]
            if session.state in {"generating", "awaiting_confirm"} and result_assets:
                session.state = "ready"
                draft = dict(session.draft or {})
                draft["result_assets"] = result_assets
                draft["result_asset_id"] = result_assets[0]["id"]
                session.draft = draft
                # Backfill a playable bubble if the completion message was never written
                msgs = list(session.messages or [])
                has_assets_msg = any(isinstance(m, dict) and m.get("assets") for m in msgs)
                if not has_assets_msg:
                    title = (session.draft or {}).get("title") or session.title or "作品"
                    msgs.append(
                        {
                            "role": "assistant",
                            "content": f"《{title}》已生成完成，可直接在下方试听。",
                            "at": datetime.now(timezone.utc).isoformat(),
                            "assets": result_assets,
                            "asset_id": result_assets[0]["id"],
                            "project_id": str(session.project_id) if session.project_id else None,
                        }
                    )
                    session.messages = msgs
                    flag_modified(session, "messages")
                    flag_modified(session, "draft")
                db.commit()
                db.refresh(session)
        elif job and job.status in {"failed", "canceled"} and session.state == "generating":
            session.state = "failed"
            db.commit()
            db.refresh(session)
    return {"data": session_public(session, result_assets=result_assets or None)}


@router.post("/api/agent/sessions/{session_id}/messages")
def agent_message(
    session_id: UUID,
    body: AgentMessageBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models import AgentSession

    session = db.get(AgentSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    try:
        session = post_message(db, session, body.content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)[:240]) from exc
    return {"data": session_public(session)}


@router.post("/api/agent/sessions/{session_id}/messages/stream")
def agent_message_stream(
    session_id: UUID,
    body: AgentMessageBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE stream: delta events then a final done payload with session state."""
    import json as _json

    from app.models import AgentSession

    session = db.get(AgentSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(404, "会话不存在")

    # Fast path: 「发我 / 听听」直接附播放器，不绕模型推诿
    if is_delivery_request(body.content):
        try:
            append_user_message(db, session, body.content)
            delivered = deliver_assets_reply(db, session, user, user_text=body.content)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, str(exc)[:240]) from exc
        if delivered is not None:
            payload = session_public(delivered)
            assets = (payload.get("messages") or [{}])[-1].get("assets") or payload.get("result_assets") or []
            text = (payload.get("messages") or [{}])[-1].get("content") or "已附上。"

            def delivery_gen():
                yield f"data: {_json.dumps({'type': 'start', 'session_id': str(session_id)}, ensure_ascii=False)}\n\n"
                yield f"data: {_json.dumps({'type': 'delta', 'text': text}, ensure_ascii=False)}\n\n"
                if assets:
                    yield f"data: {_json.dumps({'type': 'assets', 'assets': assets}, ensure_ascii=False)}\n\n"
                yield f"data: {_json.dumps({'type': 'done', 'session': payload}, ensure_ascii=False)}\n\n"

            return StreamingResponse(
                delivery_gen(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

    try:
        history = append_user_message(db, session, body.content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)[:240]) from exc

    session_id_str = str(session.id)
    user_id = user.id

    def event_gen():
        buffer = ""
        try:
            yield f"data: {_json.dumps({'type': 'start', 'session_id': session_id_str}, ensure_ascii=False)}\n\n"
            for delta in iter_assistant_stream(history):
                buffer, visible = stream_display_delta(buffer, delta)
                if visible:
                    yield f"data: {_json.dumps({'type': 'delta', 'text': visible}, ensure_ascii=False)}\n\n"
            with SessionLocal() as save_db:
                row = save_db.get(AgentSession, session_id)
                if not row or row.user_id != user_id:
                    yield f"data: {_json.dumps({'type': 'error', 'message': '会话丢失'}, ensure_ascii=False)}\n\n"
                    return
                row = finalize_assistant_reply(save_db, row, buffer)
                payload = session_public(row)
            yield f"data: {_json.dumps({'type': 'done', 'session': payload}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            # Restore clarifying state so the UI can retry
            try:
                with SessionLocal() as err_db:
                    row = err_db.get(AgentSession, session_id)
                    if row and row.user_id == user_id:
                        row.state = "clarifying"
                        err_db.commit()
            except Exception:  # noqa: BLE001
                pass
            yield f"data: {_json.dumps({'type': 'error', 'message': str(exc)[:240]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class AgentRetryBody(BaseModel):
    engine: str = "minimax"
    draft: dict | None = None


@router.post("/api/agent/sessions/{session_id}/retry")
def agent_retry(
    session_id: UUID,
    body: AgentRetryBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """After a failed generation, reopen confirm with another engine (default MiniMax)."""
    from app.models import AgentSession

    session = db.get(AgentSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    draft = dict(body.draft or session.draft or {})
    if not draft.get("prompt"):
        raise HTTPException(409, "没有可重试的方案，请先在对话里确认一版")
    engine = (body.engine or draft.get("engine") or "minimax").lower()
    if engine not in {"minimax", "suno", "mureka"}:
        engine = "minimax"
    draft["engine"] = engine
    draft["estimated_credits"] = {
        "minimax": settings.music_cost_credits,
        "suno": settings.suno_credit_cost,
        "mureka": settings.mureka_credit_cost,
    }.get(engine, settings.music_cost_credits)
    session.draft = draft
    session.state = "awaiting_confirm"
    session.confirmed_job_id = None
    messages = list(session.messages or [])
    messages.append(
        {
            "role": "assistant",
            "content": f"已把引擎改为 { {'minimax': 'MiniMax', 'suno': '苏诺', 'mureka': 'Mureka'}.get(engine, engine) }，请确认后重新生成。",
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    session.messages = messages
    flag_modified(session, "messages")
    flag_modified(session, "draft")
    db.commit()
    db.refresh(session)
    return {"data": session_public(session)}


@router.post("/api/agent/sessions/{session_id}/confirm")
def agent_confirm(
    session_id: UUID,
    body: AgentConfirmBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Confirmation gate: only now create a paid generation job."""
    from app.models import AgentSession

    session = db.get(AgentSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    draft = session.draft or {}
    kind = normalize_agent_kind(getattr(session, "kind", None) or draft.get("agent_kind"))
    # Lyrics agent / fill-on-melody confirm → Music Cover path
    if kind == "lyrics" or draft.get("workflow") == "lyrics_on_melody":
        if session.state != "awaiting_confirm":
            raise HTTPException(409, "当前没有待确认的作词方案")
        melody_raw = draft.get("melody_asset_id") or draft.get("parent_asset_id")
        if not melody_raw:
            raise HTTPException(409, "作词方案缺少旋律 Take，请先从旋律 Agent 交过来")
        if not str(draft.get("lyrics") or "").strip():
            raise HTTPException(409, "作词方案缺少歌词")
        try:
            melody_uuid = UUID(str(melody_raw))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, "melody_asset_id 无效") from exc
        return agent_fill_lyrics_on_melody(
            session_id,
            MelodyFillLyricsBody(
                asset_id=melody_uuid,
                lyrics=str(draft.get("lyrics") or ""),
                prompt=str(draft.get("prompt") or ""),
                instruction="按确认卡歌词贴唱",
                idempotency_key=body.idempotency_key,
            ),
            user=user,
            db=db,
        )

    if session.state != "awaiting_confirm" or not draft.get("prompt"):
        raise HTTPException(409, "当前没有待确认的生成方案")
    # Melody agent always instrumental
    if kind == "melody":
        draft = dict(draft)
        draft["instrumental"] = True
        draft["lyrics"] = ""
        draft["workflow"] = "melody_only"
        session.draft = draft
        flag_modified(session, "draft")
    project = ensure_agent_project(db, user, draft)
    session.project_id = project.id
    db.commit()

    engine = str(draft.get("engine") or "minimax").lower()
    mode = str(draft.get("mode") or "parametric").lower()
    instrumental = bool(draft.get("instrumental", True))
    lyrics = str(draft.get("lyrics") or "")
    if engine == "suno" and mode == "text":
        instrumental = bool(draft.get("instrumental", False))
    if engine in {"suno", "mureka"} and mode == "custom" and not lyrics.strip():
        lyrics = str(draft.get("prompt") or "")
    if kind == "melody":
        instrumental = True
        lyrics = ""
    prompt = str(draft.get("prompt") or draft.get("summary") or "对话创作")[:6000]
    idempotency_key = body.idempotency_key or f"agent-{session.id}-{uuid.uuid4()}"
    context = {
        "engine": engine,
        "mode": mode,
        "tags": draft.get("tags") or "",
        "title": draft.get("title") or project.name,
        "genre": draft.get("genre") or [],
        "bpm": draft.get("bpm") or 90,
        "mood": draft.get("mood") or "",
        "from_agent": True,
    }
    if not settings.runninghub_api_key:
        raise HTTPException(503, "音乐服务未配置")
    cost = {
        "minimax": settings.music_cost_credits,
        "suno": settings.suno_credit_cost,
        "mureka": settings.mureka_credit_cost,
    }.get(engine, settings.music_cost_credits)
    if user.credit_balance < cost:
        raise HTTPException(402, f"积分不足（需要 {cost}，当前 {user.credit_balance}）")
    job = GenerationJob(
        project_id=project.id,
        user_id=user.id,
        job_type="agent_generate",
        status="queued",
        progress_stage="queued",
        prompt=prompt if len(prompt) >= 8 else f"{prompt} ambient tone",
        params={
            "format": "mp3",
            "instrumental": instrumental,
            "lyrics": lyrics,
            "engine": engine,
            "mode": mode,
            "tags": draft.get("tags") or "",
            "title": draft.get("title") or project.name,
            "context": context,
        },
        idempotency_key=idempotency_key,
        provider="runninghub",
        model=engine if engine != "minimax" else settings.music_model,
        capability_schema_version=CAPABILITY_SCHEMA_VERSION,
        credit_hold=cost,
    )
    db.add(job)
    db.flush()
    hold_credits(db, user, cost, project_name=project.name, job_id=job.id, key=str(job.id))
    session.state = "generating"
    session.confirmed_job_id = job.id
    messages = list(session.messages or [])
    messages.append(
        {
            "role": "assistant",
            "content": (
                f"已确认，正在用 "
                f"{ {'minimax': 'MiniMax', 'suno': '苏诺', 'mureka': 'Mureka'}.get(engine, engine) } "
                f"生成（预留 {cost} 积分）。可在项目「{project.name}」查看 Takes。"
            ),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    session.messages = messages
    db.commit()
    enqueue_job(str(job.id))
    return {"data": {"session": session_public(session), "job": job_public(job), "project_id": str(project.id)}}


class MelodyFillLyricsBody(BaseModel):
    asset_id: UUID
    instruction: str = Field(default="", max_length=800)
    lyrics: str = Field(default="", max_length=1000)
    prompt: str = Field(default="", max_length=2000)
    idempotency_key: str | None = None


@router.post("/api/agent/sessions/{session_id}/fill-lyrics")
def agent_fill_lyrics_on_melody(
    session_id: UUID,
    body: MelodyFillLyricsBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 2 of melody-first: write sparse lyrics for a chosen instrumental Take, then Music Cover."""
    from app.models import AgentSession
    from app.providers import write_lyrics

    session = db.get(AgentSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    parent = resolve_owned_asset(db, user, body.asset_id)
    if not parent:
        raise HTTPException(404, "找不到这首旋律 Take")
    path = data_root() / parent.storage_key
    if not path.exists():
        raise HTTPException(400, "旋律音频文件缺失")
    if not settings.runninghub_api_key:
        raise HTTPException(503, "音乐服务未配置")

    draft = dict(session.draft or {})
    project = None
    if session.project_id:
        project = db.get(Project, session.project_id)
    if not project or getattr(project, "owner_user_id", None) != user.id:
        project = ensure_agent_project(db, user, {**draft, "title": draft.get("title") or parent.title or "对话创作"})
        session.project_id = project.id

    style_prompt = (
        body.prompt.strip()
        or str(draft.get("prompt") or "")
        or parent.prompt
        or project.prompt
        or "温暖可唱的旋律，保留原曲骨架"
    )[:2000]
    lyrics = (body.lyrics or "").strip()
    if len(lyrics) < 10:
        lyrics = write_lyrics(
            style_prompt,
            duration_seconds=parent.duration_seconds or project.duration_seconds or 90,
            bpm=parent.bpm or project.bpm,
            key=parent.key_signature or project.key_signature or "",
            mood=str(draft.get("mood") or project.mood or ""),
            structure=project.structure or "",
            genre=draft.get("genre") or project.genre or [],
            existing_lyrics=str(draft.get("lyrics") or parent.lyrics or ""),
            instruction=(
                body.instruction.strip()
                or "根据已有纯音乐旋律填词：短句、留白、跟着腔走，不要念词腔，总字数偏少"
            ),
            title=str(draft.get("title") or project.name or ""),
        )
    lyrics = lyrics.strip()[:720]
    if "\n" in lyrics[600:]:
        lyrics = lyrics[:720].rsplit("\n", 1)[0].strip()
    if len(lyrics) < 10:
        raise HTTPException(500, "自动填词失败，请补充歌词意向后再试")

    cost = CREDIT_COSTS["vocal_cover"]
    if user.credit_balance < cost:
        raise HTTPException(402, f"积分不足（需要 {cost}，当前 {user.credit_balance}）")

    cover_prompt = (
        f"{style_prompt}\n保留参考纯音乐的旋律与编曲骨架；按歌词贴合唱，短句换气，"
        f"像真人歌手跟着旋律唱，禁止朗诵念词。"
    )[:2000]
    idempotency_key = body.idempotency_key or f"fill-lyrics-{session.id}-{parent.id}-{uuid.uuid4()}"
    job = GenerationJob(
        project_id=project.id,
        user_id=user.id,
        job_type="vocal_cover",
        status="queued",
        progress_stage="queued",
        prompt=cover_prompt if len(cover_prompt) >= 8 else f"{cover_prompt} melodic vocal",
        params={
            "format": "mp3",
            "instrumental": False,
            "lyrics": lyrics,
            "vocal_cover": True,
            "engine": "minimax",
            "title": draft.get("title") or project.name,
            "context": {
                "from_agent": True,
                "workflow": "melody_then_lyrics",
                "parent_asset_id": str(parent.id),
                "engine": "minimax",
            },
        },
        idempotency_key=idempotency_key,
        provider="runninghub",
        model="minimax-music-cover",
        capability_schema_version=CAPABILITY_SCHEMA_VERSION,
        credit_hold=cost,
        parent_asset_id=parent.id,
    )
    db.add(job)
    db.flush()
    hold_credits(db, user, cost, project_name=project.name, job_id=job.id, key=str(job.id))

    draft["instrumental"] = False
    draft["lyrics"] = lyrics
    draft["workflow_stage"] = "filling_lyrics"
    draft["melody_asset_id"] = str(parent.id)
    session.draft = draft
    session.state = "generating"
    session.confirmed_job_id = job.id
    messages = list(session.messages or [])
    messages.append(
        {
            "role": "assistant",
            "content": (
                f"已按《{parent.title or project.name}》的旋律写好词，正在贴人声（预留 {cost} 积分）。\n"
                f"歌词预览：\n{lyrics[:480]}"
            ),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    session.messages = messages
    flag_modified(session, "messages")
    flag_modified(session, "draft")
    db.commit()
    enqueue_job(str(job.id))
    return {
        "data": {
            "session": session_public(session),
            "job": job_public(job),
            "lyrics": lyrics,
            "parent_asset_id": str(parent.id),
            "project_id": str(project.id),
        }
    }


class CoverToolBody(BaseModel):
    storage_key: str = ""
    asset_id: UUID | None = None
    lyrics: str = Field(min_length=10, max_length=1000)
    prompt: str = Field(default="cover with new vocals", max_length=2000)
    title: str = "AI 翻唱"
    idempotency_key: str | None = None


@router.post("/api/tools/cover")
def tool_cover(body: CoverToolBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Upload-backed Music Cover without leaving toolbox."""
    if not settings.runninghub_api_key:
        raise HTTPException(503, "音乐服务未配置")
    owned = resolve_owned_asset(db, user, body.asset_id) if body.asset_id else None
    storage_key = owned.storage_key if owned else body.storage_key
    if not storage_key:
        raise HTTPException(400, "请上传文件或选择已有作品")
    path = data_root() / storage_key
    if not path.exists():
        raise HTTPException(400, "上传文件不存在")
    project = toolbox_project(db, user)
    # Reuse owned asset as parent when possible; otherwise seed upload reference.
    if owned:
        parent = owned
        parent.lyrics = body.lyrics or parent.lyrics
        db.flush()
    else:
        parent = Asset(
            project_id=project.id,
            user_id=user.id,
            title=f"{body.title} · 参考",
            status="ready",
            format=path.suffix.lstrip(".") or "mp3",
            storage_key=storage_key,
            provider="upload",
            asset_type="audio",
            file_size_bytes=path.stat().st_size,
            lyrics=body.lyrics,
            selected=False,
            version=1,
            meta={"origin": "upload_reference"},
        )
        db.add(parent)
        db.flush()
    cost = CREDIT_COSTS["vocal_cover"]
    job = GenerationJob(
        project_id=project.id,
        user_id=user.id,
        job_type="vocal_cover",
        status="queued",
        progress_stage="queued",
        prompt=body.prompt if len(body.prompt) >= 8 else f"{body.prompt} vocal cover style",
        params={
            "format": "mp3",
            "instrumental": False,
            "lyrics": body.lyrics,
            "vocal_cover": True,
            "engine": "minimax",
            "mode": "cover",
            "context": {"engine": "minimax", "mode": "cover"},
        },
        idempotency_key=body.idempotency_key,
        provider="runninghub",
        model="minimax-music-cover",
        capability_schema_version=CAPABILITY_SCHEMA_VERSION,
        credit_hold=cost,
        parent_asset_id=parent.id,
    )
    db.add(job)
    db.flush()
    hold_credits(db, user, cost, project_name=project.name, job_id=job.id, key=str(job.id))
    db.commit()
    enqueue_job(str(job.id))
    return {"data": job_public(job)}


@router.post("/api/tools/jobs")
def create_tool_job(body: ToolJobBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.get(Project, body.project_id) if body.project_id else None
    if project and (project.owner_user_id != user.id or project.deleted_at):
        raise HTTPException(404, "项目不存在")
    if not project:
        project = toolbox_project(db, user)
    storage_key = body.storage_key
    storage_keys = list(body.storage_keys or [])
    midi_key = body.midi_key
    parent_asset_id = body.parent_asset_id
    if body.asset_id:
        owned = resolve_owned_asset(db, user, body.asset_id)
        storage_key = owned.storage_key
        parent_asset_id = parent_asset_id or owned.id
        if not midi_key:
            midi_key = owned.storage_key
    for aid in body.asset_ids or []:
        owned = resolve_owned_asset(db, user, aid)
        if owned.storage_key not in storage_keys:
            storage_keys.append(owned.storage_key)
        if not storage_key:
            storage_key = owned.storage_key
            parent_asset_id = parent_asset_id or owned.id
    if body.tool in {
        "separation",
        "stem_separate",
        "music_to_midi",
        "midi_convert",
        "midi_to_score",
        "score_convert",
        "denoise",
        "improve",
        "crop",
        "convert",
        "audio_convert",
        "mv_render",
    } and not storage_key:
        raise HTTPException(400, "请上传文件或选择已有作品")
    if body.tool == "merge" and len(storage_keys) < 2:
        raise HTTPException(400, "合并需要至少两段音频（上传或选择作品）")
    cost = cost_for_tool(body.tool, body.backend)
    if cost > 0 and user.credit_balance < cost:
        raise HTTPException(402, "积分不足")
    job = GenerationJob(
        project_id=project.id,
        user_id=user.id,
        job_type=body.tool,
        status="queued",
        progress_stage="queued",
        prompt=(body.prompt or body.tool)[:6000] if len(body.prompt or "") >= 8 else f"{body.tool} task detail",
        params={
            "tool": body.tool,
            "storage_key": storage_key,
            "storage_keys": storage_keys,
            "midi_key": midi_key or storage_key,
            "options": body.options,
            "lyrics": body.lyrics,
            "title": body.title,
            "sep_mode": body.sep_mode,
            "backend": body.backend,
        },
        idempotency_key=body.idempotency_key,
        provider="tool",
        model=body.tool,
        capability_schema_version=CAPABILITY_SCHEMA_VERSION,
        credit_hold=cost,
        parent_asset_id=parent_asset_id,
    )
    db.add(job)
    db.flush()
    if cost > 0:
        hold_credits(db, user, cost, project_name=project.name, job_id=job.id, key=str(job.id))
    db.commit()
    enqueue_job(str(job.id))
    return {"data": job_public(job)}


@router.get("/api/discover/plaza")
def discover_plaza(db: Session = Depends(get_db), limit: int = Query(40, ge=1, le=100)):
    albums = db.scalars(select(Album).where(Album.is_public.is_(True)).order_by(Album.updated_at.desc()).limit(limit)).all()
    public_assets = db.scalars(
        select(Asset)
        .where(
            Asset.deleted_at.is_(None),
            Asset.asset_type.in_(("audio", "mv")),
            Asset.is_public.is_(True),
            Asset.storage_key != "",
        )
        .order_by(Asset.created_at.desc())
        .limit(limit)
    ).all()
    return {
        "data": {
            "albums": [
                {"id": str(a.id), "title": a.title, "description": a.description, "cover_url": a.cover_url}
                for a in albums
            ],
            "tracks": [
                {
                    "id": str(a.id),
                    "title": a.title,
                    "project_id": str(a.project_id),
                    "duration_seconds": a.duration_seconds,
                    "audioUrl": f"/api/assets/{a.id}/content",
                    "is_public": bool(getattr(a, "is_public", False)),
                }
                for a in public_assets
            ],
        }
    }


@router.get("/api/creations")
def list_creations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(40, ge=1, le=100),
):
    rows = db.scalars(
        select(Asset)
        .where(
            Asset.user_id == user.id,
            Asset.deleted_at.is_(None),
            Asset.asset_type.in_(("audio", "stem", "mv", "midi", "lyrics", "analysis")),
        )
        .order_by(Asset.created_at.desc())
        .limit(limit)
    ).all()
    from app.schemas import asset_public

    return {"data": [asset_public(a) for a in rows]}


class QuickGenerateBody(BaseModel):
    title: str = ""
    prompt: str = Field(min_length=4, max_length=6000)
    lyrics: str = ""
    tags: str = ""
    engine: str = "minimax"
    mode: str = "text"
    instrumental: bool = True
    bpm: int = 90
    key: str = "D minor"
    duration_seconds: int = 90
    genre: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


@router.post("/api/create/quick")
def quick_generate(body: QuickGenerateBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One-shot parametric create: auto project + job."""
    if not settings.runninghub_api_key:
        raise HTTPException(503, "音乐服务未配置")
    engine = (body.engine or "minimax").lower()
    mode = (body.mode or "text").lower()
    if mode == "sfx":
        engine = "minimax"
        mode = "text"
        instrumental = True
        prompt = f"短音效 / SFX，10 秒内，{body.prompt}"[:6000]
    else:
        instrumental = body.instrumental
        prompt = body.prompt
    lyrics = body.lyrics
    if engine == "suno" and mode == "custom" and not lyrics.strip():
        raise HTTPException(400, "自定义模式需要歌词")
    if engine == "minimax" and mode in {"parametric", "custom"} and not instrumental and not lyrics.strip():
        raise HTTPException(400, "人声模式需要歌词")
    if engine == "mureka" and not lyrics.strip():
        lyrics = body.prompt
    title = (body.title or prompt[:24] or "参数式创作").strip()[:120]
    project = Project(
        owner_user_id=user.id,
        name=title,
        purpose="参数式生成" if mode != "sfx" else "音效",
        prompt=prompt,
        lyrics=lyrics,
        genre=body.genre or [],
        bpm=max(40, min(240, int(body.bpm or 90))),
        key_signature=body.key or "D minor",
        duration_seconds=int(body.duration_seconds or 90),
        vocal_mode="instrumental" if instrumental else "vocal",
        tone="sage",
        icon="◈",
    )
    db.add(project)
    db.flush()
    cost = {
        "minimax": settings.music_cost_credits,
        "suno": settings.suno_credit_cost,
        "mureka": settings.mureka_credit_cost,
    }.get(engine, settings.music_cost_credits)
    if mode == "sfx":
        cost = CREDIT_COSTS.get("sfx", 4)
    job = GenerationJob(
        project_id=project.id,
        user_id=user.id,
        job_type="initial_generate",
        status="queued",
        progress_stage="queued",
        prompt=prompt if len(prompt) >= 8 else f"{prompt} ambient tone",
        params={
            "format": "mp3",
            "instrumental": instrumental,
            "lyrics": lyrics,
            "engine": engine,
            "mode": "text" if mode == "sfx" else mode,
            "tags": body.tags or "、".join(body.genre) or "pop, melodic",
            "title": title,
            "context": {
                "engine": engine,
                "mode": "text" if mode == "sfx" else mode,
                "genre": body.genre,
                "bpm": body.bpm,
                "key": body.key,
                "duration_seconds": body.duration_seconds,
                "tags": body.tags,
                "title": title,
            },
        },
        idempotency_key=body.idempotency_key,
        provider="runninghub",
        model=engine if engine != "minimax" else settings.music_model,
        capability_schema_version=CAPABILITY_SCHEMA_VERSION,
        credit_hold=cost,
    )
    db.add(job)
    db.flush()
    hold_credits(db, user, cost, project_name=project.name, job_id=job.id, key=str(job.id))
    db.commit()
    enqueue_job(str(job.id))
    return {"data": {"project_id": str(project.id), "job": job_public(job), "cost": cost}}


@router.get("/api/albums")
def list_albums(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Album).where(Album.user_id == user.id).order_by(Album.updated_at.desc())).all()
    return {
        "data": [
            {
                "id": str(a.id),
                "title": a.title,
                "description": a.description,
                "is_public": a.is_public,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ]
    }


@router.post("/api/albums")
def create_album(body: AlbumCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    album = Album(user_id=user.id, title=body.title, description=body.description, is_public=body.is_public)
    db.add(album)
    db.commit()
    db.refresh(album)
    return {"data": {"id": str(album.id), "title": album.title}}


@router.post("/api/albums/{album_id}/items/{asset_id}")
def add_album_item(
    album_id: UUID,
    asset_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    album = db.get(Album, album_id)
    asset = db.get(Asset, asset_id)
    if not album or album.user_id != user.id:
        raise HTTPException(404, "专辑不存在")
    if not asset or asset.user_id != user.id:
        raise HTTPException(404, "作品不存在")
    existing = db.scalar(select(AlbumItem).where(AlbumItem.album_id == album_id, AlbumItem.asset_id == asset_id))
    if not existing:
        db.add(AlbumItem(album_id=album_id, asset_id=asset_id))
        db.commit()
    return {"data": {"ok": True}}


@router.post("/api/likes/{asset_id}")
def toggle_like(asset_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset or asset.deleted_at:
        raise HTTPException(404, "作品不存在")
    row = db.scalar(select(Like).where(Like.user_id == user.id, Like.asset_id == asset_id))
    if row:
        db.delete(row)
        liked = False
    else:
        db.add(Like(user_id=user.id, asset_id=asset_id))
        liked = True
    db.commit()
    return {"data": {"liked": liked}}


@router.get("/api/likes")
def list_likes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Like).where(Like.user_id == user.id).order_by(Like.created_at.desc())).all()
    assets = []
    for like in rows:
        asset = db.get(Asset, like.asset_id)
        if asset and not asset.deleted_at:
            assets.append({"id": str(asset.id), "title": asset.title, "project_id": str(asset.project_id)})
    return {"data": assets}


@router.post("/api/teams")
def create_team(body: TeamCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    code = secrets.token_hex(6).upper()[:12]
    team = Team(
        owner_user_id=user.id,
        name=body.name,
        description=body.description,
        industry=body.industry,
        invite_code=code,
    )
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))
    db.commit()
    return {"data": {"id": str(team.id), "name": team.name, "invite_code": team.invite_code}}


@router.post("/api/teams/join")
def join_team(body: TeamJoin, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team = db.scalar(select(Team).where(Team.invite_code == body.invite_code.strip().upper()))
    if not team:
        raise HTTPException(404, "邀请码无效")
    existing = db.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user.id))
    if not existing:
        db.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
        db.commit()
    return {"data": {"id": str(team.id), "name": team.name}}


@router.get("/api/teams")
def list_teams(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member_rows = db.scalars(select(TeamMember).where(TeamMember.user_id == user.id)).all()
    teams = []
    for m in member_rows:
        team = db.get(Team, m.team_id)
        if team:
            teams.append(
                {
                    "id": str(team.id),
                    "name": team.name,
                    "role": m.role,
                    "invite_code": team.invite_code if m.role == "owner" else "",
                    "credit_balance": team.credit_balance,
                }
            )
    return {"data": teams}


@router.get("/api/stems")
def list_stems(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    rows = db.scalars(
        select(Asset)
        .where(Asset.user_id == user.id, Asset.asset_type == "stem", Asset.deleted_at.is_(None))
        .order_by(Asset.created_at.desc())
    ).all()
    data = []
    changed = False
    for asset in rows:
        expires = (asset.created_at or datetime.now(timezone.utc)) + timedelta(days=7)
        expired = bool(asset.created_at and asset.created_at < cutoff)
        if expired:
            asset.deleted_at = datetime.now(timezone.utc)
            changed = True
        data.append(
            {
                "id": str(asset.id),
                "title": asset.title,
                "created_at": asset.created_at.isoformat() if asset.created_at else None,
                "expires_at": expires.isoformat(),
                "expired": expired,
            }
        )
    if changed:
        db.commit()
    return {"data": [row for row in data if not row["expired"]]}


@router.post("/api/mv")
def create_mv(body: MvCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    storage_key = body.storage_key
    lyrics = body.lyrics
    if body.asset_id:
        asset = db.get(Asset, body.asset_id)
        if not asset or asset.user_id != user.id:
            raise HTTPException(404, "作品不存在")
        storage_key = asset.storage_key
        lyrics = lyrics or asset.lyrics or ""
    if not storage_key:
        raise HTTPException(400, "需要音频素材")
    mv = MvProject(user_id=user.id, asset_id=body.asset_id, title=body.title[:120], lyrics=lyrics)
    db.add(mv)
    db.flush()
    project = toolbox_project(db, user)
    cost = CREDIT_COSTS["mv_render"]
    job = GenerationJob(
        project_id=project.id,
        user_id=user.id,
        job_type="mv_render",
        status="queued",
        progress_stage="queued",
        prompt=f"MV render {body.title}"[:6000],
        params={
            "tool": "mv_render",
            "storage_key": storage_key,
            "lyrics": lyrics,
            "title": body.title,
            "options": {},
        },
        provider="audio-worker",
        model="mv_render",
        credit_hold=cost,
        capability_schema_version=CAPABILITY_SCHEMA_VERSION,
    )
    db.add(job)
    db.flush()
    hold_credits(db, user, cost, project_name="MV", job_id=job.id, key=str(job.id))
    mv.job_id = job.id
    mv.status = "rendering"
    db.commit()
    enqueue_job(str(job.id))
    return {
        "data": {
            "id": str(mv.id),
            "job_id": str(job.id),
            "job": job_public(job),
            "status": mv.status,
        }
    }


@router.get("/api/mv")
def list_mv(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(MvProject).where(MvProject.user_id == user.id).order_by(MvProject.created_at.desc())).all()
    return {
        "data": [
            {
                "id": str(m.id),
                "title": m.title,
                "status": m.status,
                "job_id": str(m.job_id) if m.job_id else None,
                "result_storage_key": m.result_storage_key,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ]
    }
