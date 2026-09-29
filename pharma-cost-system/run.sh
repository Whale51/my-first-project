#!/usr/bin/env bash
# 制药企业产品成本智能分析报告系统 - 一键启动（macOS / Linux）
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  制药企业产品成本智能分析报告系统 - 一键启动"
echo "============================================================"

command -v python3 >/dev/null 2>&1 || { echo "[错误] 未检测到 python3，请先安装 Python 3.10+"; exit 1; }

echo "[1/3] 安装依赖（已安装会自动跳过）..."
python3 -m pip install -r requirements.txt -q
if [ -f requirements-optional.txt ]; then
  echo "      安装可选增强依赖（PDF 高保真导出，失败不影响运行）..."
  python3 -m pip install -r requirements-optional.txt -q || true
fi

echo "[2/3] 启动官方模拟 RPA 服务（端口 8090）..."
python3 -c "from app.paths import rpa_server_file; import subprocess,sys; subprocess.run([sys.executable, rpa_server_file()])" &
sleep 3

echo "[3/3] 启动本系统（端口 8000）..."
echo "    浏览器访问： http://127.0.0.1:8000"
echo "    按 Ctrl+C 可停止本服务"
echo
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
