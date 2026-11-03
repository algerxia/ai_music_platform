from datetime import datetime, timezone
from pathlib import Path
import tempfile
import uuid

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.credits import consume_hold, release_hold
from app.db import SessionLocal
from app.models import AgentSession, Asset, GenerationJob, MvProject, Project, User
from app.providers import (
    cover_music_runninghub,
    download_url,
    generate_music_runninghub,
    upload_runninghub_audio,
    write_lyrics,
)
from app.rh_engines import (
    audio_worker_post,
    extend_mureka,
    generate_mureka_v9,
    generate_suno_custom,
    generate_suno_single,
    separate_audio_runninghub,
)
from app.storage import data_root, upload_file


def estimate_duration_seconds(path: Path, fallback: int) -> int:
    size = path.stat().st_size
    if size <= 0:
        return fallback
    seconds = int(size * 8 / 256_000)
    return max(8, min(seconds or fallback, 600))


def _save_audio_assets(
    db,
    *,
    job: GenerationJob,
    project: Project,
    user: User,
    result: dict,
    prompt: str,
    lyrics: str,
    origin: str,
    fmt: str = "mp3",
) -> Asset:
    urls = result.get("audio_urls") or ([result["audio_url"]] if result.get("audio_url") else [])
    first_asset = None
    version = db.scalar(
        select(Asset)
        .where(Asset.project_id == project.id, Asset.deleted_at.is_(None))
        .order_by(Asset.version.desc())
        .limit(1)
    )
    next_version = (version.version + 1) if version else 1
    for old in db.scalars(select(Asset).where(Asset.project_id == project.id, Asset.deleted_at.is_(None))):
        old.selected = False

    with tempfile.TemporaryDirectory() as tmp:
        for index, url in enumerate(urls):
            target = Path(tmp) / f"{job.id}-{index}.{fmt}"
            download_url(url, target)
            storage_key = upload_file(target, content_type="audio/mpeg" if fmt == "mp3" else "audio/wav")
            duration = estimate_duration_seconds(target, project.duration_seconds or 90)
            file_size = target.stat().st_size
            stored = data_root() / storage_key
            if stored.exists():
                file_size = stored.stat().st_size
            asset = Asset(
                project_id=project.id,
                user_id=user.id,
                job_id=job.id,
                title=f"{project.name} — Take {str(next_version).zfill(2)}"
                + (f" · B" if index > 0 else ""),
                status="ready",
                format="mp3",
                duration_seconds=duration,
                sample_rate=44100,
                bpm=project.bpm,
                key_signature=project.key_signature,
                storage_key=storage_key,
                provider=result.get("model") or job.model,
                provider_request_id=result.get("task_id") or "",
                lyrics=result.get("lyrics") or lyrics,
                prompt=prompt,
                version=next_version,
                favorite=False,
                selected=index == 0,
                asset_type="audio",
                terms_version="1.0",
                file_size_bytes=file_size,
                parent_asset_id=job.parent_asset_id,
                meta={
                    "source_url": url,
                    "duration_estimated": True,
                    "origin": origin if index == 0 else "variant_sibling",
                    "job_type": job.job_type,
                    "engine": (job.params or {}).get("engine") or "minimax",
                    "instrumental": bool((job.params or {}).get("instrumental"))
                    and not bool((job.params or {}).get("vocal_cover")),
                    "lyrics_written": bool((result.get("lyrics") or lyrics or "").strip()),
                },
            )
            db.add(asset)
            db.flush()
            if first_asset is None:
                first_asset = asset
            next_version += 1
    return first_asset


def _run_music_generate(db, job, project, user, params) -> Asset:
    engine = str(params.get("engine") or "minimax").lower()
    mode = str(params.get("mode") or "parametric").lower()
    instrumental = bool(params.get("instrumental", True))
    fmt = str(params.get("format") or "mp3")
    lyrics = str(params.get("lyrics") or project.lyrics or "")
    prompt = job.prompt
    tags = str(params.get("tags") or "")
    title = str(params.get("title") or project.name or "未命名")

    if job.job_type == "vocal_cover" or params.get("vocal_cover"):
        parent = db.get(Asset, job.parent_asset_id) if job.parent_asset_id else None
        if not parent or parent.deleted_at:
            raise RuntimeError("找不到用于翻唱的成品音频")
        source_path = data_root() / parent.storage_key
        if not source_path.exists():
            raise RuntimeError("源音频文件缺失")
        if not lyrics.strip():
            lyrics = parent.lyrics or project.lyrics
        job.progress_stage = "uploading_reference"
        db.commit()
        try:
            result = cover_music_runninghub(prompt=prompt, lyrics=lyrics, audio_path=source_path, fmt=fmt)
            return _save_audio_assets(
                db, job=job, project=project, user=user, result=result, prompt=prompt, lyrics=lyrics, origin="vocal_cover"
            )
        except Exception as cover_exc:  # noqa: BLE001
            # Music Cover occasionally 模型响应异常 — fall back to sparse-lyric vocal gen
            # guided by the chosen melody's style prompt (still melody-first workflow).
            job.progress_stage = "cover_fallback_vocal"
            merged = dict(params or {})
            merged["cover_fallback"] = True
            merged["cover_error"] = str(cover_exc)[:240]
            job.params = merged
            db.commit()
            style = (parent.prompt or prompt or project.prompt or "").strip()
            fallback_prompt = (
                f"{style}\n按已选定纯音乐的旋律感觉演唱：短句、留白、跟着腔走，"
                f"禁止朗诵念词；人声像录音室歌手。"
            )[:2000]
            result = generate_music_runninghub(
                prompt=fallback_prompt,
                lyrics=lyrics,
                instrumental=False,
                fmt=fmt,
                context={**(merged.get("context") or {}), "instrumental": False, "mood": project.mood or ""},
            )
            return _save_audio_assets(
                db,
                job=job,
                project=project,
                user=user,
                result=result,
                prompt=fallback_prompt,
                lyrics=lyrics,
                origin="melody_lyrics_fallback",
            )

    if job.job_type == "extend" or engine == "mureka" and mode == "extend":
        parent = db.get(Asset, job.parent_asset_id) if job.parent_asset_id else None
        if not parent:
            raise RuntimeError("延长需要选择源 Take")
        source_path = data_root() / parent.storage_key
        if not source_path.exists():
            raise RuntimeError("源音频缺失")
        job.progress_stage = "uploading_reference"
        db.commit()
        file_url = upload_runninghub_audio(source_path)
        extend_at = int(params.get("extend_at_ms") or max(0, (parent.duration_seconds or 60) * 1000 - 5000))
        result = extend_mureka(lyrics=lyrics or "[Outro]\n继续往前走", file_url=file_url, extend_at_ms=extend_at)
        return _save_audio_assets(
            db, job=job, project=project, user=user, result=result, prompt=prompt, lyrics=lyrics, origin="extend"
        )

    def _suno_unavailable(message: str) -> bool:
        text = (message or "").lower()
        return (
            "40310" in text
            or "runninghub.ai" in text
            or "全球站" in (message or "")
            or "cn endpoint" in text
            or "cn 节点" in (message or "")
        )

    if engine == "suno":
        job.progress_stage = "provider_submit"
        db.commit()
        try:
            if mode == "custom":
                result = generate_suno_custom(title=title, lyrics=lyrics or prompt, tags=tags or "pop, melodic")
            else:
                result = generate_suno_single(description=prompt[:400], title=title, instrumental=instrumental)
            return _save_audio_assets(
                db, job=job, project=project, user=user, result=result, prompt=prompt, lyrics=lyrics, origin="suno"
            )
        except Exception as suno_exc:  # noqa: BLE001
            if not _suno_unavailable(str(suno_exc)):
                raise
            # CN Key cannot call Suno — seamless fallback to MiniMax
            job.progress_stage = "fallback_minimax"
            merged = dict(params or {})
            merged["engine"] = "minimax"
            merged["fallback_from"] = "suno"
            ctx = dict(merged.get("context") or {})
            ctx["engine"] = "minimax"
            ctx["fallback_from"] = "suno"
            merged["context"] = ctx
            job.params = merged
            job.model = settings.music_model
            params = merged
            engine = "minimax"
            db.commit()

    if engine == "mureka":
        job.progress_stage = "provider_submit"
        db.commit()
        if not lyrics.strip():
            lyrics = write_lyrics(prompt, title=title, existing_lyrics="")
        result = generate_mureka_v9(
            lyrics=lyrics,
            prompt=prompt[:1024],
            n=int(params.get("n") or 2),
            reference_id=str(params.get("reference_id") or ""),
            vocal_id=str(params.get("vocal_id") or ""),
            melody_id=str(params.get("melody_id") or ""),
        )
        return _save_audio_assets(
            db, job=job, project=project, user=user, result=result, prompt=prompt, lyrics=lyrics, origin="mureka"
        )

    # default MiniMax parametric
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
    origin = "variation" if job.parent_asset_id else ("vocal" if not instrumental else "instrumental")
    if (params or {}).get("fallback_from") == "suno":
        origin = "suno_fallback_minimax"
    return _save_audio_assets(
        db, job=job, project=project, user=user, result=result, prompt=prompt, lyrics=lyrics, origin=origin
    )


def _run_tool_job(db, job, project, user, params) -> Asset:
    tool = str(params.get("tool") or job.job_type)
    source_key = str(params.get("storage_key") or "")
    source_path = data_root() / source_key if source_key else None
    job.progress_stage = "tool_running"
    db.commit()

    if tool in {"stem_separate", "separation"}:
        mode = str(params.get("sep_mode") or "vocal")
        backend = str(params.get("backend") or "runninghub")
        if backend == "demucs" or mode == "demucs":
            if not source_path or not source_path.exists():
                raise RuntimeError("缺少源音频")
            result = audio_worker_post(
                "/separate",
                {"input_path": str(source_path), "output_dir": str(data_root() / "stems" / str(job.id))},
                timeout=900,
            )
            paths = [Path(p) for p in (result.get("paths") or [])]
            first = None
            for index, path in enumerate(paths):
                storage_key = upload_file(path, content_type="audio/wav")
                asset = Asset(
                    project_id=project.id,
                    user_id=user.id,
                    job_id=job.id,
                    title=f"{project.name} — 分轨 {path.stem}",
                    status="ready",
                    format=path.suffix.lstrip(".") or "wav",
                    storage_key=storage_key,
                    provider="demucs",
                    asset_type="stem",
                    file_size_bytes=path.stat().st_size,
                    meta={"stem": path.stem, "expires_days": 7, "job_type": tool},
                    selected=index == 0,
                    version=index + 1,
                )
                db.add(asset)
                db.flush()
                if first is None:
                    first = asset
            if not first:
                raise RuntimeError("分轨未返回文件")
            return first
        if not source_path or not source_path.exists():
            raise RuntimeError("缺少源音频")
        try:
            sep = separate_audio_runninghub(
                source_path, mode=mode if mode in {"vocal", "background", "full"} else "vocal"
            )
            return _save_audio_assets(
                db,
                job=job,
                project=project,
                user=user,
                result=sep,
                prompt=job.prompt,
                lyrics="",
                origin="stem",
            )
        except Exception as rh_error:  # noqa: BLE001
            # Cloud workflow flaky / video-only — fall back to local stem approx.
            job.progress_stage = "tool_fallback_local"
            db.commit()
            result = audio_worker_post(
                "/separate",
                {"input_path": str(source_path), "output_dir": str(data_root() / "stems" / str(job.id))},
                timeout=900,
            )
            paths = [Path(p) for p in (result.get("paths") or [])]
            if not paths:
                raise RuntimeError(f"云端分轨失败且本地无输出：{rh_error}") from rh_error
            first = None
            for index, path in enumerate(paths):
                storage_key = upload_file(path, content_type="audio/wav")
                asset = Asset(
                    project_id=project.id,
                    user_id=user.id,
                    job_id=job.id,
                    title=f"{project.name} — 分轨 {path.stem}",
                    status="ready",
                    format=path.suffix.lstrip(".") or "wav",
                    storage_key=storage_key,
                    provider="demucs-fallback",
                    asset_type="stem",
                    file_size_bytes=path.stat().st_size,
                    meta={
                        "stem": path.stem,
                        "expires_days": 7,
                        "job_type": tool,
                        "rh_error": str(rh_error)[:240],
                    },
                    selected=index == 0,
                    version=index + 1,
                )
                db.add(asset)
                db.flush()
                if first is None:
                    first = asset
            return first

    if tool in {"midi_convert", "music_to_midi"}:
        if not source_path or not source_path.exists():
            raise RuntimeError("缺少源音频")
        result = audio_worker_post(
            "/to-midi",
            {"input_path": str(source_path), "output_path": str(data_root() / "midi" / f"{job.id}.mid")},
            timeout=600,
        )
        out = Path(result["output_path"])
        storage_key = upload_file(out, content_type="audio/midi")
        asset = Asset(
            project_id=project.id,
            user_id=user.id,
            job_id=job.id,
            title=f"{project.name} — MIDI",
            status="ready",
            format="mid",
            storage_key=storage_key,
            provider="basic-pitch",
            asset_type="midi",
            file_size_bytes=out.stat().st_size,
            selected=True,
            version=1,
            meta={"job_type": tool},
        )
        db.add(asset)
        db.flush()
        return asset

    if tool in {"score_convert", "midi_to_score"}:
        midi_key = str(params.get("midi_key") or source_key)
        midi_path = data_root() / midi_key
        if not midi_path.exists():
            raise RuntimeError("缺少 MIDI 文件")
        result = audio_worker_post(
            "/to-score",
            {"input_path": str(midi_path), "output_path": str(data_root() / "score" / f"{job.id}.musicxml")},
            timeout=300,
        )
        out = Path(result["output_path"])
        storage_key = upload_file(out, content_type="application/vnd.recordare.musicxml+xml")
        asset = Asset(
            project_id=project.id,
            user_id=user.id,
            job_id=job.id,
            title=f"{project.name} — 乐谱",
            status="ready",
            format="musicxml",
            storage_key=storage_key,
            provider="music21",
            asset_type="score",
            file_size_bytes=out.stat().st_size,
            selected=True,
            version=1,
            meta={"job_type": tool, "preview": result.get("preview_text", "")},
        )
        db.add(asset)
        db.flush()
        return asset

    if tool in {"denoise", "improve", "crop", "merge", "convert", "audio_edit", "mv_render"}:
        payload = {
            "op": tool if tool != "audio_edit" else str(params.get("op") or "crop"),
            "input_path": str(source_path) if source_path else "",
            "input_paths": [str(data_root() / k) for k in (params.get("storage_keys") or [])],
            "output_path": str(data_root() / "edit" / f"{job.id}.mp3"),
            "options": params.get("options") or {},
        }
        if tool == "mv_render":
            payload["op"] = "mv_render"
            payload["output_path"] = str(data_root() / "mv" / f"{job.id}.mp4")
            payload["options"] = {
                **(params.get("options") or {}),
                "lyrics": params.get("lyrics") or "",
                "title": params.get("title") or project.name,
            }
        result = audio_worker_post("/edit", payload, timeout=900)
        out = Path(result["output_path"])
        ctype = "video/mp4" if out.suffix == ".mp4" else "audio/mpeg"
        storage_key = upload_file(out, content_type=ctype)
        asset = Asset(
            project_id=project.id,
            user_id=user.id,
            job_id=job.id,
            title=f"{project.name} — {tool}",
            status="ready",
            format=out.suffix.lstrip(".") or "mp3",
            storage_key=storage_key,
            provider="audio-worker",
            asset_type="mv" if tool == "mv_render" else "audio",
            file_size_bytes=out.stat().st_size,
            selected=True,
            version=1,
            meta={"job_type": tool},
        )
        db.add(asset)
        db.flush()
        return asset

    if tool in {"analyze", "music_analysis"}:
        from app.providers import qwen_chat

        text = qwen_chat(
            [
                {
                    "role": "system",
                    "content": "你是音乐分析师。根据用户描述与可选歌词，输出风格、结构、情绪、配器建议与可复用提示词。用中文。",
                },
                {"role": "user", "content": f"{job.prompt}\n\n歌词：\n{params.get('lyrics') or ''}"},
            ]
        )
        asset = Asset(
            project_id=project.id,
            user_id=user.id,
            job_id=job.id,
            title=f"{project.name} — 解析",
            status="ready",
            format="txt",
            storage_key="",
            provider="qwen",
            asset_type="analysis",
            selected=True,
            version=1,
            note=text[:8000],
            meta={"job_type": tool, "analysis": text[:8000]},
        )
        db.add(asset)
        db.flush()
        return asset

    if tool in {"lyrics", "ai_lyrics"}:
        lyrics = write_lyrics(job.prompt, instruction=str(params.get("instruction") or ""), title=project.name)
        asset = Asset(
            project_id=project.id,
            user_id=user.id,
            job_id=job.id,
            title=f"{project.name} — AI 歌词",
            status="ready",
            format="txt",
            lyrics=lyrics,
            provider="qwen",
            asset_type="lyrics",
            selected=True,
            version=1,
            meta={"job_type": tool},
        )
        db.add(asset)
        db.flush()
        return asset

    raise RuntimeError(f"未知工具任务: {tool}")


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
        tool_jobs = {
            "stem_separate",
            "separation",
            "midi_convert",
            "music_to_midi",
            "score_convert",
            "midi_to_score",
            "denoise",
            "improve",
            "crop",
            "merge",
            "convert",
            "audio_edit",
            "mv_render",
            "analyze",
            "music_analysis",
            "lyrics",
            "ai_lyrics",
        }

        try:
            if job.job_type in tool_jobs or params.get("tool"):
                asset = _run_tool_job(db, job, project, user, params)
            else:
                if settings.music_provider != "runninghub":
                    raise RuntimeError("当前平台音乐生成仅支持 RunningHub")
                asset = _run_music_generate(db, job, project, user, params)

            job.status = "succeeded"
            job.progress_stage = "succeeded"
            job.result_asset_id = asset.id
            job.provider = job.provider or "runninghub"
            job.model = asset.provider or job.model
            job.provider_request_id = asset.provider_request_id or ""
            job.credits_charged = job.credit_hold
            job.finished_at = datetime.now(timezone.utc)
            project.updated_at = datetime.now(timezone.utc)
            if job.job_type == "mv_render" or (job.params or {}).get("tool") == "mv_render":
                mv = db.scalar(select(MvProject).where(MvProject.job_id == job.id))
                if mv:
                    mv.status = "ready"
                    mv.result_storage_key = asset.storage_key
            consume_hold(
                db,
                user,
                job.credit_hold,
                project_name=project.name,
                job_id=job.id,
                key=str(job.id),
            )
            _notify_agent_job(db, job, project, asset=asset, failed=False)
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
            _notify_agent_job(db, job, project, asset=None, failed=True, error=str(exc)[:240])
            db.commit()
            raise


def _notify_agent_job(
    db,
    job: GenerationJob,
    project: Project,
    *,
    asset: Asset | None,
    failed: bool,
    error: str = "",
) -> None:
    """Attach playable results (or failure note) back onto the conversational session."""
    from_agent = bool((job.params or {}).get("context", {}).get("from_agent")) or job.job_type == "agent_generate"
    if not from_agent:
        return
    session = db.scalar(
        select(AgentSession).where(
            AgentSession.confirmed_job_id == job.id,
            AgentSession.user_id == job.user_id,
        )
    )
    if not session:
        session = db.scalar(
            select(AgentSession)
            .where(AgentSession.project_id == project.id, AgentSession.user_id == job.user_id)
            .order_by(AgentSession.updated_at.desc())
            .limit(1)
        )
    if not session:
        return
    messages = list(session.messages or [])
    if failed:
        session.state = "failed"
        messages.append(
            {
                "role": "assistant",
                "content": f"生成失败：{error or '请稍后重试或换一个引擎。'}",
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        session.messages = messages
        flag_modified(session, "messages")
        session.updated_at = datetime.now(timezone.utc)
        return

    takes = db.scalars(
        select(Asset)
        .where(
            Asset.job_id == job.id,
            Asset.deleted_at.is_(None),
            Asset.asset_type.in_(("audio", "stem", "mv")),
        )
        .order_by(Asset.version.asc())
    ).all()
    if not takes and asset:
        takes = [asset]
    assets_payload = [
        {
            "id": str(a.id),
            "title": a.title,
            "duration": a.duration_seconds,
            "format": a.format,
            "assetType": a.asset_type or "audio",
            "fileSize": a.file_size_bytes or 0,
            "audioUrl": f"/api/assets/{a.id}/content",
            "url": f"/api/assets/{a.id}/content",
            "preview": "video" if (a.asset_type == "mv" or (a.format or "").lower() in {"mp4", "webm", "mov"}) else "audio",
            "instrumental": bool((a.meta or {}).get("instrumental"))
            or (a.meta or {}).get("origin") == "instrumental",
            "origin": (a.meta or {}).get("origin") or "",
        }
        for a in takes
    ]
    title = project.name or (session.draft or {}).get("title") or "作品"
    is_instrumental = bool((job.params or {}).get("instrumental")) and not bool(
        (job.params or {}).get("vocal_cover")
    )
    is_cover = bool((job.params or {}).get("vocal_cover") or job.job_type == "vocal_cover")
    cover_fallback = bool((job.params or {}).get("cover_fallback"))
    # Tag media cards so UI can offer “按旋律填词”
    for row in assets_payload:
        row["instrumental"] = is_instrumental
        row["origin"] = (
            "vocal_cover"
            if is_cover and not cover_fallback
            else ("melody_lyrics_fallback" if cover_fallback else ("instrumental" if is_instrumental else "vocal"))
        )
    if is_instrumental:
        lines = [
            f"《{title}》纯音乐旋律已出，可直接在下方试听。",
            "先挑一首好听的 Take，再点「按这首旋律填词」——会按旋律写词并贴上人声，避免念词感。",
        ]
    elif is_cover or cover_fallback:
        lines = [f"《{title}》已按选定旋律填词并贴上人声，可直接试听。"]
        if cover_fallback:
            lines.append("（参考翻唱通道繁忙，已用同风格短句人声生成保底。）")
    else:
        lines = [f"《{title}》已生成完成，可直接在下方试听。"]
    if len(assets_payload) > 1:
        lines.append(f"共 {len(assets_payload)} 个版本（Take）。")
    session.state = "ready"
    draft = dict(session.draft or {})
    draft["result_assets"] = assets_payload
    draft["result_asset_id"] = assets_payload[0]["id"] if assets_payload else None
    if is_instrumental:
        draft["workflow_stage"] = "melody_ready"
        draft["instrumental"] = True
    elif is_cover:
        draft["workflow_stage"] = "lyrics_filled"
        draft["instrumental"] = False
    session.draft = draft
    messages.append(
        {
            "role": "assistant",
            "content": "\n".join(lines),
            "at": datetime.now(timezone.utc).isoformat(),
            "assets": assets_payload,
            "asset_id": assets_payload[0]["id"] if assets_payload else None,
            "project_id": str(project.id),
            "melody_ready": is_instrumental,
        }
    )
    session.messages = messages
    flag_modified(session, "messages")
    flag_modified(session, "draft")
    session.updated_at = datetime.now(timezone.utc)
