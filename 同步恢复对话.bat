@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

set "TOOL_DIR=%~dp0github"
set "CLI=%~dp0codex_provider_local_launcher.mjs"
set "CODEX_HOME=%USERPROFILE%\.codex"
set "NODE_NO_WARNINGS=1"
set "NODE_EXE="

for /d %%D in ("%LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node\*") do (
    if exist "%%~fD\bin\node.exe" set "NODE_EXE=%%~fD\bin\node.exe"
)

if not defined NODE_EXE (
    for /f "delims=" %%N in ('where node 2^>nul') do (
        if not defined NODE_EXE set "NODE_EXE=%%N"
    )
)

echo ============================================
echo  Codex Session Recovery / Provider Sync
echo ============================================
echo.

if not exist "%CLI%" (
    echo [ERROR] Missing local launcher:
    echo   "%CLI%"
    echo.
    pause
    exit /b 1
)

if not exist "%TOOL_DIR%\src\service.js" (
    echo [ERROR] Missing GitHub tool code:
    echo   "%TOOL_DIR%\src\service.js"
    echo.
    pause
    exit /b 1
)

if not defined NODE_EXE (
    echo [ERROR] Missing Node.js.
    echo This launcher checks Codex bundled Node first, then system PATH.
    echo.
    pause
    exit /b 1
)

"%NODE_EXE%" "%CLI%" interactive "%CODEX_HOME%"
if errorlevel 1 (
    echo.
    echo [ERROR] Operation failed. Check the message above.
    echo.
    pause
    exit /b 1
)

echo.
pause
