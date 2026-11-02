#!/usr/bin/env bash
# 本机执行：push（可选）→ 打 bundle → scp → ECS 更新。
# 默认用 bundle，避免 ECS 直连 GitHub 不稳定。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${ECS_HOST:-59.110.35.36}"
USER="${ECS_USER:-root}"
KEY="${ECS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${ECS_DIR:-/opt/ai_music_platform}"
BRANCH="${DEPLOY_BRANCH:-main}"
BUNDLE_LOCAL="${TMPDIR:-/tmp}/ai_music_platform.deploy.bundle"
BUNDLE_REMOTE="/tmp/ai_music_platform.deploy.bundle"

cd "$ROOT"

if [[ "${SKIP_PUSH:-0}" != "1" ]]; then
  echo "==> ensure $BRANCH is pushed"
  # HTTPS 在本机也可能要登录；优先已配置的 SSH origin
  git push origin "$BRANCH"
fi

echo "==> create git bundle ($BRANCH)"
git bundle create "$BUNDLE_LOCAL" "$BRANCH"

echo "==> scp bundle → ${USER}@${HOST}"
scp -i "$KEY" -o ConnectTimeout=15 "$BUNDLE_LOCAL" "${USER}@${HOST}:${BUNDLE_REMOTE}"

echo "==> remote_update via bundle"
ssh -i "$KEY" -o ConnectTimeout=15 "${USER}@${HOST}" \
  "GIT_BUNDLE=${BUNDLE_REMOTE} bash ${REMOTE_DIR}/deploy/remote_update.sh"
