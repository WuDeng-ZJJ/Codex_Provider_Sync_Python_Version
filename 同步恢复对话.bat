@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal EnableExtensions DisableDelayedExpansion
set "SYNC_EXIT_CODE=0"

set "CLI=%~dp0codex_provider_local_launcher.py"
set "DELETE_HELPER=%~dp0codex_delete_conversation.py"
set "CLOSE_CODEX=%~dp0close_codex_desktop.ps1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHON_EXE="
set "PYTHON_ARGS="
set "CODEX_HOME_SOURCE="
set "SYNC_CODEX_HOME="
set "DEFAULT_CODEX_HOME="

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
    echo.
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

if defined USERPROFILE set "DEFAULT_CODEX_HOME=%USERPROFILE%\.codex"
if not defined DEFAULT_CODEX_HOME if defined HOMEDRIVE if defined HOMEPATH set "DEFAULT_CODEX_HOME=%HOMEDRIVE%%HOMEPATH%\.codex"
if defined DEFAULT_CODEX_HOME for %%I in ("%DEFAULT_CODEX_HOME%") do set "DEFAULT_CODEX_HOME=%%~fI"

if defined CODEX_HOME goto use_configured_home
set "SYNC_CODEX_HOME=%DEFAULT_CODEX_HOME%"
set "CODEX_HOME_SOURCE=USERPROFILE default"
goto normalize_home

:use_configured_home
set "SYNC_CODEX_HOME=%CODEX_HOME%"
set "CODEX_HOME_SOURCE=CODEX_HOME environment variable"

:normalize_home
set "SYNC_CODEX_HOME=%SYNC_CODEX_HOME:"=%"
if defined SYNC_CODEX_HOME for %%I in ("%SYNC_CODEX_HOME%") do set "SYNC_CODEX_HOME=%%~fI"
call :is_codex_home "%SYNC_CODEX_HOME%"
if defined CODEX_HOME_VALID goto home_ready

if /I not "%CODEX_HOME_SOURCE%"=="CODEX_HOME environment variable" goto missing_codex_home
call :is_codex_home "%DEFAULT_CODEX_HOME%"
if not defined CODEX_HOME_VALID goto invalid_configured_home
echo [WARN] CODEX_HOME does not point to a recognizable Codex data directory:
echo   "%SYNC_CODEX_HOME%"
echo [WARN] Falling back to the current user's default Codex directory:
echo   "%DEFAULT_CODEX_HOME%"
echo.
set "SYNC_CODEX_HOME=%DEFAULT_CODEX_HOME%"
set "CODEX_HOME_SOURCE=USERPROFILE default (invalid CODEX_HOME ignored)"
goto home_ready

:invalid_configured_home
echo [ERROR] CODEX_HOME does not point to a recognizable Codex data directory:
echo   "%SYNC_CODEX_HOME%"
if defined DEFAULT_CODEX_HOME echo [INFO] The default directory was also not usable: "%DEFAULT_CODEX_HOME%"
echo [INFO] Fix or remove the CODEX_HOME environment variable, then run Codex Desktop once.
echo.
set "SYNC_EXIT_CODE=1"
goto finish

:missing_codex_home
echo [ERROR] The Codex data directory could not be found.
if defined SYNC_CODEX_HOME echo [INFO] Checked: "%SYNC_CODEX_HOME%"
echo [INFO] Run Codex Desktop once, or set CODEX_HOME to the directory containing its local data.
echo.
set "SYNC_EXIT_CODE=1"
goto finish

:home_ready
for /f "delims=" %%P in ('where.exe py.exe 2^>nul') do if not defined PYTHON_EXE call :try_python "%%P" "-3"
for /f "delims=" %%P in ('where.exe python.exe 2^>nul') do if not defined PYTHON_EXE call :try_python "%%P" ""
for /f "delims=" %%P in ('where.exe python3.exe 2^>nul') do if not defined PYTHON_EXE call :try_python "%%P" ""

if not defined PYTHON_EXE (
    echo.
    echo [ERROR] No usable Python 3.11 or newer installation was found.
    echo [INFO] Windows Store aliases and older Python versions are not sufficient.
    echo [INFO] Install Python from https://www.python.org/downloads/windows/ and run this file again.
    echo.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

echo.
echo [INFO] Codex home source: %CODEX_HOME_SOURCE%
echo [INFO] Codex home: "%SYNC_CODEX_HOME%"
echo [INFO] Python command: "%PYTHON_EXE%" %PYTHON_ARGS%
"%PYTHON_EXE%" %PYTHON_ARGS% -X utf8 -c "import sys; print('[INFO] Selected Python: '+sys.version.split()[0]+' | '+sys.executable)"
if errorlevel 1 (
    echo [ERROR] The selected Python interpreter stopped working during preflight.
    echo.
    set "SYNC_EXIT_CODE=1"
    goto finish
)

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

echo [INFO] Starting the interactive maintenance program...
"%PYTHON_EXE%" %PYTHON_ARGS% -X utf8 -u "%CLI%" interactive "%SYNC_CODEX_HOME%"
set "PYTHON_EXIT_CODE=%ERRORLEVEL%"
if "%PYTHON_EXIT_CODE%"=="0" goto finish

echo.
echo [ERROR] Python command exited with code %PYTHON_EXIT_CODE%.
echo [ERROR] Operation failed. The detailed Python error is shown above.
echo.
set "SYNC_EXIT_CODE=%PYTHON_EXIT_CODE%"
goto finish

:finish
echo.
powershell.exe -NoLogo -NoProfile -Command "$answer = Read-Host '是否打开 ChatGPT？N：不打开，其余键或直接回车：打开'; if ($answer -ine 'N') { try { Start-Process -FilePath 'explorer.exe' -ArgumentList 'shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App' -WindowStyle Hidden -ErrorAction Stop } catch { Write-Host ('[ERROR] 无法打开 ChatGPT：' + $_.Exception.Message); exit 1 } }"
if errorlevel 1 set "SYNC_EXIT_CODE=1"
if "%SYNC_EXIT_CODE%"=="0" exit /b 0
echo.
echo Press any key to close this window...
pause >nul
exit /b %SYNC_EXIT_CODE%

:try_python
if defined PYTHON_EXE exit /b 0
echo [INFO] Checking Python candidate: "%~1" %~2
"%~1" %~2 -X utf8 -c "import pathlib,sqlite3,sys; print('[INFO] Candidate: '+sys.version.split()[0]+' | '+sys.executable); sys.exit(3) if sys.version_info < (3,11) else None; import tomllib; [compile(pathlib.Path(p).read_bytes(),p,'exec') for p in sys.argv[1:]]" "%CLI%" "%DELETE_HELPER%"
set "PYTHON_PROBE_EXIT=%ERRORLEVEL%"
if "%PYTHON_PROBE_EXIT%"=="0" goto accept_python
echo [WARN] Rejected Python candidate; exit code %PYTHON_PROBE_EXIT%.
exit /b 0

:accept_python
set "PYTHON_EXE=%~1"
set "PYTHON_ARGS=%~2"
exit /b 0

:is_codex_home
set "CODEX_HOME_VALID="
if "%~1"=="" exit /b 0
if not exist "%~1\." exit /b 0
if exist "%~1\config.toml" set "CODEX_HOME_VALID=1"
if exist "%~1\sessions\." set "CODEX_HOME_VALID=1"
if exist "%~1\archived_sessions\." set "CODEX_HOME_VALID=1"
if exist "%~1\sqlite\." set "CODEX_HOME_VALID=1"
if exist "%~1\state_5.sqlite" set "CODEX_HOME_VALID=1"
if exist "%~1\.codex-global-state.json" set "CODEX_HOME_VALID=1"
exit /b 0
