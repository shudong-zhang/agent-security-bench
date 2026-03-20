#!/bin/bash
# entrypoint.sh
# 容器启动入口。直接执行传入的命令（claude / bash 等）。
# 保持简单：不需要 Xvfb，因为我们的测试不涉及剪贴板。

set -e

# 如果没有传入命令，默认进 bash（调试用）
if [ $# -eq 0 ]; then
    exec bash
fi

exec "$@"
