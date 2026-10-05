from datetime import datetime, timedelta, timezone
from uuid import UUID
import hashlib
import io
import os
import secrets
import uuid
import zipfile

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record
from app.capabilities import CAPABILITY_SCHEMA_VERSION, public_capabilities
from app.config import settings
from app.credits import hold_credits, release_hold
from app.db import get_db, init_db
from app.models import Asset, Consent, CreditLedger, DeletionRequest, GenerationJob, Project, Report, User
from app.plans import PLANS, get_plan
from app.policy import review_generation_text
from app.providers import parse_prompt_controls, qwen_chat, write_lyrics
from app.queue import enqueue_job
from app.schemas import (
    AssetPatch,
    DeletionCreate,
    ForgotPasswordRequest,
    JobCreate,
    LoginRequest,
    LyricsWriteRequest,
    PasswordChange,
    ProfileUpdate,
    ProjectCreate,
    ProjectUpdate,
    ProviderSettingsUpdate,
    QwenAssistRequest,
    RegisterRequest,
    ReportCreate,
    ResetPasswordRequest,
    asset_public,
    job_public,
    project_public,
    user_public,
)
from app.security import create_access_token, get_current_user, hash_password, verify_password
from app.storage import download_bytes, ensure_bucket


app = FastAPI(title="Studio AI API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    try:
        ensure_bucket()
    except Exception as exc:  # noqa: BLE001
        print(f"MinIO bootstrap skipped: {exc}")


def api_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "platform": "docker",
        "qwen_configured": bool(settings.dashscope_api_key),
        "qwen_provider": "阿里云百炼",
        "model": settings.dashscope_model,
        "audio_generation_configured": bool(settings.runninghub_api_key),
        "music_provider": settings.music_provider,
        "music_model": settings.music_model,
        "music_adapter_enabled": bool(settings.runninghub_api_key or settings.dashscope_api_key),
        "music_cost_credits": settings.music_cost_credits,
        "capability_schema_version": CAPABILITY_SCHEMA_VERSION,
        "features": {
            "auth": True,
            "projects": True,
            "async_jobs": True,
            "credits": True,
            "object_storage": True,
            "variation": True,
            "midi": False,
            "stems": False,
            "billing": False,
        },
    }


@app.post("/api/auth/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if not body.accept_terms:
        raise HTTPException(status_code=400, detail="请先同意服务条款")
    if not body.accept_privacy:
        raise HTTPException(status_code=400, detail="请先同意隐私政策")
    email = body.email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="该邮箱已注册")
    user = User(
        email=email,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        plan_code="creator",
        credit_balance=settings.free_trial_credits,
        daw=body.daw.strip()[:64],
        role_label=body.role_label.strip()[:64],
    )
    db.add(user)
    db.flush()
    db.add(Consent(user_id=user.id, consent_type="terms", document_version="1.0", accepted=True))
    db.add(Consent(user_id=user.id, consent_type="privacy", document_version="1.0", accepted=True))
    if body.marketing_opt_in:
        db.add(Consent(user_id=user.id, consent_type="marketing", document_version="1.0", accepted=True))
    db.add(
        CreditLedger(
            user_id=user.id,
            entry_type="grant",
            amount=settings.free_trial_credits,
            balance_after=user.credit_balance,
            label="首次免费额度",
            project_name="Creator 方案",
            status="已发放",
            idempotency_key=f"grant-signup-{user.id}",
        )
    )
    db.commit()
    token = create_access_token(str(user.id), user.email)
    return {"data": {"token": token, "user": user_public(user)}}


@app.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token = create_access_token(str(user.id), user.email)
    return {"data": {"token": token, "user": user_public(user)}}


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@app.post("/api/auth/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if not user or user.status != "active":
        return {"data": {"message": "如果该邮箱已注册，可使用一次性令牌重置密码。本机未接邮件服务。"}}
    token = secrets.token_urlsafe(24)
    user.reset_token_hash = _hash_reset_token(token)
    user.reset_token_expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    db.commit()
    return {
        "data": {
            "message": "本机演示没有邮箱。请立即用下面的一次性令牌重置密码，15 分钟内有效。",
            "reset_token": token,
            "expires_in_minutes": 15,
        }
    }


@app.post("/api/auth/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if not user or not user.reset_token_hash:
        raise HTTPException(status_code=400, detail="重置令牌无效或已过期")
    expires = user.reset_token_expires
    if not expires or expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="重置令牌已过期，请重新申请")
    if user.reset_token_hash != _hash_reset_token(body.token.strip()):
        raise HTTPException(status_code=400, detail="重置令牌无效或已过期")
    user.password_hash = hash_password(body.new_password)
    user.reset_token_hash = ""
    user.reset_token_expires = None
    record(db, actor_id=user.id, action="auth.reset_password", resource_type="user", resource_id=str(user.id))
    db.commit()
    return {"data": {"ok": True}}


@app.get("/api/me")
def me(user: User = Depends(get_current_user)):
    return {"data": user_public(user)}


@app.patch("/api/me")
def update_me(body: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.display_name:
        user.display_name = body.display_name.strip()
    if body.daw is not None:
        user.daw = body.daw.strip()[:64]
    if body.role_label is not None:
        user.role_label = body.role_label.strip()[:64]
    if body.timezone:
        user.timezone = body.timezone.strip()[:64]
    db.commit()
    db.refresh(user)
    record(db, actor_id=user.id, action="profile.update", resource_type="user", resource_id=str(user.id))
    db.commit()
    return {"data": user_public(user)}


@app.post("/api/me/password")
def change_password(body: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"data": {"ok": True}}


@app.get("/api/capabilities")
def capabilities():
    return {
        "data": public_capabilities(
            bool(settings.runninghub_api_key),
            bool(settings.dashscope_api_key),
        )
    }


@app.get("/api/plans")
def list_plans():
    return {"data": PLANS}


@app.get("/api/credits/ledger")
def credit_ledger(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(CreditLedger).where(CreditLedger.user_id == user.id).order_by(CreditLedger.created_at.desc()).limit(100)
    ).all()
    plan = get_plan(user.plan_code)
    return {
        "data": {
            "balance": user.credit_balance,
            "monthly_quota": plan.get("credits") or settings.plan_monthly_credits,
            "entries": [
                {
                    "id": str(row.id),
                    "label": row.label,
                    "project": row.project_name,
                    "date": row.created_at.strftime("%Y-%m-%d"),
                    "amount": row.amount,
                    "status": row.status,
                    "type": row.entry_type,
                }
                for row in rows
            ],
        }
    }


@app.get("/api/projects")
def list_projects(
    request: Request,
    q: str = Query(""),
    status: str = Query("active"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Project).where(Project.owner_user_id == user.id)
    if status == "active":
        stmt = stmt.where(Project.deleted_at.is_(None), Project.status != "archived")
    elif status == "archived":
        stmt = stmt.where(Project.deleted_at.is_(None), Project.status == "archived")
    elif status != "all":
        stmt = stmt.where(Project.deleted_at.is_(None))
    if q.strip():
        stmt = stmt.where(Project.name.ilike(f"%{q.strip()}%"))
    projects = db.scalars(stmt.order_by(Project.updated_at.desc())).all()
    base = api_base(request)
    payload = []
    for project in projects:
        assets = [
            asset_public(asset, base)
            for asset in db.scalars(
                select(Asset)
                .where(Asset.project_id == project.id, Asset.deleted_at.is_(None))
                .order_by(Asset.created_at.desc())
            )
        ]
        pending = db.scalar(
            select(GenerationJob).where(
                GenerationJob.project_id == project.id,
                GenerationJob.status.in_(("queued", "running", "validating", "finalizing")),
            )
        )
        payload.append(project_public(project, assets, job_public(pending) if pending else None))
    return {"data": payload}


@app.post("/api/projects")
def create_project(body: ProjectCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tones = ["sage", "violet", "amber", "blue", "rose", "teal"]
    icons = ["◌", "✳", "〰", "♬"]
    count = db.query(Project).filter(Project.owner_user_id == user.id).count()
    project = Project(
        owner_user_id=user.id,
        name=body.name.strip(),
        purpose=body.purpose,
        prompt=body.prompt,
        tone=tones[count % len(tones)],
        icon=icons[count % len(icons)],
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"data": project_public(project, [], None)}


@app.get("/api/projects/{project_id}")
def get_project(project_id: UUID, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project or project.owner_user_id != user.id or project.deleted_at:
        raise HTTPException(status_code=404, detail="项目不存在")
    base = api_base(request)
    assets = [
        asset_public(asset, base)
        for asset in db.scalars(
            select(Asset).where(Asset.project_id == project.id, Asset.deleted_at.is_(None)).order_by(Asset.created_at.desc())
        )
    ]
    pending = db.scalar(
        select(GenerationJob).where(
            GenerationJob.project_id == project.id,
            GenerationJob.status.in_(("queued", "running", "validating", "finalizing")),
        )
    )
    return {"data": project_public(project, assets, job_public(pending) if pending else None)}


@app.patch("/api/projects/{project_id}")
def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.owner_user_id != user.id or project.deleted_at:
        raise HTTPException(status_code=404, detail="项目不存在")
    data = body.model_dump(exclude_unset=True)
    mapping = {
        "key_signature": "key_signature",
        "duration_seconds": "duration_seconds",
        "vocal_mode": "vocal_mode",
        "negative_prompt": "negative_prompt",
    }
    for key, value in data.items():
        attr = mapping.get(key, key)
        setattr(project, attr, value)
    if "time_signature" in data and data["time_signature"]:
        project.time_signature = str(data["time_signature"])[:16]
    if "notes" in data and data["notes"] is not None:
        project.notes = str(data["notes"])[:4000]
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(project)
    return {"data": project_public(project)}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project or project.owner_user_id != user.id or project.deleted_at:
        raise HTTPException(status_code=404, detail="项目不存在")
    project.status = "deletion_pending"
    project.deleted_at = datetime.now(timezone.utc)
    record(db, actor_id=user.id, action="project.delete", resource_type="project", resource_id=str(project.id))
    db.commit()
    return {"data": {"ok": True}}


@app.post("/api/projects/{project_id}/jobs")
def create_job(
    project_id: UUID,
    body: JobCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    project = db.get(Project, project_id)
    if not project or project.owner_user_id != user.id or project.deleted_at:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.status == "archived":
        raise HTTPException(status_code=409, detail="已归档项目不能生成，请先恢复")
    if not settings.runninghub_api_key:
        raise HTTPException(status_code=503, detail="音乐服务未配置 RUNNINGHUB_API_KEY")
    if body.format.lower() == "wav":
        raise HTTPException(status_code=400, detail="当前模型只提供 MP3 原文件。WAV/MIDI/分轨暂不支持。")

    blocked = review_generation_text(body.prompt, body.lyrics, str(body.context))
    if blocked:
        raise HTTPException(status_code=400, detail=blocked)

    plan = get_plan(user.plan_code)
    active_user = db.scalar(
        select(func.count(GenerationJob.id)).where(
            GenerationJob.user_id == user.id,
            GenerationJob.status.in_(("queued", "running", "validating", "finalizing")),
        )
    )
    if (active_user or 0) >= int(plan.get("concurrency") or 2):
        raise HTTPException(status_code=429, detail=f"当前方案并发上限为 {plan.get('concurrency')} 个任务")

    active = db.scalar(
        select(GenerationJob).where(
            GenerationJob.project_id == project.id,
            GenerationJob.status.in_(("queued", "running", "validating", "finalizing")),
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="该项目已有进行中的生成任务")

    key = (idempotency_key or body.idempotency_key or "").strip()
    if key:
        existing = db.scalar(
            select(GenerationJob).where(
                GenerationJob.user_id == user.id,
                GenerationJob.idempotency_key == key,
            )
        )
        if existing:
            return {"data": job_public(existing)}

    ctx = body.context or {}
    if "bpm" in ctx and ctx["bpm"]:
        project.bpm = int(ctx["bpm"])
    if ctx.get("key"):
        project.key_signature = str(ctx["key"])
    if ctx.get("duration_seconds"):
        project.duration_seconds = int(ctx["duration_seconds"])
    if ctx.get("mood"):
        project.mood = str(ctx["mood"])
    if ctx.get("structure"):
        project.structure = str(ctx["structure"])
    if ctx.get("negative_prompt"):
        project.negative_prompt = str(ctx["negative_prompt"])
    if ctx.get("genre"):
        project.genre = list(ctx["genre"])
    if ctx.get("time_signature"):
        project.time_signature = str(ctx["time_signature"])[:16]
    project.prompt = body.prompt
    is_cover = (body.job_type or "") == "vocal_cover"
    if is_cover:
        project.vocal_mode = "vocal"
    else:
        project.vocal_mode = "instrumental" if body.instrumental else "vocal"
    if body.lyrics:
        project.lyrics = body.lyrics
    lyrics_text = (body.lyrics or project.lyrics or "").strip()
    if is_cover:
        if not body.parent_asset_id:
            raise HTTPException(status_code=400, detail="人声翻唱需要选择一个已生成的成品 Take")
        if len(lyrics_text) < 10:
            raise HTTPException(status_code=400, detail="Music Cover 歌词至少 10 个字符，请先填词")
        parent = db.get(Asset, body.parent_asset_id)
        if not parent or parent.user_id != user.id or parent.project_id != project.id or parent.deleted_at:
            raise HTTPException(status_code=404, detail="源版本不存在")
        if (parent.file_size_bytes or 0) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="参考音频超过 50MB，无法提交 Music Cover")
        if int(parent.duration_seconds or 0) > 360:
            raise HTTPException(status_code=400, detail="参考音频超过 6 分钟，Music Cover 不接受")
        prompt = body.prompt
        job_type = "vocal_cover"
        instrumental_flag = False
    else:
        if not body.instrumental and not lyrics_text:
            raise HTTPException(status_code=400, detail="人声模式需要填写歌词，或先使用副驾生成歌词")
        prompt = body.prompt
        job_type = "variation" if body.parent_asset_id else (body.job_type or "initial_generate")
        if body.parent_asset_id:
            parent = db.get(Asset, body.parent_asset_id)
            if not parent or parent.user_id != user.id:
                raise HTTPException(status_code=404, detail="源版本不存在")
            prompt = (
                f"{body.prompt}\n\n[Variation of Take {parent.version}] Keep the song identity, "
                "change arrangement and vocal ornament, do not copy the previous mix exactly."
            )[:6000]
        instrumental_flag = body.instrumental

    cost = settings.music_cost_credits
    job = GenerationJob(
        project_id=project.id,
        user_id=user.id,
        job_type=job_type,
        status="queued",
        progress_stage="queued",
        prompt=prompt,
        params={
            "format": "mp3",
            "instrumental": instrumental_flag,
            "lyrics": body.lyrics or project.lyrics,
            "context": body.context,
            "vocal_cover": is_cover,
        },
        idempotency_key=key or None,
        provider="runninghub",
        model="minimax-music-cover" if is_cover else settings.music_model,
        capability_schema_version=body.capability_schema_version or CAPABILITY_SCHEMA_VERSION,
        credit_hold=cost,
        parent_asset_id=body.parent_asset_id,
    )
    db.add(job)
    db.flush()
    hold_credits(db, user, cost, project_name=project.name, job_id=job.id, key=str(job.id))
    record(db, actor_id=user.id, action="job.create", resource_type="job", resource_id=str(job.id), metadata={"type": job_type})
    db.commit()
    enqueue_job(str(job.id))
    return {"data": job_public(job)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(GenerationJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"data": job_public(job)}


@app.get("/api/jobs")
def list_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db), limit: int = Query(40, ge=1, le=100)):
    jobs = db.scalars(
        select(GenerationJob).where(GenerationJob.user_id == user.id).order_by(GenerationJob.created_at.desc()).limit(limit)
    ).all()
    payload = []
    for job in jobs:
        item = job_public(job)
        project = db.get(Project, job.project_id)
        item["project_name"] = project.name if project else ""
        item["project_id"] = str(job.project_id)
        payload.append(item)
    return {"data": payload}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(
    job_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(GenerationJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status not in ("failed", "canceled"):
        raise HTTPException(status_code=409, detail="只有失败或已取消的任务可以重试（会新建任务并重新预留积分）")
    params = job.params or {}
    body = JobCreate(
        prompt=job.prompt if len(job.prompt or "") >= 8 else f"{job.prompt} variation",
        format="mp3",
        instrumental=bool(params.get("instrumental", True)),
        lyrics=str(params.get("lyrics") or ""),
        parent_asset_id=job.parent_asset_id,
        job_type=job.job_type or "initial_generate",
        capability_schema_version=job.capability_schema_version,
        context=params.get("context") or {},
        idempotency_key=f"retry-{job.id}-{uuid.uuid4()}",
    )
    return create_job(job.project_id, body, request, user, db, None)


@app.get("/api/activity")
def activity(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = db.scalars(
        select(GenerationJob).where(GenerationJob.user_id == user.id).order_by(GenerationJob.created_at.desc()).limit(12)
    ).all()
    notices = []
    if user.credit_balance < settings.music_cost_credits:
        notices.append({"id": "low-credits", "level": "warn", "text": f"积分余额 {user.credit_balance}，不足一次生成。"})
    for job in jobs:
        project = db.get(Project, job.project_id)
        name = project.name if project else "项目"
        if job.status == "failed":
            notices.append(
                {
                    "id": f"job-{job.id}",
                    "level": "error",
                    "text": f"{name} 生成失败：{job.error_message or '未知原因'}",
                    "job_id": str(job.id),
                    "project_id": str(job.project_id),
                }
            )
        elif job.status in ("queued", "running", "validating", "finalizing"):
            notices.append(
                {
                    "id": f"job-{job.id}",
                    "level": "info",
                    "text": f"{name} 正在 {job.progress_stage or job.status}",
                    "job_id": str(job.id),
                    "project_id": str(job.project_id),
                }
            )
    return {"data": {"notices": notices[:8], "unread": len(notices)}}


@app.get("/api/privacy/export")
def export_privacy(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = db.scalars(select(Project).where(Project.owner_user_id == user.id)).all()
    jobs = db.scalars(select(GenerationJob).where(GenerationJob.user_id == user.id)).all()
    ledger = db.scalars(select(CreditLedger).where(CreditLedger.user_id == user.id)).all()
    return {
        "data": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "user": user_public(user),
            "projects": [{"id": str(item.id), "name": item.name, "purpose": item.purpose, "status": item.status} for item in projects],
            "jobs": [job_public(item) for item in jobs[:200]],
            "ledger": [
                {"type": row.entry_type, "amount": row.amount, "label": row.label, "at": row.created_at.isoformat()}
                for row in ledger[:200]
            ],
            "note": "不含音频二进制。下载作品请使用素材库。",
        }
    }


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(GenerationJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != "queued":
        raise HTTPException(status_code=409, detail="仅排队中的任务可以取消；运行中取消当前供应商不支持")
    job.cancel_requested = True
    job.status = "canceled"
    job.progress_stage = "canceled"
    job.finished_at = datetime.now(timezone.utc)
    user_row = db.get(User, user.id)
    project = db.get(Project, job.project_id)
    release_hold(
        db,
        user_row,
        job.credit_hold,
        project_name=project.name if project else "",
        job_id=job.id,
        key=str(job.id),
    )
    record(db, actor_id=user.id, action="job.cancel", resource_type="job", resource_id=str(job.id))
    db.commit()
    return {"data": job_public(job)}


@app.post("/api/projects/{project_id}/duplicate")
def duplicate_project(project_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project or project.owner_user_id != user.id or project.deleted_at:
        raise HTTPException(status_code=404, detail="项目不存在")
    copy = Project(
        owner_user_id=user.id,
        name=f"{project.name} 副本",
        purpose=project.purpose,
        prompt=project.prompt,
        genre=project.genre,
        bpm=project.bpm,
        key_signature=project.key_signature,
        duration_seconds=project.duration_seconds,
        vocal_mode=project.vocal_mode,
        mood=project.mood,
        structure=project.structure,
        negative_prompt=project.negative_prompt,
        lyrics=project.lyrics,
        time_signature=getattr(project, "time_signature", "4/4"),
        tone=project.tone,
        icon=project.icon,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return {"data": project_public(copy, [], None)}


@app.post("/api/projects/{project_id}/archive")
def archive_project(project_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project or project.owner_user_id != user.id or project.deleted_at:
        raise HTTPException(status_code=404, detail="项目不存在")
    project.status = "archived" if project.status != "archived" else "active"
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"data": project_public(project)}


@app.patch("/api/assets/{asset_id}")
def patch_asset(asset_id: UUID, body: AssetPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset or asset.user_id != user.id or asset.deleted_at:
        raise HTTPException(status_code=404, detail="素材不存在")
    if body.favorite is not None:
        asset.favorite = body.favorite
    if body.note is not None:
        asset.note = body.note[:2000]
    if body.lyrics is not None:
        asset.lyrics = body.lyrics[:3500]
    if body.selected is True:
        for other in db.scalars(select(Asset).where(Asset.project_id == asset.project_id, Asset.deleted_at.is_(None))):
            other.selected = other.id == asset.id
    db.commit()
    db.refresh(asset)
    return {"data": asset_public(asset)}


@app.post("/api/assets/{asset_id}/export")
def export_asset(asset_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset or asset.user_id != user.id or asset.deleted_at:
        raise HTTPException(status_code=404, detail="素材不存在")
    try:
        audio = download_bytes(asset.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="音频文件缺失，无法导出") from exc
    project = db.get(Project, asset.project_id)
    manifest = (
        f"Studio AI export\n"
        f"project: {project.name if project else ''}\n"
        f"take: {asset.version}\n"
        f"title: {asset.title}\n"
        f"provider: {asset.provider}\n"
        f"model_request: {asset.provider_request_id}\n"
        f"created: {asset.created_at.isoformat() if asset.created_at else ''}\n"
        f"bpm: {asset.bpm}\n"
        f"key: {asset.key_signature}\n"
        f"terms_version: {getattr(asset, 'terms_version', '1.0')}\n"
        f"prompt:\n{asset.prompt}\n\n"
        f"lyrics:\n{asset.lyrics}\n\n"
        "unsupported: MIDI, stems, guaranteed WAV. Original file is MP3 from MiniMax Music.\n"
        "This platform does not claim copyright exclusivity.\n"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (project.name if project else "project"))
        zf.writestr(f"{safe}_v{str(asset.version).zfill(2)}_mix.mp3", audio)
        zf.writestr(f"{safe}_v{str(asset.version).zfill(2)}_generation.txt", manifest)
    record(
        db,
        actor_id=user.id,
        action="asset.export",
        resource_type="asset",
        resource_id=str(asset.id),
    )
    db.commit()
    ascii_name = f"take_v{str(asset.version).zfill(2)}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{ascii_name}"'},
    )


@app.post("/api/reports")
def create_report(body: ReportCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = Report(user_id=user.id, target_type=body.target_type, target_id=body.target_id, reason=body.reason)
    db.add(row)
    db.commit()
    return {"data": {"id": str(row.id), "status": row.status}}


@app.post("/api/privacy/deletion-requests")
def deletion_request(body: DeletionCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = DeletionRequest(user_id=user.id, scope=body.scope, note=body.note[:500], status="received")
    db.add(row)
    record(db, actor_id=user.id, action="privacy.deletion_request", resource_type="user", resource_id=str(user.id))
    db.commit()
    return {"data": {"id": str(row.id), "status": row.status, "message": "已记录。本地演示不会自动清除计费流水。"}}


@app.get("/api/assets")
def list_assets(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    base = api_base(request)
    assets = db.scalars(
        select(Asset).where(Asset.user_id == user.id, Asset.deleted_at.is_(None)).order_by(Asset.created_at.desc())
    ).all()
    result = []
    for asset in assets:
        item = asset_public(asset, base)
        project = db.get(Project, asset.project_id)
        item["projectName"] = project.name if project else ""
        item["projectId"] = str(asset.project_id)
        result.append(item)
    return {"data": result}


@app.get("/api/assets/{asset_id}/content")
def asset_content(asset_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset or asset.user_id != user.id or asset.deleted_at:
        raise HTTPException(status_code=404, detail="素材不存在")
    try:
        data = download_bytes(asset.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="音频文件缺失，请重新生成") from exc
    media = "audio/mpeg" if asset.format == "mp3" else "audio/wav"
    return Response(content=data, media_type=media, headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/qwen/assist")
def qwen_assist(body: QwenAssistRequest, user: User = Depends(get_current_user)):
    _ = user
    if not settings.dashscope_api_key:
        raise HTTPException(status_code=503, detail="Qwen 未配置")
    context = body.context or {}
    text = qwen_chat(
        [
            {
                "role": "system",
                "content": "你是专业音乐制作副驾。输出两段：创作描述：... 与 制作建议：... 都用中文，简洁可执行。",
            },
            {
                "role": "user",
                "content": f"用户灵感：{body.prompt}\n参数：{context}",
            },
        ]
    )
    return {"data": {"text": text, "model": settings.dashscope_model}}


@app.post("/api/qwen/parse-controls")
def qwen_parse_controls(body: QwenAssistRequest, user: User = Depends(get_current_user)):
    _ = user
    data = parse_prompt_controls(body.prompt)
    return {"data": data}


@app.post("/api/projects/{project_id}/write-lyrics")
def write_project_lyrics(
    project_id: UUID,
    body: LyricsWriteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not settings.dashscope_api_key:
        raise HTTPException(status_code=503, detail="Qwen 未配置，无法填词")
    project = db.get(Project, project_id)
    if not project or project.owner_user_id != user.id or project.deleted_at:
        raise HTTPException(status_code=404, detail="项目不存在")
    asset = None
    if body.asset_id:
        asset = db.get(Asset, body.asset_id)
        if not asset or asset.user_id != user.id or asset.project_id != project.id or asset.deleted_at:
            raise HTTPException(status_code=404, detail="版本不存在")
    prompt = (asset.prompt if asset and asset.prompt else project.prompt or "").strip()
    if len(prompt) < 8:
        raise HTTPException(status_code=400, detail="请先写创作描述，再为这首曲子填词")
    blocked = review_generation_text(prompt, body.instruction, project.lyrics)
    if blocked:
        raise HTTPException(status_code=400, detail=blocked)
    lyrics = write_lyrics(
        prompt,
        duration_seconds=int((asset.duration_seconds if asset else 0) or project.duration_seconds or 90),
        bpm=int((asset.bpm if asset else 0) or project.bpm or 0) or None,
        key=(asset.key_signature if asset and asset.key_signature else project.key_signature) or "",
        mood=project.mood or "",
        structure=project.structure or "",
        genre=project.genre or [],
        existing_lyrics=(asset.lyrics if asset and asset.lyrics.strip() else project.lyrics) or "",
        instruction=body.instruction,
        title=project.name,
    )
    blocked_lyrics = review_generation_text(lyrics)
    if blocked_lyrics:
        raise HTTPException(status_code=400, detail=blocked_lyrics)
    if body.apply:
        project.lyrics = lyrics
        project.updated_at = datetime.now(timezone.utc)
        if asset:
            asset.lyrics = lyrics
        else:
            selected = db.scalar(
                select(Asset).where(
                    Asset.project_id == project.id,
                    Asset.deleted_at.is_(None),
                    Asset.selected.is_(True),
                )
            )
            if selected:
                selected.lyrics = lyrics
        record(
            db,
            actor_id=user.id,
            action="lyrics.write",
            resource_type="project",
            resource_id=str(project.id),
            metadata={"asset_id": str(asset.id) if asset else None, "model": settings.dashscope_model},
        )
        db.commit()
    return {
        "data": {
            "lyrics": lyrics,
            "model": settings.dashscope_model,
            "applied": bool(body.apply),
            "note": "填词只写入歌词，不会改已生成的 MP3。若要人声，请改为人声模式后重新生成。",
        }
    }


@app.get("/api/settings/providers")
def get_providers(user: User = Depends(get_current_user)):
    _ = user
    return {
        "data": {
            "qwen": {
                "provider": "dashscope",
                "model": settings.dashscope_model,
                "configured": bool(settings.dashscope_api_key),
            },
            "music": {
                "provider": settings.music_provider,
                "model": settings.music_model,
                "configured": bool(settings.runninghub_api_key),
                "cost_credits": settings.music_cost_credits,
            },
        }
    }


@app.put("/api/settings/providers")
def update_providers(body: ProviderSettingsUpdate, user: User = Depends(get_current_user)):
    if user.email.lower() != settings.bootstrap_admin_email.lower():
        raise HTTPException(status_code=403, detail="仅管理员可修改供应商凭据。普通用户请使用环境变量。")
    # Runtime env update for current process; docker restart preferred for persistence.
    if body.qwen_model:
        os.environ["DASHSCOPE_MODEL"] = body.qwen_model
        settings.dashscope_model = body.qwen_model
    if body.music_provider:
        os.environ["MUSIC_PROVIDER"] = body.music_provider
        settings.music_provider = body.music_provider
    if body.music_model:
        os.environ["MUSIC_MODEL"] = body.music_model
        settings.music_model = body.music_model
    if body.dashscope_api_key:
        os.environ["DASHSCOPE_API_KEY"] = body.dashscope_api_key
        settings.dashscope_api_key = body.dashscope_api_key
    if body.runninghub_api_key:
        os.environ["RUNNINGHUB_API_KEY"] = body.runninghub_api_key
        settings.runninghub_api_key = body.runninghub_api_key
    if body.runninghub_base_url:
        os.environ["RUNNINGHUB_BASE_URL"] = body.runninghub_base_url
        settings.runninghub_base_url = body.runninghub_base_url
    return {"data": {"ok": True}}


# Compatibility shim for old frontend direct generate endpoint
@app.post("/api/music/generate")
def legacy_generate(body: JobCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Create ephemeral project if needed is too heavy; require using project jobs.
    raise HTTPException(
        status_code=410,
        detail="请改用异步任务接口 POST /api/projects/{id}/jobs",
    )
