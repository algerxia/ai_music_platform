# Studio AI ↔ suno.cn 功能覆盖验收表

> 更新：2026-11 · 目标为功能覆盖并强化「对话式 / 参数式」双轨与专业 Take 工作流。

| suno 能力 | Studio AI 入口 | 供给 | 状态 |
|---|---|---|---|
| 文生音乐 | 参数式 · 文生 / 对话式 | RunningHub Suno V5.5 single | 已接 |
| 自定义歌词+风格 | 参数式 · 自定义 | RunningHub Suno V5.5 custom | 已接 |
| MiniMax 可控生成 | 参数式 · MiniMax / 项目工作台 | RunningHub MiniMax 2.5/2.6 | 已有并保留 |
| Mureka | 参数式 · Mureka | RunningHub Mureka V9 | 已接 |
| 对话 Agent | `/create/agent` | Qwen 状态机 + 确认卡 | 已接（确认前不扣费） |
| Cover / 翻唱 | 工作台人声翻唱 / 工具提示 | MiniMax Music Cover | 已有 |
| 延长 / 续写 | Take「延长」 | Mureka extend | 已接 |
| 人声/伴奏分离 | 工具箱 · AI 分离 | RH extract + audio-worker | 已接 |
| 全轨分轨 | 分离 · demucs 后端 | audio-worker ffmpeg 近似分轨 | 已接（CPU 近似；可换真 Demucs） |
| 转 MIDI | 工具箱 | audio-worker（basic-pitch 可选） | 已接 |
| 转乐谱 | 工具箱 | audio-worker music21 可选 | 已接 |
| 降噪 / 音质 / 剪辑 / 合并 / 转码 | 工具箱 | audio-worker ffmpeg | 已接 |
| 音乐解析 / AI 歌词 | 工具箱 | Qwen | 已接 |
| 钢琴 / 音乐信 / LRC / 多音轨 | 我的 / 工具箱 | 前端 | 已接（轻量） |
| MV | `/mv` | audio-worker ffmpeg 成片 | 已接 |
| 发现广场 | `/discover` | 公开专辑 + 精选 | 已接 |
| 我的 · 专辑/喜欢/分轨/团队 | `/mine` | Postgres 新表 | 已接 |
| 音效 SFX | 工具成本表已留 | 可走 MiniMax/Suno 文生短描述 | 入口在能力表；可用文生代替 |

## 相对 suno 的增强

- 对话式 / 参数式显式分流
- Agent **确认卡门禁**，确认前不扣积分
- 版本不覆盖、A/B、能力旗标诚实展示
- 项目 / Take / 导出 ZIP 专业工作流

## 部署检查

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
curl -s http://127.0.0.1:8003/api/health
curl -s http://127.0.0.1:8090/health   # 若映射了 audio-worker 端口；默认仅内网
```

确保 `.env` 含 `RUNNINGHUB_API_KEY`、`DASHSCOPE_API_KEY`。
