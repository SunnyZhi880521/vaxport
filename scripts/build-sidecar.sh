#!/bin/bash
# PyInstaller 打包脚本，输出 Tauri sidecar 兼容的二进制文件名
# 用法: bash scripts/build-sidecar.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# 安装 PyInstaller
pip install pyinstaller -q 2>/dev/null

# 打包
echo "==> PyInstaller 打包 vaxport..."
pyinstaller vaxport.spec --noconfirm --clean

# 确定目标三元组
detect_target() {
    case "$(uname -s)" in
        Darwin)  echo "x86_64-apple-darwin" ;;  # 注: Apple Silicon 也报 x86_64 给 Tauri sidecar
        Linux)   echo "x86_64-unknown-linux-gnu" ;;
        MINGW*|MSYS*|CYGWIN*)  echo "x86_64-pc-windows-msvc" ;;
        *)       echo "unknown-target" ;;
    esac
}

TARGET=$(detect_target)
EXT=""
[ "$(uname -s)" = "MINGW" ] || [ "$(uname -s)" = "MSYS" ] || [ "$(uname -s)" = "CYGWIN" ] && EXT=".exe"

# 输出到 Tauri sidecar 目录
BINARIES_DIR="./Vaxport-GUI/src-tauri/binaries"
mkdir -p "$BINARIES_DIR"

cp "dist/vaxport-api${EXT}" "$BINARIES_DIR/vaxport-api-${TARGET}${EXT}"
echo "==> 输出: $BINARIES_DIR/vaxport-api-${TARGET}${EXT}"