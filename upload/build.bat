@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  Infinite Canvas - Desktop Build
echo ========================================

:: Check requirements
where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Please install Node.js first.
    pause
    exit /b 1
)

:: Step 1: Setup embedded Python
echo.
echo [1/4] Checking embedded Python...
if not exist "python\python.exe" (
    echo [INFO] Python 3.13 not found. Run setup-python.bat first.
    echo        Or press any key to run it now...
    pause >nul
    call setup-python.bat
    if errorlevel 1 (
        pause
        exit /b 1
    )
) else (
    echo [OK] Python found at python\python.exe
)

:: Step 2: Install npm dependencies
echo.
echo [2/4] Installing npm dependencies...
call npm install
if errorlevel 1 (
    echo [ERROR] npm install failed.
    pause
    exit /b 1
)
echo [OK] npm dependencies installed.

:: Step 3: Build Electron app
echo.
echo [3/4] Building Electron app (this may take a while)...
call npx electron-builder --win --x64
if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

:: Step 4: Show result
echo.
echo ========================================
echo  Build Complete!
echo ========================================
for /f "delims=" %%i in ('dir /b /s "dist-electron\*.exe" 2^>nul') do (
    echo  Installer: %%i
)
echo ========================================

pause
