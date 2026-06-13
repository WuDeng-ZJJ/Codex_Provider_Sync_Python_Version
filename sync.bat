@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=codex_provider_sync.py"

echo ============================================
echo  Codex Session Recovery / Provider Sync
echo ============================================
echo.
echo [Step 1] Status (read-only)
echo --------------------------------------------
python -X utf8 "%PY%" --status
echo.

echo [Step 2] Dry-run preview (no write)
echo --------------------------------------------
python -X utf8 "%PY%" --sync --dry-run
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
python -X utf8 "%PY%" --sync
echo.
echo Done. Reopen Codex to see the list.
echo.
pause
