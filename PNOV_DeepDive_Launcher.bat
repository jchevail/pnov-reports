@echo off
title PNOV Deep Dive — Auto-Update Launcher
echo ============================================
echo   PNOV Deep Dive — Checking for updates...
echo ============================================
echo.

:: Download latest version from GitHub
curl -sL "https://raw.githubusercontent.com/jchevail/pnov-reports/main/pnov_deep_dive.py" -o "%TEMP%\pnov_deep_dive.py"

if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Could not download latest version. Using local copy if available.
    if exist "%~dp0pnov_deep_dive.py" (
        set "SCRIPT=%~dp0pnov_deep_dive.py"
    ) else (
        echo [ERROR] No local copy found and download failed.
        pause
        exit /b 1
    )
) else (
    echo [OK] Latest version downloaded.
    set "SCRIPT=%TEMP%\pnov_deep_dive.py"
)

:: Check Python is installed
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Install/update dependencies silently
echo.
echo Installing dependencies...
pip install selenium webdriver-manager --quiet --disable-pip-version-check 2>nul

:: Download geckodriver if not present (avoids GitHub API rate limit issues)
if not exist "%TEMP%\geckodriver.exe" (
    echo Downloading geckodriver...
    curl -sL "https://github.com/mozilla/geckodriver/releases/download/v0.35.0/geckodriver-v0.35.0-win64.zip" -o "%TEMP%\geckodriver.zip"
    if exist "%TEMP%\geckodriver.zip" (
        powershell -Command "Expand-Archive -Force '%TEMP%\geckodriver.zip' '%TEMP%'" 2>nul
        if exist "%TEMP%\geckodriver.exe" (
            echo [OK] geckodriver ready.
        ) else (
            echo [WARNING] Could not extract geckodriver. The tool will try webdriver_manager.
        )
        del "%TEMP%\geckodriver.zip" 2>nul
    )
) else (
    echo [OK] geckodriver already available.
)

:: Launch the tool
echo.
echo Launching PNOV Deep Dive...
echo.
python "%SCRIPT%"

pause
