"""Persist runtime provider settings (RunningHub / DashScope) beyond process memory."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting


SETTING_KEYS = (
    "runninghub_api_key",
    "runninghub_base_url",
    "runninghub_ai_base_url",
    "dashscope_api_key",
    "dashscope_model",
    "music_provider",
    "music_model",
)


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if not row or row.value is None:
        return default
    return str(row.value)


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def apply_stored_settings(db: Session) -> None:
    """Load DB overrides into the in-memory settings singleton."""
    for key in SETTING_KEYS:
        row = db.scalar(select(AppSetting).where(AppSetting.key == key))
        if not row or not (row.value or "").strip():
            continue
        value = str(row.value).strip()
        if key == "runninghub_api_key":
            settings.runninghub_api_key = value
        elif key == "runninghub_base_url":
            settings.runninghub_base_url = value
        elif key == "runninghub_ai_base_url":
            settings.runninghub_ai_base_url = value
        elif key == "dashscope_api_key":
            settings.dashscope_api_key = value
        elif key == "dashscope_model":
            settings.dashscope_model = value
        elif key == "music_provider":
            settings.music_provider = value
        elif key == "music_model":
            settings.music_model = value


def mask_secret(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}…{text[-4:]}"
