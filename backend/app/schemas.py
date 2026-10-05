from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    accept_terms: bool = True
    accept_privacy: bool = True
    marketing_opt_in: bool = False
    daw: str = ""
    role_label: str = ""


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    token: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    daw: str | None = None
    role_label: str | None = None
    timezone: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=128)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: str = "灵感草稿"
    prompt: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    purpose: str | None = None
    prompt: str | None = None
    genre: list[str] | None = None
    bpm: int | None = None
    key_signature: str | None = None
    duration_seconds: int | None = None
    vocal_mode: str | None = None
    mood: str | None = None
    structure: str | None = None
    negative_prompt: str | None = None
    lyrics: str | None = None
    status: str | None = None
    time_signature: str | None = None
    notes: str | None = None
    tags: list[str] | None = None


class JobCreate(BaseModel):
    prompt: str = Field(min_length=8, max_length=6000)
    format: str = "mp3"
    instrumental: bool = True
    lyrics: str = ""
    parent_asset_id: UUID | None = None
    idempotency_key: str | None = None
    job_type: str = "initial_generate"
    capability_schema_version: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class AssetPatch(BaseModel):
    favorite: bool | None = None
    note: str | None = None
    selected: bool | None = None
    lyrics: str | None = Field(default=None, max_length=3500)


class LyricsWriteRequest(BaseModel):
    instruction: str = Field(default="", max_length=2000)
    asset_id: UUID | None = None
    apply: bool = True


class ReportCreate(BaseModel):
    target_type: str = Field(min_length=1, max_length=32)
    target_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=4, max_length=2000)


class DeletionCreate(BaseModel):
    scope: str = "account"
    note: str = ""


class QwenAssistRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=6000)
    context: dict[str, Any] = Field(default_factory=dict)


class ProviderSettingsUpdate(BaseModel):
    qwen_model: str | None = None
    music_provider: str | None = None
    music_model: str | None = None
    dashscope_api_key: str | None = None
    runninghub_api_key: str | None = None
    runninghub_base_url: str | None = None


from app.plans import get_plan


def user_public(user) -> dict:
    plan = get_plan(user.plan_code)
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "plan_code": user.plan_code,
        "plan_name": plan["name"],
        "credit_balance": user.credit_balance,
        "daw": getattr(user, "daw", "") or "",
        "role_label": getattr(user, "role_label", "") or "",
        "timezone": getattr(user, "timezone", "Asia/Shanghai"),
        "monthly_quota": plan.get("credits") or 200,
        "plan_note": plan.get("note") or "",
        "created_at": user.created_at.isoformat() if isinstance(user.created_at, datetime) else user.created_at,
    }


def project_public(project, assets=None, pending_job=None) -> dict:
    return {
        "id": str(project.id),
        "name": project.name,
        "purpose": project.purpose,
        "status": project.status,
        "prompt": project.prompt,
        "genre": project.genre or [],
        "bpm": project.bpm,
        "key": project.key_signature,
        "duration": project.duration_seconds,
        "vocal": project.vocal_mode,
        "mood": project.mood,
        "structure": project.structure,
        "negativePrompt": project.negative_prompt,
        "lyrics": project.lyrics,
        "timeSignature": getattr(project, "time_signature", "4/4") or "4/4",
        "notes": getattr(project, "notes", "") or "",
        "tone": project.tone,
        "icon": project.icon,
        "createdAt": project.created_at.isoformat(),
        "updatedAt": project.updated_at.isoformat(),
        "assets": assets or [],
        "pendingJob": pending_job,
    }


def asset_public(asset, api_base: str = "") -> dict:
    # Always relative so the browser stays on the nginx origin (e.g. :8080),
    # not request.base_url which often drops the published port.
    _ = api_base
    audio_url = f"/api/assets/{asset.id}/content"
    return {
        "id": str(asset.id),
        "title": asset.title,
        "duration": asset.duration_seconds,
        "bpm": asset.bpm,
        "key": asset.key_signature,
        "format": asset.format,
        "sampleRate": asset.sample_rate,
        "channels": asset.channels,
        "audioUrl": audio_url,
        "provider": asset.provider,
        "providerRequestId": asset.provider_request_id,
        "lyrics": asset.lyrics,
        "date": asset.created_at.strftime("%m.%d · %H:%M"),
        "favorite": asset.favorite,
        "prompt": asset.prompt,
        "version": asset.version,
        "selected": asset.selected,
        "status": asset.status,
        "note": getattr(asset, "note", "") or "",
        "assetType": getattr(asset, "asset_type", "audio") or "audio",
        "fileSize": getattr(asset, "file_size_bytes", 0) or 0,
        "parentAssetId": str(asset.parent_asset_id) if getattr(asset, "parent_asset_id", None) else None,
        "termsVersion": getattr(asset, "terms_version", "1.0") or "1.0",
        "createdAt": asset.created_at.isoformat() if asset.created_at else None,
    }


def job_public(job) -> dict:
    return {
        "id": str(job.id),
        "project_id": str(job.project_id),
        "status": job.status,
        "job_type": job.job_type,
        "prompt": job.prompt,
        "error_message": job.error_message,
        "credit_hold": job.credit_hold,
        "provider": job.provider,
        "model": job.model,
        "result_asset_id": str(job.result_asset_id) if job.result_asset_id else None,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "progress_stage": getattr(job, "progress_stage", job.status) or job.status,
        "error_code": getattr(job, "error_code", "") or "",
        "capability_schema_version": getattr(job, "capability_schema_version", "") or "",
        "parent_asset_id": str(job.parent_asset_id) if job.parent_asset_id else None,
        "can_cancel": job.status == "queued",
        "credits_charged": getattr(job, "credits_charged", 0) or 0,
    }
