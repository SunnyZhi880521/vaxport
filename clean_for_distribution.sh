#!/bin/bash
# 清理 vaxport 项目中的构建产物和临时文件
# 用于打包发送给其他机器进行构建

set -e

echo "🧹 开始清理 vaxport 项目..."

# 清理 PyInstaller 构建产物
echo "  清理 PyInstaller 构建产物..."
rm -rf build/
rm -rf dist/

# 清理 Tauri/Rust 构建产物
echo "  清理 Tauri/Rust 构建产物..."
rm -rf Vaxport-GUI/node_modules/
rm -rf Vaxport-GUI/src-tauri/target/
rm -rf Vaxport-GUI/src-tauri/binaries/
rm -rf Vaxport-GUI/dist/

# 清理 Python 缓存
echo "  清理 Python 缓存..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
find . -type f -name "*.pyo" -delete 2>/dev/null || true

# 清理 macOS 临时文件
echo "  清理 macOS 临时文件..."
find . -name ".DS_Store" -delete 2>/dev/null || true

echo ""
echo "✅ 清理完成！"
echo ""
echo "项目现在可以压缩发送到其他机器进行构建。"
echo "在目标机器上需要运行："
echo "  - Python 依赖：pip install -r requirements.txt"
echo "  - 前端依赖：cd Vaxport-GUI && pnpm install"
echo "  - 然后执行打包流程"
