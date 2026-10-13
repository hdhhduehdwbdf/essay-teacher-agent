@echo off
rem Essay Teacher - one-click start (server runs in a minimized window)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    start "essay-teacher" /min py web_server.py
) else (
    start "essay-teacher" /min python web_server.py
)
timeout /t 3 >nul
start "" http://127.0.0.1:8778
echo [OK] server starting, browser will open http://127.0.0.1:8778
