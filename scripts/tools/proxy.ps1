# 彻底精简的终端代理注入脚本
Write-Host "🚀 正在为当前终端注入 30810 专属代理..." -ForegroundColor Yellow

$env:HTTP_PROXY="http://127.0.0.1:30810"
$env:HTTPS_PROXY="http://127.0.0.1:30810"
$env:ALL_PROXY="http://127.0.0.1:30810"

Write-Host "✅ 代理注入成功！当前终端已进入「无限制模式」。" -ForegroundColor Green
Write-Host "💡 现在你可以自由输入 docker compose build 或其他任何命令了。" -ForegroundColor Cyan