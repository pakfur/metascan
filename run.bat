@echo off
REM Metascan Windows backend launcher.
REM First run: creates venv-win\, installs requirements.txt.
REM Every run:  activates the venv and starts the FastAPI server.
REM
REM Override the bind host/port with METASCAN_HOST / METASCAN_PORT env vars.
REM Force a dependency reinstall by deleting venv-win\.deps-installed.

setlocal
cd /d "%~dp0"

REM ---- 1. Verify Python is available ----
where python >nul 2>nul
if errorlevel 1 (
    echo [error] python not found on PATH.
    echo         Install Python 3.11 or 3.12 from https://www.python.org/
    exit /b 1
)

REM ---- 2. Create venv-win on first run ----
if not exist "venv-win\Scripts\activate.bat" (
    echo [setup] Creating virtual environment in venv-win\ ...
    python -m venv venv-win
    if errorlevel 1 (
        echo [error] failed to create venv-win
        exit /b 1
    )
    REM Force a fresh dependency install whenever the venv is recreated.
    if exist "venv-win\.deps-installed" del /q "venv-win\.deps-installed"
)

REM ---- 3. Activate the venv ----
call "venv-win\Scripts\activate.bat"
if errorlevel 1 (
    echo [error] failed to activate venv-win
    exit /b 1
)

REM ---- 4. Install dependencies on first run (or after manual marker delete) ----
if not exist "venv-win\.deps-installed" (
    echo [setup] Upgrading pip ...
    python -m pip install --upgrade pip
    echo [setup] Installing Python dependencies (first run can take several minutes) ...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [error] pip install failed
        exit /b 1
    )
    > "venv-win\.deps-installed" echo installed
    echo [setup] Dependencies installed.
)

REM ---- 5. Launch the FastAPI backend ----
if "%METASCAN_PORT%"=="" set METASCAN_PORT=8700
if "%METASCAN_HOST%"=="" set METASCAN_HOST=0.0.0.0
echo.
echo [run] Starting Metascan backend at http://localhost:%METASCAN_PORT%
echo       (Ctrl+C to stop)
echo.
python run_server.py
