Write-Host "正在停止现有Flask服务器..." -ForegroundColor Yellow
$pythonProcesses = Get-Process python -ErrorAction SilentlyContinue
if ($pythonProcesses) {
    $pythonProcesses | Stop-Process -Force
    Write-Host "已停止 $($pythonProcesses.Count) 个Python进程。" -ForegroundColor Green
} else {
    Write-Host "未找到运行的Python进程。" -ForegroundColor Gray
}

Write-Host "正在启动Flask服务器..." -ForegroundColor Yellow
Set-Location "c:\Users\13826\WorkBuddy\Claw\stock-portfolio"
Start-Process python -ArgumentList "server.py" -WorkingDirectory $PWD -NoNewWindow
Write-Host "服务器已启动！" -ForegroundColor Green
Write-Host "请在新打开的窗口中查看日志。" -ForegroundColor Cyan
Write-Host "注意：不要关闭新窗口，否则服务器将停止。" -ForegroundColor Yellow