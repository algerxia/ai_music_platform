#!/usr/bin/env bash
# 本机执行：SSH 到 ECS 跑 remote_update.sh（须先 git push）。
set -euo pipefail

HOST="${ECS_HOST:-59.110.35.36}"
USER="${ECS_USER:-root}"
KEY="${ECS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${ECS_DIR:-/opt/ai_music_platform}"

ssh -i "$KEY" -o ConnectTimeout=15 "${USER}@${HOST}" \
  "bash ${REMOTE_DIR}/deploy/remote_update.sh"
