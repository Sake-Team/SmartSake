@echo off
echo Stopping mock_server.py...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq mock_server*" >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8080" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo Starting mock_server.py...
start "mock_server" /B python "%~dp0mock_server.py"
timeout /t 2 /nobreak >nul

echo Mock server restarted. Open http://localhost:8080
pause
