"""Local audio processing worker: edit / midi / score / separate / mv."""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Studio AI Audio Worker", version="1.0.0")


class SeparateBody(BaseModel):
    input_path: str
    output_dir: str


class MidiBody(BaseModel):
    input_path: str
    output_path: str


class ScoreBody(BaseModel):
    input_path: str
    output_path: str


class EditBody(BaseModel):
    op: str = "crop"
    input_path: str = ""
    input_paths: list[str] = Field(default_factory=list)
    output_path: str
    options: dict = Field(default_factory=dict)


def run(cmd: list[str], timeout: int = 600) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg failed")[:400])


@app.get("/health")
def health():
    return {"ok": True, "service": "audio-worker"}


@app.post("/separate")
def separate(body: SeparateBody):
    src = Path(body.input_path)
    if not src.exists():
        raise HTTPException(400, "input missing")
    out = Path(body.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    vocals = out / "vocals.wav"
    other = out / "no_vocals.wav"
    drums = out / "drums.wav"
    bass = out / "bass.wav"
    # Center-channel approximation + band splits (CPU-friendly stand-in for Demucs).
    # RH extract-vocal/background remains the high-quality path in the API.
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-filter_complex",
            "[0:a]pan=mono|c0=0.5*c0+0.5*c1[v];[0:a]pan=stereo|c0=c0|c1=c1,extrastereo=m=2.5[i]",
            "-map",
            "[v]",
            str(vocals),
            "-map",
            "[i]",
            str(other),
        ]
    )
    run(["ffmpeg", "-y", "-i", str(src), "-af", "lowpass=f=250", str(bass)])
    run(["ffmpeg", "-y", "-i", str(src), "-af", "bandpass=f=1200:width_type=h:w=1800", str(drums)])
    paths = [p for p in [vocals, other, bass, drums] if p.exists()]
    return {"ok": True, "paths": [str(p) for p in paths], "engine": "ffmpeg-approx"}


@app.post("/to-midi")
def to_midi(body: MidiBody):
    src = Path(body.input_path)
    if not src.exists():
        raise HTTPException(400, "input missing")
    out = Path(body.output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from basic_pitch.inference import predict_and_save
        from basic_pitch import ICASSP_2022_MODEL_PATH

        predict_and_save(
            [str(src)],
            str(out.parent),
            save_midi=True,
            sonify_midi=False,
            save_model_outputs=False,
            save_notes=False,
            model_or_model_path=ICASSP_2022_MODEL_PATH,
        )
        # basic_pitch names output as <stem>_basic_pitch.mid
        candidates = list(out.parent.glob(f"{src.stem}*.mid"))
        if not candidates:
            raise RuntimeError("basic-pitch 未产出 MIDI")
        candidates[0].replace(out)
    except Exception as exc:  # noqa: BLE001
        # Fallback: write a minimal empty MIDI-ish marker file with note
        out.write_bytes(b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x00\x60MTrk\x00\x00\x00\x04\x00\xff\x2f\x00")
        return {"ok": True, "output_path": str(out), "warning": str(exc)[:200]}
    return {"ok": True, "output_path": str(out)}


@app.post("/to-score")
def to_score(body: ScoreBody):
    src = Path(body.input_path)
    if not src.exists():
        raise HTTPException(400, "midi missing")
    out = Path(body.output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    preview = ""
    try:
        from music21 import converter

        score = converter.parse(str(src))
        score.write("musicxml", fp=str(out))
        preview = str(score.analyze("key"))
    except Exception as exc:  # noqa: BLE001
        out.write_text(
            f'<?xml version="1.0"?><score-partwise><credit><credit-words>{exc}</credit-words></credit></score-partwise>',
            encoding="utf-8",
        )
        preview = str(exc)[:200]
    return {"ok": True, "output_path": str(out), "preview_text": preview}


@app.post("/edit")
def edit(body: EditBody):
    out = Path(body.output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    opts = body.options or {}
    op = body.op

    if op == "mv_render":
        src = Path(body.input_path)
        if not src.exists():
            raise HTTPException(400, "audio missing")
        title = str(opts.get("title") or "Studio AI MV").replace(":", " ")
        lyrics = str(opts.get("lyrics") or title)
        # Drawtext lyric MV: solid background + scrolling-ish static lines + audio
        lines = [ln.strip() for ln in lyrics.splitlines() if ln.strip()][:12] or [title]
        label = "\\n".join(lines)[:400]
        vf = (
            f"color=c=0x101820:s=1280x720:d=1[bg];"
            f"[bg][0:a]concat=n=1:v=1:a=1,format=yuv420p,"
            f"drawtext=text='{title[:40]}':x=(w-text_w)/2:y=80:fontsize=36:fontcolor=white,"
            f"drawtext=text='{label[:200]}':x=(w-text_w)/2:y=200:fontsize=22:fontcolor=0xccdddd"
        )
        # Simpler reliable approach: generate silent video then mux
        duration = float(opts.get("duration") or 0)
        if duration <= 0:
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(src),
                ],
                capture_output=True,
                text=True,
            )
            try:
                duration = float(probe.stdout.strip() or "30")
            except ValueError:
                duration = 30
        run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x142033:s=1280x720:d={duration}",
                "-i",
                str(src),
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-vf",
                f"drawtext=text='{title[:48]}':x=(w-text_w)/2:y=h*0.35:fontsize=42:fontcolor=white:borderw=2",
                str(out),
            ],
            timeout=900,
        )
        return {"ok": True, "output_path": str(out)}

    src = Path(body.input_path) if body.input_path else None
    if op == "merge":
        paths = [Path(p) for p in body.input_paths if Path(p).exists()]
        if len(paths) < 2:
            raise HTTPException(400, "merge needs >=2 files")
        list_file = out.parent / f"{out.stem}-list.txt"
        list_file.write_text("\n".join(f"file '{p}'" for p in paths), encoding="utf-8")
        try:
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)])
        except RuntimeError:
            # Different codecs / sample rates: re-encode to mp3.
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_file),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    str(out.with_suffix(".mp3")),
                ]
            )
            out = out.with_suffix(".mp3")
        return {"ok": True, "output_path": str(out)}

    if not src or not src.exists():
        raise HTTPException(400, "input missing")

    if op == "to_video":
        # Wrap audio into a minimal mp4 so RH extract-vocal (videoUrl) accepts it.
        duration = float(opts.get("duration") or 0)
        if duration <= 0:
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(src),
                ],
                capture_output=True,
                text=True,
            )
            try:
                duration = float(probe.stdout.strip() or "30")
            except ValueError:
                duration = 30
        target = out if out.suffix.lower() == ".mp4" else out.with_suffix(".mp4")
        run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x101820:s=640x360:d={duration}",
                "-i",
                str(src),
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(target),
            ],
            timeout=600,
        )
        return {"ok": True, "output_path": str(target)}

    if op in {"crop"}:
        start = float(opts.get("start") or 0)
        end = float(opts.get("end") or 30)
        if end <= start:
            end = start + 5
        try:
            run(["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", str(src), "-c", "copy", str(out)])
        except RuntimeError:
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(start),
                    "-to",
                    str(end),
                    "-i",
                    str(src),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    str(out),
                ]
            )
    elif op in {"denoise"}:
        run(["ffmpeg", "-y", "-i", str(src), "-af", "afftdn=nf=-25", str(out)])
    elif op in {"improve"}:
        rate = str(opts.get("sample_rate") or "48000")
        run(["ffmpeg", "-y", "-i", str(src), "-ar", rate, "-b:a", "320k", str(out)])
    elif op in {"convert"}:
        fmt = str(opts.get("format") or "mp3")
        target = out if out.suffix.lstrip(".") == fmt else out.with_suffix(f".{fmt}")
        run(["ffmpeg", "-y", "-i", str(src), str(target)])
        out = target
    else:
        run(["ffmpeg", "-y", "-i", str(src), str(out)])
    return {"ok": True, "output_path": str(out)}
