# Studio AI · 全功能本地平台

基于 Docker 的完整工作室：账号、项目、积分账本、异步生成队列、MinIO 对象存储、RunningHub 真人声出歌。

## 一键启动

1. 确保 Docker Desktop 已启动  
2. 配置根目录 `.env`（至少 `RUNNINGHUB_API_KEY`，可选 `DASHSCOPE_API_KEY`）  
3. 启动：

```powershell
docker compose up -d --build
```

打开 <http://127.0.0.1:8080>

默认账号：

- 邮箱：`admin@studio.ai`
- 密码：`studio123`
- 初始积分：200（每次生成预留 10，失败自动退回）

## 组件

| 服务 | 端口 | 作用 |
|---|---|---|
| web (nginx) | 8080 | 前端 |
| api | 8003 | FastAPI |
| worker | - | Redis 队列消费 / RunningHub 出歌 |
| postgres | 5432 | 用户/项目/任务/积分 |
| redis | 6379 | 异步任务队列 |
| 本地卷 `studio_data` | - | 音频文件持久化（当前环境 MinIO 镜像不可用，已用本地卷替代） |

## 已实现能力（相对 PRD P0）

- 邮箱注册 / 登录 / JWT 会话 / 本机一次性令牌重置密码
- 项目创建、编辑、归档、复制、删除、列表搜索
- 异步生成任务（queued → running → succeeded/failed），失败可重试
- 积分预留 / 结算 / 失败退回账本
- 素材库、同源鉴权播放与下载、ZIP 导出（MP3 + generation.txt）
- 工作室最近任务、通知、设置（资料/密码/隐私申请）
- Qwen 创作副驾
- RunningHub MiniMax Music 2.5 真人声/纯音乐

## 尚未做成产品级的部分（按 PRD 诚实标注，不假装可用）

- 真实支付 / 订阅续费 webhook
- 团队协作、OAuth、MFA
- MIDI / stems 分轨导出
- 邮件验证码（本机用一次性令牌代替）

## 旧版本机双进程（无 Docker）

仍可用 `python -m backend.server`（旧 stdlib API）+ `python -m http.server 5173 --directory frontend`，但完整平台请走 Docker。
