# 阿里云 ECS 部署与连接说明（给 Agent / 运维）

正规流程：**本机改代码 → commit → push GitHub `main` → 服务器 `git pull` + Compose 重建**。  
不要再用 `scp` 零散覆盖生产文件（紧急热修除外，事后必须补 commit/push）。

## 现网信息

| 项 | 值 |
| --- | --- |
| GitHub | `https://github.com/algerxia/ai_music_platform.git` |
| 部署分支 | `main` |
| 地域 | `cn-beijing` |
| 实例 ID | `i-2zecs3h1bypi5c01mkxe` |
| 公网 IP | **`59.110.35.36`**（注意不是 `59.119…`） |
| 安全组 | `sg-2zeaqz72g0obkpq3x0z9` |
| 项目目录 | `/opt/ai_music_platform`（**git clone**） |
| 对外入口 | http://59.110.35.36 （80）与 http://59.110.35.36:8080 |
| 栈 | Docker Compose：`web` / `api` / `worker` / `postgres` / `redis` |

健康检查：

```bash
curl -sS http://59.110.35.36/api/health
curl -sS http://59.110.35.36:8080/api/health
```

## 标准发布流程

```bash
# 1) 本机
cd /path/to/ai_music_platform
git status
git add …
git commit -m "…"
git push origin main

# 2) 一键部署到 ECS（本机执行）
./deploy/deploy.sh

# 等价：SSH 后手动
ssh -i ~/.ssh/id_ed25519 root@59.110.35.36 'bash /opt/ai_music_platform/deploy/remote_update.sh'
```

`remote_update.sh` 会：`git pull --ff-only` →  
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` → 探活。

生产专用覆盖在 `docker-compose.prod.yml`（国内镜像、CORS、80 端口）。`.env` **永不进 Git**，只留在服务器。

## 连接方式（优先顺序）

### 1. SSH（首选）

本机私钥：`~/.ssh/id_ed25519`（公钥已在服务器 `root` 的 `authorized_keys`）。

```bash
ssh -i ~/.ssh/id_ed25519 root@59.110.35.36
ssh -i ~/.ssh/id_ed25519 root@59.110.35.36 'cd /opt/ai_music_platform && git status && docker compose -f docker-compose.yml -f docker-compose.prod.yml ps'
```

### 2. Workbench CLI（SSH 不可用时）

`~/bin/workbench`，配置 `~/.workbench/config.json`。**禁止**把 AccessKey 写进仓库或聊天。

```bash
export PATH="$HOME/bin:$PATH"
workbench list
```

## 服务器日常命令

```bash
cd /opt/ai_music_platform

git status
git log -1 --oneline

docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=80 api worker web

# 仅前端已 pull 且只需重载 nginx 配置时
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web nginx -s reload
```

关键路径：

- 代码：`/opt/ai_music_platform`（跟踪 `origin/main`）
- 密钥：`/opt/ai_music_platform/.env`（**勿 cat 到聊天**）
- 编排：`docker-compose.yml` + `docker-compose.prod.yml`
- Nginx：`deploy/nginx.conf`

命名卷（与目录名绑定，换路径会丢数据映射）：`studio_pg` / `studio_redis` / `studio_data`。

安全组至少放行 TCP `22`、`80`、`8080`。

## 首次把现网改成 Git 部署（已做过可跳过）

在服务器上（保留 `.env` 与 Docker volumes）：

```bash
# 备份密钥
cp /opt/ai_music_platform/.env /root/ai_music_platform.env.bak

# 停栈（volumes 保留）
cd /opt/ai_music_platform && docker compose down || true

# 挪走旧目录（非 git），再 clone
mv /opt/ai_music_platform /opt/ai_music_platform.legacy_scp
git clone https://github.com/algerxia/ai_music_platform.git /opt/ai_music_platform
cp /root/ai_music_platform.env.bak /opt/ai_music_platform/.env
chmod +x /opt/ai_music_platform/deploy/*.sh

cd /opt/ai_music_platform
bash deploy/remote_update.sh
```

确认健康后，可择期删除 `/opt/ai_music_platform.legacy_scp`。

## 国内网络注意点

- 基础镜像走 DaoCloud：`docker.m.daocloud.io/library/...`（见 `docker-compose.prod.yml` / `backend/Dockerfile`）。
- pip 用阿里云源（清华在该 ECS 上曾 403）。
- 站点为 **HTTP**：无 `crypto.randomUUID`，前端用 `newIdempotencyKey`；改前端请 bump `index.html` 的 `app.js?v=`。

## 登录与密钥（勿泄露）

- 演示管理员见登录页；公网请尽快改密。
- RunningHub / 百炼 Key 只在服务器 `.env` 或设置页，**不要**读出贴到聊天。
- `.gitignore` 已忽略 `.env`。

## Agent 操作约束

1. **发布路径**：commit → `git push origin main` → `./deploy/deploy.sh`。禁止把未入库改动只 scp 到生产后了事。
2. 改生产前：`curl /api/health` + 服务器 `git log -1`。
3. 绝不打印 / 提交 AccessKey、`.env`、供应商 Key。
4. 公网 IP 以 **`59.110.35.36`** 为准。
5. 排障：页面 vs 生成；生成看 `worker` 日志与 `generation_jobs`；前端先看 `app.js?v=` 是否最新。
