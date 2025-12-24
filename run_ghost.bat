@echo off
chcp 65001 >nul
title Ghost Shell

cd /d "F:\github\AIOT\ghost_shell_websocket"
taskkill /f /im python.exe 2>nul

echo.
echo Starting Ghost Shell...
echo.

python ghost_server.py

pause
