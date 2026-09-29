@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   制药企业产品成本智能分析报告系统 - 一键启动
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 python 命令，请先安装 Python 3.10+ 并勾选 "Add to PATH"
  pause
  exit /b 1
)

echo [1/3] 安装依赖（已安装会自动跳过）...
python -m pip install -r requirements.txt -q
if exist requirements-optional.txt (
  echo       安装可选增强依赖（PDF 高保真导出：调用本机 Word，失败不影响运行）...
  python -m pip install -r requirements-optional.txt -q 2>nul
)

echo [2/3] 启动官方模拟 RPA 服务（端口 8090）...
start "mock-rpa-8090" cmd /c python -c "from app.paths import rpa_server_file; import subprocess,sys; subprocess.run([sys.executable, rpa_server_file()])" ^& pause
timeout /t 3 >nul

echo [3/3] 启动本系统（端口 8000）...
echo      浏览器访问： http://127.0.0.1:8000
echo      按 Ctrl+C 可停止本服务
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

pause
