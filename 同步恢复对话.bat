@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal

set "CLI=%~dp0codex_provider_local_launcher.py"
set "CODEX_HOME=%USERPROFILE%\.codex"
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
    pause
    exit /b 1
)

if not defined PYTHON_EXE (
    echo [ERROR] Missing Python.
    echo This launcher checks system PATH for python or py.
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" %PYTHON_ARGS% "%CLI%" interactive "%CODEX_HOME%"
if errorlevel 1 (
    echo.
    echo [ERROR] Operation failed. Check the message above.
    echo.
    pause
    exit /b 1
)

echo.
pause
