# Ghost Shell One-Click Launcher v2.1
# Starts server and opens client in browser

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Fallback to absolute path if running from Desktop/Shortcut copy
if (-not (Test-Path (Join-Path $ScriptDir "ghost_server.py"))) {
    $ScriptDir = "F:\github\AIOT\ghost_shell"
}

$ServerScript = Join-Path $ScriptDir "ghost_server.py"

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "     Ghost Shell v2.1 Launcher      " -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan

# Kill existing python processes for clean start
$existing = Get-Process -Name python -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Stopping existing Python processes..." -ForegroundColor Yellow
    $existing | Stop-Process -Force
    Start-Sleep -Seconds 1
}

Write-Host "Starting Ghost Shell Server..." -ForegroundColor Green
Start-Process python -ArgumentList "-u", $ServerScript -WindowStyle Minimized
Start-Sleep -Seconds 2

# Open client in browser via localhost (with cache-bust timestamp)
Write-Host "Opening client in browser..." -ForegroundColor Green
$timestamp = [DateTimeOffset]::Now.ToUnixTimeSeconds()
Start-Process "http://localhost:8000?v=$timestamp"

Write-Host ""
Write-Host "Ghost Shell v2.1 is ready!" -ForegroundColor Green
Write-Host "- PC (HTTP):      http://localhost:8000" -ForegroundColor Gray
Write-Host "- Mobile (HTTPS): https://192.168.31.141:8444 (Accept cert)" -ForegroundColor Gray
Write-Host ""
Write-Host "Features:" -ForegroundColor Cyan
Write-Host "  - Multi-monitor support" -ForegroundColor Gray
Write-Host "  - Touch: Tap=Click, LongPress=RightClick, Swipe=Scroll" -ForegroundColor Gray
Write-Host ""
Write-Host "Press any key to exit (server keeps running)..." -ForegroundColor Yellow
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

