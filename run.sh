#!/bin/bash
# PolySleuth 启动脚本

echo "🔍 PolySleuth - Polymarket 刷量取证分析系统"
echo "============================================="

# 切换到项目目录
cd "$(dirname "$0")"

# 创建日志目录
mkdir -p logs

# 检查 Python 版本
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "📌 Python 版本: $PYTHON_VERSION"

# 检查依赖
echo "📦 检查依赖..."
pip install -q fastapi uvicorn web3 requests sqlalchemy pydantic

# 启动后端
echo ""
echo "🚀 启动 PolySleuth 后端..."
echo "📍 API 地址: http://localhost:8000"
echo "📖 文档地址: http://localhost:8000/docs"
echo "🌐 前端地址: http://localhost:8000"
echo "📝 日志文件: $(pwd)/logs/polysleuth.log"
echo ""

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
