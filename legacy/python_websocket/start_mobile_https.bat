@echo off
setlocal
echo Starting Ghost Shell Server (HTTPS Mode for Mobile Clipboard)...

:: Kill existing python
taskkill /IM python.exe /F >nul 2>&1

:: Start Server with HTTPS flag
start /min python ghost_server.py --https

echo Waiting for server...
timeout /t 2 >nul

echo Opening browser...
start https://localhost:8000

echo.
echo Ghost Shell HTTPS Mode Ready!
echo PC:     https://localhost:8000
echo Mobile: https://192.168.31.141:8000 (Accept Certificate)
echo.
pause
