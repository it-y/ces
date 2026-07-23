@echo off
cd /d "%~dp0"

echo === Infinite Canvas ===

:: Kill existing server on port 3000 (match :3000 exactly to avoid matching :30001 etc)
echo [1/3] Killing old processes on port 3000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr /r "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr /r "ESTABLISHED"') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr /r "TIME_WAIT"') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Also kill any python.exe running uvicorn (catches cases where port was already freed)
echo [2/3] Killing stale uvicorn processes...
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*uvicorn*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host ('Killed ' + $p.Count + ' process(es)') } else { Write-Host 'None found' }" 2>nul

:: Wait for port release
timeout /t 2 /nobreak >nul

:: Start browser and server
echo [3/3] Starting server...
start http://127.0.0.1:3000
python -m uvicorn app.main:app --host 127.0.0.1 --port 3000

echo.
pause
