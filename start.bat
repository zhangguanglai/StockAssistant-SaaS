@echo off
chcp 65001 >nul
echo ========================================
echo   A股持仓管家 - 启动中...
echo ========================================
echo.

cd /d "%~dp0"

echo [1/2] 检查依赖...
pip install -r requirements.txt -q 2>nul

echo [2/2] 启动服务...
echo.
echo 访问地址: http://127.0.0.1:5000
echo 按 Ctrl+C 停止服务
echo.

start http://127.0.0.1:5000
python server.py

pause
