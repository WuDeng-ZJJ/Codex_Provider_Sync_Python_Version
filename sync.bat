@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=codex_provider_sync.py"

REM --- Detect Python (prefer 'python', fall back to 'py' launcher) ---
set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE ( where py >nul 2>nul && set "PYEXE=py" )
if not defined PYEXE (
    echo ============================================
    echo  Python NOT found
    echo ============================================
    echo.
    echo This tool needs Python 3.8 or newer.
    echo.
    echo Please install it from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: during install, check
    echo   "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo ============================================
echo  Codex Session Recovery / Provider Sync
echo ============================================
echo.
echo [Step 1] Status (read-only)
echo --------------------------------------------
%PYEXE% -X utf8 "%PY%" --status
echo.

echo [Step 2] Dry-run preview (no write)
echo --------------------------------------------
%PYEXE% -X utf8 "%PY%" --sync --dry-run
echo.

echo ============================================
set /p "OK=Type Y to sync for real (auto backup), any other key to cancel: "
if /i not "%OK%"=="Y" (
    echo Cancelled. Nothing changed.
    echo.
    pause
    exit /b 0
)

echo.
echo [Step 3] Syncing...
echo --------------------------------------------
%PYEXE% -X utf8 "%PY%" --sync
echo.
echo Done. Reopen Codex to see the list.
echo.
pause
