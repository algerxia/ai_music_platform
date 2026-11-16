"""Admin user management + RunningHub account APIs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Consent, User
from app.passwords import hash_password
from app.runninghub_account import fetch_runninghub_account
from app.schemas import user_public
from app.security import get_current_user, require_admin, user_is_admin
from app.settings_store import apply_stored_settings, get_setting, mask_secret, set_setting

router = APIRouter()


class AdminUserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=6, max_length=128)
    is_admin: bool = False
    status: str = "active"


class AdminUserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = None
    is_admin: bool | None = None
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role_label: str | None = Field(default=None, max_length=64)


def _user_admin_public(user: User) -> dict:
    data = user_public(user)
    data.update(
        {
            "is_admin": user_is_admin(user),
            "status": user.status,
            "deleted_at": user.deleted_at.isoformat() if user.deleted_at else None,
        }
    )
    return data


@router.get("/api/admin/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _ = admin
    rows = db.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc())).all()
    return {"data": [_user_admin_public(u) for u in rows]}


@router.post("/api/admin/users")
def create_user(body: AdminUserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _ = admin
    email = str(body.email).strip().lower()
    exists = db.scalar(select(User).where(User.email == email))
    if exists and not exists.deleted_at:
        raise HTTPException(400, "该邮箱已注册")
    status = (body.status or "active").strip().lower()
    if status not in {"active", "disabled"}:
        raise HTTPException(400, "status 仅支持 active / disabled")
    user = User(
        email=email,
        display_name=body.display_name.strip()[:120],
        password_hash=hash_password(body.password),
        status=status,
        is_admin=bool(body.is_admin),
        credit_balance=0,
        plan_code="creator",
    )
    db.add(user)
    db.flush()
    db.add(Consent(user_id=user.id, consent_type="terms", document_version="1.0", accepted=True))
    db.add(Consent(user_id=user.id, consent_type="privacy", document_version="1.0", accepted=True))
    db.commit()
    db.refresh(user)
    return {"data": _user_admin_public(user)}


@router.patch("/api/admin/users/{user_id}")
def patch_user(
    user_id: UUID,
    body: AdminUserPatch,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(404, "用户不存在")
    if body.display_name is not None:
        user.display_name = body.display_name.strip()[:120]
    if body.role_label is not None:
        user.role_label = body.role_label.strip()[:64]
    if body.status is not None:
        status = body.status.strip().lower()
        if status not in {"active", "disabled"}:
            raise HTTPException(400, "status 仅支持 active / disabled")
        if user.id == admin.id and status != "active":
            raise HTTPException(400, "不能停用当前登录的管理员账号")
        user.status = status
    if body.is_admin is not None:
        if user.id == admin.id and not body.is_admin:
            raise HTTPException(400, "不能取消自己的管理员权限")
        user.is_admin = bool(body.is_admin)
    if body.password:
        user.password_hash = hash_password(body.password)
    db.commit()
    db.refresh(user)
    return {"data": _user_admin_public(user)}


@router.get("/api/runninghub/account")
def runninghub_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    apply_stored_settings(db)
    try:
        data = fetch_runninghub_account()
    except RuntimeError as exc:
        return {
            "data": {
                "configured": bool(settings.runninghub_api_key),
                "error": str(exc),
                "remain_money": None,
                "remain_coins": None,
                "currency": "CNY",
            }
        }
    return {"data": data}


@router.get("/api/settings/providers")
def get_providers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    apply_stored_settings(db)
    rh_key = settings.runninghub_api_key or get_setting(db, "runninghub_api_key")
    ds_key = settings.dashscope_api_key or get_setting(db, "dashscope_api_key")
    return {
        "data": {
            "qwen": {
                "provider": "dashscope",
                "model": settings.dashscope_model,
                "configured": bool(ds_key),
                "key_masked": mask_secret(ds_key) if user_is_admin(user) else "",
            },
            "music": {
                "provider": settings.music_provider,
                "model": settings.music_model,
                "configured": bool(rh_key),
                "cost_credits": 0,
                "key_masked": mask_secret(rh_key) if user_is_admin(user) else "",
                "base_url": settings.runninghub_base_url,
            },
            "billing": {
                "mode": "runninghub_upstream",
                "platform_credits_enabled": False,
            },
            "can_edit": user_is_admin(user),
        }
    }


class ProviderSettingsUpdate(BaseModel):
    qwen_model: str | None = None
    music_provider: str | None = None
    music_model: str | None = None
    dashscope_api_key: str | None = None
    runninghub_api_key: str | None = None
    runninghub_base_url: str | None = None


@router.put("/api/settings/providers")
def update_providers(
    body: ProviderSettingsUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    if body.qwen_model:
        settings.dashscope_model = body.qwen_model
        set_setting(db, "dashscope_model", body.qwen_model)
    if body.music_provider:
        settings.music_provider = body.music_provider
        set_setting(db, "music_provider", body.music_provider)
    if body.music_model:
        settings.music_model = body.music_model
        set_setting(db, "music_model", body.music_model)
    if body.dashscope_api_key:
        settings.dashscope_api_key = body.dashscope_api_key
        set_setting(db, "dashscope_api_key", body.dashscope_api_key)
    if body.runninghub_api_key:
        settings.runninghub_api_key = body.runninghub_api_key
        set_setting(db, "runninghub_api_key", body.runninghub_api_key)
    if body.runninghub_base_url:
        settings.runninghub_base_url = body.runninghub_base_url.rstrip("/")
        set_setting(db, "runninghub_base_url", settings.runninghub_base_url)
    db.commit()
    return {"data": {"ok": True, "persisted": True}}
