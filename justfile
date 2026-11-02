# Build, test and run Metascan: `just` lists the recipes.
#
# First-time setup is not here: on an NVIDIA GPU torch must be installed first
# or you get a CPU-only build. See docs/first_time_setup.md.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

py := if os_family() == "windows" { "venv/Scripts/python" } else { "venv/bin/python" }
dirs := "metascan/ backend/ tests/"
host := env_var_or_default("METASCAN_HOST", "0.0.0.0")
port := env_var_or_default("METASCAN_PORT", "8700")

# List the recipes
[private]
default:
    @just --list --unsorted

# Fail unless the venv exists and is Python 3.11, the only supported version
[private]
venv:
    @{{ py }} -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'

# ---- Run ---------------------------------------------------------------------

# Run Metascan at http://localhost:8700, rebuilding the UI when it is stale
[windows]
run:
    .\run.bat

# Run Metascan at http://localhost:8700
[unix]
run: build backend

# Run the backend only. run.bat explains why Windows must not use run_server.py
[windows]
backend: venv
    {{ py }} -m uvicorn backend.main:app --host {{ host }} --port {{ port }}

# Run the backend only
[unix]
backend: venv
    {{ py }} run_server.py

# Run the frontend dev server at http://localhost:5173, beside `just backend`
frontend:
    npm --prefix frontend run dev

# ---- Build -------------------------------------------------------------------

# Type-check and build the UI
build:
    npm --prefix frontend run build

# Install or update the Python packages and the frontend's
deps: venv
    {{ py }} -m pip install -r requirements.txt -r requirements-dev.txt flake8
    npm --prefix frontend install

# ---- Test --------------------------------------------------------------------

# Run the tests in parallel: `just test tests/test_scorecard.py -k brief`
test *args: venv
    {{ py }} -m pytest -n auto {{ args }}

# Run the tests one at a time, for a debugger or `-s`
test-serial *args: venv
    {{ py }} -m pytest {{ args }}

# ---- Quality -----------------------------------------------------------------

# Lint with flake8, as CI does
lint: venv
    {{ py }} -m flake8 {{ dirs }} --count --select=E9,F63,F7,F82 --show-source --statistics
    {{ py }} -m flake8 {{ dirs }} --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

# Format the code with black
format: venv
    {{ py }} -m black {{ dirs }}

# Check the formatting without changing anything, as CI does
format-check: venv
    {{ py }} -m black --check {{ dirs }}

# Type-check with mypy, as CI does
typecheck: venv
    {{ py }} -m mypy --check-untyped-defs metascan/

# Run flake8, black and mypy
quality: lint format-check typecheck

# Run everything CI runs: run this before committing
check: quality build test
