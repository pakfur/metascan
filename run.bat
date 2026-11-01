@echo off
REM Metascan on Windows: rebuilds the UI when it is out of date, then starts
REM the backend, which serves it at http://localhost:8700.
REM
REM First-time setup is in docs\first_time_setup.md.
REM
REM METASCAN_HOST / METASCAN_PORT   bind address (default 0.0.0.0:8700)
REM METASCAN_SKIP_FRONTEND=1        start without checking the UI
REM
REM Uvicorn runs directly, not through run_server.py: its auto-reload picks an
REM event loop that cannot start subprocesses on Windows, which breaks CLIP
REM search, the taste scorer and writer training.

setlocal
cd /d "%~dp0"
set "PY=venv\Scripts\python.exe"

if not exist "%PY%" goto :no_venv
"%PY%" -c "import sys; sys.exit(sys.version_info[:2] != (3, 11))"
if errorlevel 1 goto :wrong_python

if not defined METASCAN_SKIP_FRONTEND call :frontend
if errorlevel 1 exit /b 1

if not defined METASCAN_PORT set "METASCAN_PORT=8700"
if not defined METASCAN_HOST set "METASCAN_HOST=0.0.0.0"
echo.
echo [run] Starting Metascan at http://localhost:%METASCAN_PORT%  (Ctrl+C to stop)
echo.
"%PY%" -m uvicorn backend.main:app --host %METASCAN_HOST% --port %METASCAN_PORT%
if errorlevel 1 goto :server_failed
exit /b 0


:frontend
"%PY%" scripts\frontend_status.py
if errorlevel 2 goto :install
if errorlevel 1 goto :build
echo [ok] UI is up to date.
exit /b 0

:install
call :need_npm
if errorlevel 1 exit /b 1
echo [setup] Installing frontend packages ...
pushd frontend
call npm install
set "RESULT=%errorlevel%"
popd
if not "%RESULT%"=="0" goto :install_failed
"%PY%" scripts\frontend_status.py --mark-installed
"%PY%" scripts\frontend_status.py
if not errorlevel 1 exit /b 0

:build
call :need_npm
if errorlevel 1 exit /b 1
echo [build] The UI is out of date. Rebuilding ...
pushd frontend
call npm run build
set "RESULT=%errorlevel%"
popd
if not "%RESULT%"=="0" goto :build_failed
exit /b 0

:need_npm
where npm >nul 2>nul
if not errorlevel 1 exit /b 0
echo [error] The UI needs building, but npm is not on PATH.
echo         Install Node.js 20.19+ or 22.12+ and open a new terminal,
echo         or set METASCAN_SKIP_FRONTEND=1 to start without it.
exit /b 1

:install_failed
echo [error] npm install failed. See the output above.
exit /b 1

:build_failed
echo [error] The UI did not build, so it would be out of date. See the output above.
echo         Fix it, or set METASCAN_SKIP_FRONTEND=1 to start with the last build.
exit /b 1


:no_venv
echo [error] No virtual environment at venv\.
echo         Create it as docs\first_time_setup.md describes: on an NVIDIA GPU,
echo         torch must be installed first or you get a CPU-only build.
exit /b 1

:wrong_python
echo [error] venv\ is not Python 3.11, the only supported version.
echo         Delete it and recreate it as docs\first_time_setup.md describes.
exit /b 1

:server_failed
echo.
echo [error] The server stopped with an error. See the output above.
echo         WinError 10013 or 10048 means port %METASCAN_PORT% is blocked, often by
echo         Metascan in WSL. Stop it, or set METASCAN_PORT to another port.
exit /b 1
