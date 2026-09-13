@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
set "SYNC_EXIT_CODE=0"

set "CLI=%~dp0codex_provider_local_launcher.py"
set "DELETE_HELPER=%~dp0codex_delete_conversation.py"
set "CLOSE_CODEX=%~dp0close_codex_desktop.ps1"
set "SYNC_CODEX_HOME=%CODEX_HOME%"
if not defined SYNC_CODEX_HOME set "SYNC_CODEX_HOME=%USERPROFILE%\.codex"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHON_EXE="
set "PYTHON_ARGS="

for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)

if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('where py 2^>nul') do (
        if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
    )
    if defined PYTHON_EXE set "PYTHON_ARGS=-3"
)

echo ============================================
echo  Codex Session Recovery / Provider Sync
echo ============================================
echo.

if not exist "%CLI%" (
    echo [ERROR] Missing Python launcher:
    echo   "%CLI%"
    echo.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

if not exist "%DELETE_HELPER%" (
    echo [ERROR] Missing conversation deletion helper:
    echo   "%DELETE_HELPER%"
    set "SYNC_EXIT_CODE=1"
    goto finish
)

if not exist "%CLOSE_CODEX%" (
    echo [ERROR] Missing Codex Desktop close helper:
    echo   "%CLOSE_CODEX%"
    echo.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

if not defined PYTHON_EXE (
    echo [ERROR] Missing Python.
    echo This launcher checks system PATH for python or py.
    echo.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

echo [INFO] Codex home: "%SYNC_CODEX_HOME%"
echo [INFO] Sync covers both state databases and the local desktop catalog.
echo [INFO] Closing Codex Desktop before local data maintenance...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CLOSE_CODEX%"
if errorlevel 1 (
    echo.
    echo [ERROR] Codex Desktop could not be closed. Operation stopped.
    echo.
    set "SYNC_EXIT_CODE=1"
    goto finish
)
echo.

"%PYTHON_EXE%" %PYTHON_ARGS% "%CLI%" interactive "%SYNC_CODEX_HOME%"
if errorlevel 1 (
    echo.
    echo [ERROR] Operation failed. Check the message above.
    echo.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

goto finish

:finish
echo.
powershell.exe -NoLogo -NoProfile -Command "$answer = Read-Host '是否打开 ChatGPT？N：不打开，其余键或直接回车：打开'; if ($answer -ine 'N') { try { Start-Process -FilePath 'explorer.exe' -ArgumentList 'shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App' -WindowStyle Hidden -ErrorAction Stop } catch { Write-Host ('[ERROR] 无法打开 ChatGPT：' + $_.Exception.Message); exit 1 } }"
if errorlevel 1 set "SYNC_EXIT_CODE=1"
exit /b %SYNC_EXIT_CODE%
