from datetime import datetime, timezone
from pathlib import Path
import tempfile
import uuid

from sqlalchemy import select

from app.config import settings
from app.credits import consume_hold, release_hold
from app.db import SessionLocal
from app.models import Asset, GenerationJob, Project, User
from app.providers import cover_music_runninghub, download_url, generate_music_runninghub, write_lyrics
from app.storage import data_root, upload_file


def estimate_duration_seconds(path: Path, fallback: int) -> int:
    size = path.stat().st_size
    if size <= 0:
        return fallback
    # Requested bitrate is 256 kbps; this is an estimate, not a decoded duration.
    seconds = int(size * 8 / 256_000)
    return max(8, min(seconds or fallback, 600))


def process_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(GenerationJob, uuid.UUID(job_id))
        if not job or job.status not in ("queued", "running"):
            return
        if job.cancel_requested or job.status == "canceled":
            job.status = "canceled"
            job.progress_stage = "canceled"
            job.finished_at = datetime.now(timezone.utc)
            user = db.get(User, job.user_id)
            project = db.get(Project, job.project_id)
            if user:
                release_hold(
                    db,
                    user,
                    job.credit_hold,
                    project_name=project.name if project else "",
                    job_id=job.id,
                    key=str(job.id),
                )
            db.commit()
            return
        user = db.get(User, job.user_id)
        project = db.get(Project, job.project_id)
        if not user or not project:
            return

        job.status = "running"
        job.progress_stage = "running"
        job.started_at = datetime.now(timezone.utc)
        job.attempt = max(1, job.attempt)
        db.commit()

        params = job.params or {}
        instrumental = bool(params.get("instrumental", True))
        fmt = str(params.get("format") or "mp3")
        lyrics = str(params.get("lyrics") or project.lyrics or "")
        prompt = job.prompt
        is_cover = job.job_type == "vocal_cover" or bool(params.get("vocal_cover"))

        try:
            if settings.music_provider != "runninghub":
                raise RuntimeError("当前平台音乐生成仅支持 RunningHub")
            if is_cover:
                parent = db.get(Asset, job.parent_asset_id) if job.parent_asset_id else None
                if not parent or parent.deleted_at:
                    raise RuntimeError("找不到用于翻唱的成品音频")
                source_path = data_root() / parent.storage_key
                if not source_path.exists():
                    raise RuntimeError("源音频文件缺失，无法上传到 RunningHub")
                if not lyrics.strip():
                    lyrics = parent.lyrics or project.lyrics
                job.progress_stage = "uploading_reference"
                db.commit()
                result = cover_music_runninghub(
                    prompt=prompt,
                    lyrics=lyrics,
                    audio_path=source_path,
                    fmt=fmt,
                )
            else:
                if not instrumental and not lyrics.strip():
                    job.progress_stage = "writing_lyrics"
                    db.commit()
                    lyrics = write_lyrics(
                        prompt,
                        duration_seconds=project.duration_seconds or 90,
                        bpm=project.bpm,
                        key=project.key_signature or "",
                        mood=project.mood or "",
                        structure=project.structure or "",
                        genre=project.genre or [],
                        existing_lyrics="",
                        title=project.name,
                    )
                job.progress_stage = "provider_submit"
                db.commit()
                result = generate_music_runninghub(
                    prompt=prompt,
                    lyrics=lyrics,
                    instrumental=instrumental,
                    fmt=fmt,
                    context=params.get("context") or {},
                )
            job.progress_stage = "finalizing"
            db.commit()
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / f"{job.id}.{fmt}"
                download_url(result["audio_url"], target)
                storage_key = upload_file(target, content_type="audio/mpeg" if fmt == "mp3" else "audio/wav")
                duration = estimate_duration_seconds(target, project.duration_seconds)
                file_size = target.stat().st_size

            stored = data_root() / storage_key
            if stored.exists():
                file_size = stored.stat().st_size

            version = db.scalar(
                select(Asset)
                .where(Asset.project_id == project.id, Asset.deleted_at.is_(None))
                .order_by(Asset.version.desc())
                .limit(1)
            )
            next_version = (version.version + 1) if version else 1
            for old in db.scalars(select(Asset).where(Asset.project_id == project.id, Asset.deleted_at.is_(None))):
                old.selected = False

            parent_id = job.parent_asset_id
            asset = Asset(
                project_id=project.id,
                user_id=user.id,
                job_id=job.id,
                title=f"{project.name} — Take {str(next_version).zfill(2)}",
                status="ready",
                format="mp3",
                duration_seconds=duration,
                sample_rate=44100,
                bpm=project.bpm,
                key_signature=project.key_signature,
                storage_key=storage_key,
                provider=result["model"],
                provider_request_id=result["task_id"],
                lyrics=result.get("lyrics") or lyrics,
                prompt=prompt,
                version=next_version,
                favorite=False,
                selected=True,
                asset_type="audio",
                terms_version="1.0",
                file_size_bytes=file_size,
                parent_asset_id=parent_id,
                meta={"source_url": result["audio_url"], "duration_estimated": True, "cover_source": result.get("source_audio_url")},
            )
            db.add(asset)
            db.flush()

            job.status = "succeeded"
            job.progress_stage = "succeeded"
            job.result_asset_id = asset.id
            job.provider = "runninghub"
            job.model = result["model"]
            job.provider_request_id = result["task_id"]
            job.credits_charged = job.credit_hold
            job.finished_at = datetime.now(timezone.utc)
            project.updated_at = datetime.now(timezone.utc)
            consume_hold(
                db,
                user,
                job.credit_hold,
                project_name=project.name,
                job_id=job.id,
                key=str(job.id),
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.progress_stage = "failed"
            job.error_code = "PROVIDER_ERROR"
            job.error_message = str(exc)[:500]
            job.finished_at = datetime.now(timezone.utc)
            release_hold(
                db,
                user,
                job.credit_hold,
                project_name=project.name,
                job_id=job.id,
                key=str(job.id),
            )
            db.commit()
            raise
