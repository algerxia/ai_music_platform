CAPABILITY_SCHEMA_VERSION = "2026-11"

CREDIT_COSTS = {
    "minimax_generate": 10,
    "suno_generate": 20,
    "mureka_generate": 50,
    "extend": 30,
    "vocal_cover": 15,
    "stem_rh": 10,
    "stem_demucs": 20,
    "midi": 20,
    "score": 20,
    "denoise": 10,
    "improve": 10,
    "crop": 10,
    "merge": 10,
    "convert": 5,
    "analyze": 40,
    "lyrics": 4,
    "sfx": 4,
    "mv_render": 30,
    "agent_message": 0,
}


RUNNINGHUB_MUSIC = {
    "id": "runninghub-minimax-music",
    "provider": "runninghub",
    "engine": "minimax",
    "model": "minimax-music-2.6",
    "label": "MiniMax Music",
    "schema_version": CAPABILITY_SCHEMA_VERSION,
    "modes": ["text", "custom", "parametric"],
    "job_types": {
        "initial_generate": {"supported": True, "note": "参数式整首生成，结果作为新版本保留"},
        "variation": {"supported": True, "note": "基于选中版本整曲再生成，不覆盖旧文件"},
        "extend": {"supported": False, "note": "MiniMax 无精确续写；请改用 Mureka 延长"},
        "region_regen": {"supported": False, "note": "当前模型不支持指定区域重生成"},
        "vocal_cover": {
            "supported": True,
            "note": "Music Cover：参考音频 6 秒–6 分钟、≤50MB；歌词 10–1000 字。",
        },
    },
    "parameters": {
        "prompt": {"min": 8, "max": 6000, "required": True, "provider_max": 2000},
        "lyrics": {"max": 3500, "required_if_vocal": True, "cover_max": 1000, "cover_min": 10},
        "bpm": {"min": 40, "max": 240, "control": "approximate"},
        "key": {"control": "approximate"},
        "time_signature": {"control": "stored_only"},
        "duration_seconds": {"options": [30, 60, 90, 120, 180, 240], "control": "approximate"},
        "instrumental": {"supported": True},
        "negative_prompt": {"control": "prompt_fused"},
        "output_count": {"min": 1, "max": 1},
    },
    "outputs": {
        "mp3": {"supported": True, "native": True},
        "wav": {"supported": False},
        "midi": {"supported": False},
        "stems": {"supported": False},
    },
    "credit_cost": CREDIT_COSTS["minimax_generate"],
}

SUNO_MUSIC = {
    "id": "runninghub-suno-v5.5",
    "provider": "runninghub",
    "engine": "suno",
    "model": "suno-v5.5",
    "label": "苏诺 Suno V5.5",
    "modes": ["text", "custom"],
    "job_types": {
        "initial_generate": {"supported": True, "note": "文生一次约两首备选"},
        "custom_generate": {"supported": True, "note": "自定义歌词 + 风格标签"},
        "variation": {"supported": False, "note": "请用参数式 MiniMax 做变体，或重新自定义生成"},
        "extend": {"supported": False, "note": "请改用 Mureka 延长"},
        "vocal_cover": {"supported": False, "note": "请使用 MiniMax Music Cover"},
    },
    "parameters": {
        "description": {"max": 400, "required_for": "text"},
        "title": {"max": 80},
        "lyrics": {"max": 5000, "required_for": "custom"},
        "tags": {"max": 1000, "required_for": "custom"},
        "instrumental": {"supported": True},
    },
    "outputs": {"mp3": {"supported": True, "native": True}},
    "credit_cost": CREDIT_COSTS["suno_generate"],
}

MUREKA_MUSIC = {
    "id": "runninghub-mureka-v9",
    "provider": "runninghub",
    "engine": "mureka",
    "model": "mureka-v9",
    "label": "Mureka V9",
    "modes": ["custom", "extend"],
    "job_types": {
        "initial_generate": {"supported": True, "note": "歌词 + 风格；可选参考/音色/旋律"},
        "extend": {"supported": True, "note": "从指定时间点尾部延长（v7.6）"},
        "vocal_cover": {"supported": False},
    },
    "parameters": {
        "lyrics": {"max": 3000, "required": True},
        "prompt": {"max": 1024},
        "n": {"min": 1, "max": 3, "default": 2},
        "reference": {"supported": True, "min_seconds": 30},
    },
    "outputs": {"mp3": {"supported": True, "native": True}},
    "credit_cost": CREDIT_COSTS["mureka_generate"],
    "extend_credit_cost": CREDIT_COSTS["extend"],
}

TOOLS = {
    "ai_lyrics": {"supported": True, "provider": "qwen", "credit_cost": CREDIT_COSTS["lyrics"]},
    "ai_cover": {"supported": True, "provider": "runninghub", "credit_cost": CREDIT_COSTS["vocal_cover"]},
    "music_to_midi": {"supported": True, "provider": "audio_worker", "credit_cost": CREDIT_COSTS["midi"]},
    "midi_to_score": {"supported": True, "provider": "audio_worker", "credit_cost": CREDIT_COSTS["score"]},
    "music_analysis": {"supported": True, "provider": "qwen", "credit_cost": CREDIT_COSTS["analyze"]},
    "improve": {"supported": True, "provider": "audio_worker", "credit_cost": CREDIT_COSTS["improve"]},
    "separation": {"supported": True, "provider": "runninghub+audio_worker", "credit_cost": CREDIT_COSTS["stem_rh"]},
    "crop": {"supported": True, "provider": "audio_worker", "credit_cost": CREDIT_COSTS["crop"]},
    "merge": {"supported": True, "provider": "audio_worker", "credit_cost": CREDIT_COSTS["merge"]},
    "denoise": {"supported": True, "provider": "audio_worker", "credit_cost": CREDIT_COSTS["denoise"]},
    "audio_convert": {"supported": True, "provider": "audio_worker", "credit_cost": CREDIT_COSTS["convert"]},
    "multi_track": {"supported": True, "provider": "frontend", "credit_cost": 0},
    "piano": {"supported": True, "provider": "frontend", "credit_cost": 0},
    "lrc": {"supported": True, "provider": "frontend+qwen", "credit_cost": 0},
    "music_letter": {"supported": True, "provider": "frontend", "credit_cost": 0},
    "auto_lyrics": {"supported": True, "provider": "runninghub+audio_worker", "credit_cost": CREDIT_COSTS["vocal_cover"]},
    "sfx": {"supported": True, "provider": "runninghub", "credit_cost": CREDIT_COSTS["sfx"]},
    "mv": {"supported": True, "provider": "audio_worker", "credit_cost": CREDIT_COSTS["mv_render"]},
}


def public_capabilities(music_configured: bool, qwen_configured: bool, audio_worker_ok: bool = False) -> dict:
    minimax = dict(RUNNINGHUB_MUSIC)
    minimax["configured"] = music_configured
    suno = dict(SUNO_MUSIC)
    suno["configured"] = music_configured
    mureka = dict(MUREKA_MUSIC)
    mureka["configured"] = music_configured
    tools = {}
    for key, meta in TOOLS.items():
        item = dict(meta)
        provider = meta.get("provider", "")
        if provider == "qwen" or provider.startswith("frontend"):
            item["available"] = qwen_configured if "qwen" in provider else True
        elif "audio_worker" in provider:
            item["available"] = audio_worker_ok or music_configured
        else:
            item["available"] = music_configured
        if provider == "qwen":
            item["available"] = qwen_configured
        tools[key] = item
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "create_modes": {
            "conversational": {
                "id": "agent",
                "label": "对话式生成",
                "supported": qwen_configured,
                "note": "先作词锁定 → 再按词谱曲；作词确认不计费，谱曲确认后按 RunningHub 计费",
            },
            "parametric": {
                "id": "parametric",
                "label": "参数式生成",
                "supported": music_configured,
                "note": "结构化参数 + 模型选择；版本不覆盖，支持 A/B",
            },
        },
        "engines": {
            "minimax": minimax,
            "suno": suno,
            "mureka": mureka,
        },
        "music": minimax,
        "tools": tools,
        "credit_costs": CREDIT_COSTS,
        "qwen_assist": {
            "supported": qwen_configured,
            "provider": "dashscope",
            "lyrics": {"supported": qwen_configured, "max": 3500},
            "agent": {"supported": qwen_configured},
        },
        "audio_worker": {"available": audio_worker_ok},
        "rights": {
            "commercial_use": "取决于 RunningHub / MiniMax / Suno / Mureka 当期条款，平台不保证版权或独家",
            "training_opt_in_default": False,
            "terms_version": "1.0",
            "privacy_version": "1.0",
        },
    }
