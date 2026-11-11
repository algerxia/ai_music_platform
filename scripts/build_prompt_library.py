#!/usr/bin/env python3
"""Rebuild backend/app/data/prompt_library.json from docs/AI_MUSIC_PROMPTS_FEISHU.md."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "AI_MUSIC_PROMPTS_FEISHU.md"
OUT = ROOT / "backend" / "app" / "data" / "prompt_library.json"


def section(title_pat: str, text: str) -> str:
    m = re.search(title_pat, text)
    if not m:
        return ""
    start = m.end()
    nxt = re.search(r"\n## ", text[start:])
    return text[start : start + (nxt.start() if nxt else len(text))]


def bullets_under(md: str, heading: str) -> list[str]:
    m = re.search(rf"### {re.escape(heading)}\n\n([\s\S]*?)(?=\n### |\n## |\Z)", md)
    if not m:
        return []
    items: list[str] = []
    seen: set[str] = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("- "):
            it = line[2:].strip()
            if it and it not in seen:
                seen.add(it)
                items.append(it)
    return items


def main() -> None:
    md = MD.read_text(encoding="utf-8")

    quality = {
        "style_tag": "[ENHANCED STEREO, PROFESSIONAL PRISTINE MASTERING, FINAL STUDIO MIXDOWN]",
        "lyrics_tag": "[SUNO, PLAY:QUALITY_SOUND_COMPETITION_WINNER_FINAL_STUDIO_MIXDOWN.WAV]",
        "note": "适用于 Suno V4~5.0：style/tags 追加 style_tag；歌词栏可追加 lyrics_tag",
    }
    mentor_styles = [
        {"id": "wyy", "tag": "[wyy陈易狄 style]", "label": "陈易狄风", "bias": "人声、情感、民谣、流行、说唱"},
        {
            "id": "cyan",
            "tag": "[Cyan 青幻 studio style]",
            "label": "青幻工作室",
            "bias": "欧美、日韩、音效、编曲结构复杂性",
        },
    ]
    internal_codes = [
        "[complex morphing dynamic EQ]",
        "[complex dynamic multi-layering]",
        "[complex sound effects and sound samples]",
        "[Sophisticated sound design]",
        "[Rich genus chord details]",
        "[catchy chord details]",
        "[Brilliant song structure arrangement]",
        "[catchy and brilliant song composing]",
        "[Condenser Mic]",
        "[clear vocal]",
        "[Granularity]",
        "[440hz]",
        "[Vocal Compression]",
        "[complex sequencer]",
        "[ambience sounds effects]",
        "[masterpiece]",
    ]

    tpl_sec = section(r"## 【导师模板】", md)
    templates = []
    for m in re.finditer(r"### (.+)\n\n```\n([\s\S]*?)```", tpl_sec):
        title = m.group(1).strip()
        body = m.group(2).strip()
        tid = re.sub(r"[^\w]+", "_", title.lower())[:40].strip("_") or f"tpl_{len(templates)}"
        vocal = "female" if "女" in title else ("male" if "男" in title else "mixed")
        templates.append({"id": tid, "title": title, "prompt": body, "vocal": vocal, "source": "feishu_mentor"})

    struct = []
    meta_sec = section(r"### 基础 元标签\.xlsx", md)
    for line in meta_sec.splitlines():
        if not line.startswith("|") or "---" in line or line.startswith("| Key"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 2 or not parts[0].startswith("["):
            continue
        struct.append({"key": parts[0], "zh": parts[1], "explain": parts[2] if len(parts) > 2 else ""})

    genre_dict = []
    gsec = section(r"### 音乐流派大全\.xlsx", md)
    for line in gsec.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 2 or parts[0] in {"标签", ""}:
            continue
        genre_dict.append({"en": parts[0], "zh": parts[1]})

    featured_genres = [
        {"id": "pop", "label": "流行", "tags": ["Pop", "Popular"]},
        {"id": "cfolk", "label": "国风", "tags": ["Ethnic Fusion", "World/Ethnic"]},
        {"id": "cinematic", "label": "电影感", "tags": ["Cinematic"]},
        {"id": "electronic", "label": "电子", "tags": ["Electronic", "EDM", "Synthwave"]},
        {"id": "folk", "label": "民谣", "tags": ["Folk"]},
        {"id": "rnb", "label": "R&B", "tags": ["RnB", "Soul"]},
        {"id": "hiphop", "label": "嘻哈", "tags": ["HipHop", "Rap", "Trap"]},
        {"id": "jazz", "label": "爵士", "tags": ["Jazz", "Jazz/Soul"]},
        {"id": "rock", "label": "摇滚", "tags": ["Rock", "Indie"]},
        {"id": "ambient", "label": "氛围", "tags": ["Ambient", "Downtempo"]},
        {"id": "urban", "label": "城市流行", "tags": ["Urban", "Pop"]},
        {"id": "reggae", "label": "雷鬼", "tags": ["Reggae", "Dancehall"]},
    ]

    lib = {
        "version": 1,
        "source": "https://my.feishu.cn/wiki/K8BEwoUbqirSfmkMTHLch00mnMc",
        "source_doc": "docs/AI_MUSIC_PROMPTS_FEISHU.md",
        "quality_boost": quality,
        "mentor_styles": mentor_styles,
        "production_tags": internal_codes,
        "templates": templates,
        "structure_tags": struct,
        "genre_categories": bullets_under(md, "Genre（流派）"),
        "style_moods": bullets_under(md, "Styles（风格）"),
        "song_types": bullets_under(md, "Song Types（歌曲类型）"),
        "genre_dictionary": genre_dict,
        "featured_genres": featured_genres,
        "vocal_prompts": {
            "emotion": ["Sad", "Happy", "Romantic", "Melancholic", "Angry", "Hopeful", "Nostalgic", "Tender"],
            "style": ["Soulful", "Husky", "Intimate", "Ethereal", "Breathy", "Powerful", "Whisper", "Belting"],
            "effect": ["Reverb-Heavy", "Delay Echo", "Telephone Bandpass", "Auto-tune Soft", "Dry Close Mic"],
        },
        "sfx_tags": [
            "Rain Ambience",
            "Thunder",
            "Ocean Waves",
            "Heartbeat",
            "Footsteps",
            "Crowd Noise",
            "Church Bells",
            "Forest Ambience",
            "Cathedral Reverb",
        ],
        "rap_styles": [
            "Old school",
            "New school",
            "Boombap",
            "G-Funk",
            "Trap",
            "Drill",
            "Melodic Rap",
            "Lofi HipHop",
            "Pop HipHop",
            "Conscious Rap",
            "West Coast",
            "East Coast",
        ],
        "lyrics_rules": [
            "禁止：物品罗列、反问、「是不是」、随意符号堆砌",
            "增强：情绪主线贯穿；浅显金句；多结构押韵/谐音",
            "公式：氛围营造 → 情感倾诉 → 意象深化 → 情感总结",
            "歌词用 [Verse]/[Chorus]/[Bridge] 等结构标签；短句留白，跟着腔唱，忌念词腔",
            "格式末尾可加编曲/人声效果提示",
        ],
        "agent_quick": {
            "general": [
                {"label": "城市流行男声", "text": "用导师模板「男生 · 城市流行」出一首带记忆点的旋律，先纯音乐"},
                {"label": "华语女声抒情", "text": "参考「女生 · 华语流行抒情」风格，先出纯音乐再填词"},
                {"label": "国风融合", "text": "Ethnic Fusion 国风，笛子主旋律，空灵，纯音乐"},
            ],
            "melody": [
                {"label": "城市流行钩子", "text": "Urban Pop，BPM 88，明亮男声向编曲但无人声，突出副歌钩子"},
                {"label": "民谣钢琴弦乐", "text": "Folk，BPM 70，钢琴+弦乐，厚实而克制的纯音乐"},
                {"label": "Chill 说唱底床", "text": "Hip-hop melancholic groove，深合成贝斯+鼓机，无人声底床"},
            ],
            "lyrics": [
                {"label": "倾诉短句", "text": "按绑定旋律写对一个人的倾诉；短句留白；[Verse]/[Chorus]；禁止念词"},
                {"label": "副歌金句", "text": "副歌浅显金句+谐音；主歌字少；跟着腔走"},
                {"label": "国风意象", "text": "山野夜色、月光、旧城门意象；克制不堆砌"},
            ],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(lib, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {OUT} templates={len(templates)} struct={len(struct)} "
        f"genres={len(genre_dict)} bytes={OUT.stat().st_size}"
    )


if __name__ == "__main__":
    main()
