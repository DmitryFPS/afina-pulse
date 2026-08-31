@echo off
cd /d "%~dp0"
set AFINA_HOST=0.0.0.0
set AFINA_PORT=8091
echo Starting Afina Watch on http://127.0.0.1:8091
py -3 scripts\ui_server.py
if errorlevel 1 python scripts\ui_server.py
pause
