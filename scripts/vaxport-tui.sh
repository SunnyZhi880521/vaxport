#!/bin/bash
# vaxport-tui — 启动 FastAPI 后端 + Go Bubble Tea TUI

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
GO="${GO:-/usr/lib/go-1.24/bin/go}"

# 检查后端是否已在运行
if ! curl -s http://127.0.0.1:8931/api/health > /dev/null 2>&1; then
    echo "启动 FastAPI 后端..."
    python3 -m uvicorn vaxport.api.server:app --host 127.0.0.1 --port 8931 &
    sleep 2
fi

# 启动 Go TUI（exec 让 Go 进程直接接管终端）
export CGO_ENABLED=0
export GOTOOLCHAIN=local
export GOPROXY="${GOPROXY:-https://goproxy.cn,direct}"

cd "$DIR/tui"
exec "$GO" run .