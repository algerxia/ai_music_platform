#!/usr/bin/env bash
# 在 ECS 上执行：更新代码并重建 Compose（保留 .env 与 Docker volumes）。
# 支持两种更新源：
#   1) GIT_BUNDLE=/path/to.bundle  （推荐：本机 deploy.sh 传来，避开 ECS→GitHub 不稳定）
#   2) 直接 git pull origin（偶发可用；国内 ECS 常失败）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .git ]]; then
  echo "ERROR: $ROOT 不是 git 仓库。请按 deploy/ECS_OPS.md 先完成 git 化。" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "ERROR: 缺少 .env（密钥不进 Git）。请从备份恢复后再部署。" >&2
  exit 1
fi

BRANCH="${DEPLOY_BRANCH:-main}"

if [[ -n "${GIT_BUNDLE:-}" ]]; then
  echo "==> git fetch from bundle: $GIT_BUNDLE"
  git fetch "$GIT_BUNDLE" "$BRANCH:$BRANCH"
  git checkout "$BRANCH"
  git reset --hard "$BRANCH"
elif [[ "${SKIP_GIT_PULL:-0}" == "1" ]]; then
  echo "==> SKIP_GIT_PULL=1，使用当前工作树 $(git rev-parse --short HEAD)"
else
  echo "==> git fetch / checkout $BRANCH / pull --ff-only"
  git fetch origin
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
fi

echo "==> docker compose up -d --build (prod overlay)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

echo "==> reload nginx (ignore if web not ready)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T web nginx -s reload 2>/dev/null || true

echo "==> health"
curl -fsS http://127.0.0.1/api/health || curl -fsS http://127.0.0.1:8080/api/health
echo
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
echo "==> done $(git rev-parse --short HEAD)"
