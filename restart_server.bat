@echo off
echo 正在停止现有Flask服务器...
taskkill /F /IM python.exe > nul 2>&1
if %ERRORLEVEL% == 0 (
    echo 已停止所有Python进程。
) else (
    echo 未找到运行的Python进程。
)

echo 正在启动Flask服务器...
cd /d "c:\Users\13826\WorkBuddy\Claw\stock-portfolio"
start "A股投资助手服务器" python server.py
echo 服务器已启动，请在新打开的窗口中查看日志。
echo 注意：不要关闭新窗口，否则服务器将停止。
echo 按任意键退出此脚本...
pause > nul