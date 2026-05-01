@echo off
REM Metascan Windows frontend setup.
REM Installs npm packages and builds frontend\dist\ so the FastAPI backend
REM (run.bat) can serve the Vue UI at http://localhost:8700.
REM
REM Re-run this script after pulling frontend changes or updating package.json.

setlocal
cd /d "%~dp0"

REM ---- 1. Verify Node.js + npm are available ----
where node >nul 2>nul
if errorlevel 1 (
    echo [error] node not found on PATH.
    echo         Install Node.js LTS from https://nodejs.org/ and reopen this terminal.
    exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
    echo [error] npm not found on PATH.
    echo         Install Node.js LTS from https://nodejs.org/ and reopen this terminal.
    exit /b 1
)

REM ---- 2. Install dependencies ----
echo [setup] Installing frontend npm packages (first run can take a few minutes) ...
pushd frontend
call npm install
if errorlevel 1 (
    echo [error] npm install failed
    popd
    exit /b 1
)

REM ---- 3. Build production bundle into frontend\dist\ ----
echo [build] Building Vue production bundle ...
call npm run build
if errorlevel 1 (
    echo [error] npm run build failed
    popd
    exit /b 1
)
popd

echo.
echo [done] Frontend built to frontend\dist\.
echo        Run run.bat to start the backend; the UI will be served at http://localhost:8700
