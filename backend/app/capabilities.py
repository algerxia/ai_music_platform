CAPABILITY_SCHEMA_VERSION = "2026-10"

RUNNINGHUB_MUSIC = {
    "id": "runninghub-minimax-music-2.5",
    "provider": "runninghub",
    "model": "minimax-music-2.5",
    "label": "MiniMax Music 2.5",
    "schema_version": CAPABILITY_SCHEMA_VERSION,
    "job_types": {
        "initial_generate": {"supported": True, "note": "整首生成，结果作为新版本保留"},
        "variation": {"supported": True, "note": "基于选中版本整曲再生成，不覆盖旧文件"},
        "extend": {"supported": False, "note": "当前模型不支持精确续写/延长"},
        "region_regen": {"supported": False, "note": "当前模型不支持指定区域重生成"},
        "vocal_cover": {
            "supported": True,
            "note": "把成品上传到 RunningHub 后走 MiniMax Music Cover：参考音频 6 秒–6 分钟、≤50MB；歌词提交 10–1000 字；生成新 Take，不覆盖旧文件。不保证 1:1 保留编曲。",
        },
    },
    "parameters": {
        "prompt": {
            "min": 8,
            "max": 6000,
            "required": True,
            "provider_max": 2000,
            "note": "创作台可写 6000 字；提交 MiniMax 时自动压缩到 2000 字",
        },
        "lyrics": {
            "max": 3500,
            "required_if_vocal": True,
            "cover_max": 1000,
            "cover_min": 10,
            "note": "工作台可存 3500 字；Music Cover 提交时压缩到 1000 字",
        },
        "bpm": {"min": 40, "max": 240, "control": "approximate"},
        "key": {"control": "approximate"},
        "time_signature": {"control": "stored_only", "note": "写入项目元数据，模型不保证拍号"},
        "duration_seconds": {"options": [30, 60, 90, 120, 180, 240], "control": "approximate"},
        "instrumental": {"supported": True},
        "negative_prompt": {"control": "prompt_fused"},
        "output_count": {"min": 1, "max": 1, "note": "每次任务 1 条结果，多版本请连续生成"},
    },
    "outputs": {
        "mp3": {"supported": True, "native": True},
        "wav": {"supported": False, "note": "供应商返回 MP3；可下载原始文件与元数据清单"},
        "midi": {"supported": False, "note": "当前模型不输出 MIDI"},
        "stems": {"supported": False, "note": "当前模型不输出分轨"},
    },
    "limits": {
        "max_concurrent_jobs_per_user": 2,
        "max_active_jobs_per_project": 1,
        "cancel_when": ["queued"],
        "progress": "stage_only",
    },
}


def public_capabilities(music_configured: bool, qwen_configured: bool) -> dict:
    model = dict(RUNNINGHUB_MUSIC)
    model["configured"] = music_configured
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "qwen_assist": {
            "supported": qwen_configured,
            "provider": "dashscope",
            "lyrics": {
                "supported": qwen_configured,
                "max": 3500,
                "note": "生成完成后可按曲填词；不会改写已有 MP3，人声需另开一次生成",
            },
        },
        "music": model,
        "rights": {
            "commercial_use": "取决于 RunningHub / MiniMax 当期条款，平台不保证版权或独家",
            "training_opt_in_default": False,
            "terms_version": "1.0",
            "privacy_version": "1.0",
        },
    }
