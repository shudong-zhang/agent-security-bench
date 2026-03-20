#!/bin/bash
# docker/build.sh
# 构建 agent-bench 沙箱镜像
set -e

# ── 代理配置（按需填写）──────────────────────────
HTTP_PROXY=""
HTTPS_PROXY=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "[build] Building agent-bench:latest ..."
docker build -t agent-bench:latest \
    --build-arg HTTP_PROXY="$HTTP_PROXY" \
    --build-arg HTTPS_PROXY="$HTTPS_PROXY" \
    --build-arg http_proxy="$HTTP_PROXY" \
    --build-arg https_proxy="$HTTPS_PROXY" \
    -f docker/Dockerfile docker/

echo "[done] Image agent-bench:latest ready."
echo ""
echo "Next steps:"
echo "  1. cp docker/.env.example docker/.env  # 填入 API key"
echo "  2. export ANTHROPIC_API_KEY=xxx         # 或直接用 .env"
echo "  3. python main.py --surface local_file  # 运行 benchmark"
