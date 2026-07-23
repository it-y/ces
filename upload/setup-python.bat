@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  Setup Embedded Python 3.13
echo ========================================

set PYTHON_VERSION=3.13.12
set PYTHON_ZIP=python-%PYTHON_VERSION%-embed-amd64.zip
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/%PYTHON_ZIP%
set PYTHON_DIR=python

:: Check if Python already exists
if exist "%PYTHON_DIR%\python.exe" (
    echo [OK] Embedded Python already exists at %PYTHON_DIR%\
    goto :CHECK_PACKAGES
)

echo [1/5] Creating directory...
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

echo [2/5] Downloading Python %PYTHON_VERSION% embedded...
echo       URL: %PYTHON_URL%
curl -L -o "%PYTHON_ZIP%" "%PYTHON_URL%"
if errorlevel 1 (
    echo [ERROR] Download failed. Check network connection.
    pause
    exit /b 1
)

echo [3/5] Extracting...
tar -xf "%PYTHON_ZIP%" -C "%PYTHON_DIR%"
if errorlevel 1 (
    echo [ERROR] Extract failed.
    pause
    exit /b 1
)
del "%PYTHON_ZIP%"

echo [4/5] Enabling pip in embedded Python...
:: Enable site module
echo import site > "%PYTHON_DIR%\sitecustomize.py"

:: Fix _pth file to allow site-packages
for %%F in ("%PYTHON_DIR%\python*._pth") do (
    echo %%F
    copy /Y "%%F" "%%F.bak"
    (
        echo python%PYTHON_VERSION:~0,4%.zip
        echo.
        echo import site
    ) > "%%F"
)

echo [5/5] Installing pip...
curl -L -o "%PYTHON_DIR%\get-pip.py" https://bootstrap.pypa.io/get-pip.py
"%PYTHON_DIR%\python.exe" "%PYTHON_DIR%\get-pip.py" --no-warn-script-location
if errorlevel 1 (
    echo [WARN] pip install may have failed. Trying alternative...
    "%PYTHON_DIR%\python.exe" -m ensurepip --upgrade
)

:CHECK_PACKAGES
echo.
echo [INFO] Installing Python packages from requirements.txt...
if exist requirements.txt (
    "%PYTHON_DIR%\python.exe" -m pip install -r requirements.txt --no-warn-script-location --target "%PYTHON_DIR%\Lib\site-packages"
    if errorlevel 1 (
        echo [WARN] Some packages may not have been installed.
    ) else (
        echo [OK] Packages installed.
    )
) else (
    echo [WARN] requirements.txt not found.
)

:: Verify
echo.
echo [INFO] Verifying installation...
"%PYTHON_DIR%\python.exe" -c "import fastapi; import uvicorn; import httpx; import pydantic; from PIL import Image; import aiofiles; import websockets; print('[OK] All core packages loaded successfully')"
if errorlevel 1 (
    echo [WARN] Some packages failed to import. Check the output above.
)

echo.
echo ========================================
echo  Setup Complete!
echo  Python: %PYTHON_DIR%\python.exe
echo ========================================
pause
