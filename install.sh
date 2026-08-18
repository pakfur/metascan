#!/usr/bin/env bash
# Metascan Installation Script
#
# Creates a Python virtual environment, installs the backend and frontend
# dependencies, downloads NLTK / AI model assets, and verifies the result.
#
# Usage: ./install.sh [options]
#   --python PATH     Use a specific Python interpreter for the venv
#   --recreate-venv   Delete and rebuild venv/ even if it looks usable
#   --no-dev          Skip requirements-dev.txt, flake8 and the editable install
#   --skip-frontend   Skip `npm install` in frontend/
#   --skip-models     Skip setup_models.py (NLTK data + AI upscaling models)
#   -h, --help        Show this help

set -Eeuo pipefail

# Run from the repo root regardless of where the script was invoked from.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The one supported interpreter. Keep in sync with setup.py's
# python_requires=">=3.11,<3.12" and .github/workflows/python-package.yml.
# 3.13+ cannot work at all (the pinned Pillow 10.2.0 publishes no wheel past
# cp312 and its sdist fails to build); 3.12 is simply not a tested build.
PY_SERIES="3.11"
PY_MINOR=11

VENV_DIR="venv"
PYTHON_OVERRIDE=""
RECREATE_VENV=0
INSTALL_DEV=1
DO_FRONTEND=1
DO_MODELS=1
WARNINGS=()

trap 'echo; echo "✗ Installation failed (line $LINENO, exit $?). Nothing above this point is complete." >&2' ERR

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --python)        PYTHON_OVERRIDE="${2:-}"; shift 2 ;;
        --python=*)      PYTHON_OVERRIDE="${1#*=}"; shift ;;
        --recreate-venv) RECREATE_VENV=1; shift ;;
        --no-dev)        INSTALL_DEV=0; shift ;;
        --skip-frontend) DO_FRONTEND=0; shift ;;
        --skip-models)   DO_MODELS=0; shift ;;
        -h|--help)       usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

warn() { echo "⚠ $1"; WARNINGS+=("$1"); }

echo "=== Metascan Installation ==="
echo

# ---------------------------------------------------------------------------
# 1. Locate a supported Python interpreter
# ---------------------------------------------------------------------------
# The old script only enforced a >= 3.11 lower bound, so on a host whose
# `python3` is newer it happily built a venv that could not install the pinned
# dependencies.
py_version() { "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null; }

py_supported() {
    command -v "$1" >/dev/null 2>&1 || return 1
    "$1" -c "import sys; v=sys.version_info; sys.exit(0 if v[0]==3 and v[1]==${PY_MINOR} else 1)" 2>/dev/null
}

PYTHON=""
if [ -n "$PYTHON_OVERRIDE" ]; then
    if ! command -v "$PYTHON_OVERRIDE" >/dev/null 2>&1; then
        echo "Error: --python '$PYTHON_OVERRIDE' not found" >&2
        exit 1
    fi
    if ! py_supported "$PYTHON_OVERRIDE"; then
        echo "Error: --python '$PYTHON_OVERRIDE' is $(py_version "$PYTHON_OVERRIDE"); Metascan requires Python ${PY_SERIES}.x." >&2
        exit 1
    fi
    PYTHON="$PYTHON_OVERRIDE"
else
    for candidate in "python${PY_SERIES}" python3 python; do
        if py_supported "$candidate"; then PYTHON="$candidate"; break; fi
    done
fi

if [ -z "$PYTHON" ]; then
    echo "Error: no supported Python found. Metascan requires Python ${PY_SERIES}.x." >&2
    echo "Interpreters detected on PATH:" >&2
    for candidate in python3 python3.11 python3.12 python3.13 python3.14; do  # probe list, not a support list
        if command -v "$candidate" >/dev/null 2>&1; then
            echo "  - $candidate ($(py_version "$candidate"))" >&2
        fi
    done
    echo >&2
    echo "Install a supported interpreter, e.g.:" >&2
    echo "  macOS:  brew install python@${PY_SERIES}" >&2
    echo "  Ubuntu: sudo apt install python${PY_SERIES} python${PY_SERIES}-venv" >&2
    echo "then re-run:  ./install.sh --python python${PY_SERIES}" >&2
    exit 1
fi

echo "✓ Python interpreter: $PYTHON ($(py_version "$PYTHON"))"

# ---------------------------------------------------------------------------
# 2. Virtual environment
# ---------------------------------------------------------------------------
# The old script reused any existing venv/ unconditionally, so a directory left
# behind by an unsupported interpreter was silently reused and every install
# into it failed.
VENV_PY="$VENV_DIR/bin/python"
[ -d "$VENV_DIR" ] && [ ! -x "$VENV_PY" ] && [ -x "$VENV_DIR/Scripts/python.exe" ] && VENV_PY="$VENV_DIR/Scripts/python.exe"

if [ -d "$VENV_DIR" ] && [ "$RECREATE_VENV" -eq 1 ]; then
    echo "Removing existing virtual environment (--recreate-venv)..."
    rm -rf "$VENV_DIR"
elif [ -d "$VENV_DIR" ]; then
    if [ ! -x "$VENV_PY" ]; then
        echo "Existing $VENV_DIR/ has no usable interpreter — rebuilding it."
        rm -rf "$VENV_DIR"
    elif ! py_supported "$VENV_PY"; then
        echo "Existing $VENV_DIR/ uses Python $(py_version "$VENV_PY"), not the supported ${PY_SERIES}.x — rebuilding it."
        rm -rf "$VENV_DIR"
    fi
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment ($VENV_DIR)..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$VENV_DIR/Scripts/python.exe"
if [ ! -x "$VENV_PY" ]; then
    echo "Error: virtual environment created but $VENV_PY is missing." >&2
    echo "On Debian/Ubuntu this usually means the python3-venv package is not installed." >&2
    exit 1
fi
echo "✓ Virtual environment ready: $(py_version "$VENV_PY")"

# Call the venv interpreter directly instead of `source venv/bin/activate` —
# activation is unnecessary here and interacts badly with `set -u`.
PIP="$VENV_PY -m pip"

# ---------------------------------------------------------------------------
# 3. Python dependencies
# ---------------------------------------------------------------------------
echo
echo "Upgrading pip, setuptools and wheel..."
# setuptools/wheel matter: several pinned deps (basicsr, realesrgan) still ship
# sdists that need a working legacy build backend.
$PIP install --upgrade pip setuptools wheel

echo
echo "Installing runtime dependencies (requirements.txt)..."
$PIP install -r requirements.txt

if [ "$INSTALL_DEV" -eq 1 ]; then
    echo
    echo "Installing development dependencies (requirements-dev.txt)..."
    $PIP install -r requirements-dev.txt
    # flake8 is used by `make quality` and CI but is not in either file.
    $PIP install flake8
    echo "Installing metascan in editable mode..."
    $PIP install -e .
fi

# ---------------------------------------------------------------------------
# 4. Directories
# ---------------------------------------------------------------------------
echo
echo "Creating directories..."
# Thumbnails live under the project data dir in development mode
# (metascan/utils/app_paths.py::get_thumbnail_cache_dir), not ~/.metascan.
mkdir -p data/thumbnails

# ---------------------------------------------------------------------------
# 5. NLTK data + AI models (network, optional)
# ---------------------------------------------------------------------------
if [ "$DO_MODELS" -eq 1 ]; then
    echo
    echo "Setting up NLTK data and AI upscaling models..."
    if ! "$VENV_PY" setup_models.py; then
        warn "setup_models.py failed — NLTK data and/or AI models are missing. Re-run '$VENV_PY setup_models.py' once network access is available."
    fi
else
    echo
    echo "Skipping setup_models.py (--skip-models)."
fi

# ---------------------------------------------------------------------------
# 6. Frontend
# ---------------------------------------------------------------------------
# The Vue 3 SPA is the only UI; a backend-only install cannot serve anything.
if [ "$DO_FRONTEND" -eq 1 ]; then
    echo
    if ! command -v npm >/dev/null 2>&1; then
        warn "npm not found — frontend dependencies were not installed. Install Node.js 20+, then run 'cd frontend && npm install'."
    else
        echo "Installing frontend dependencies (npm $(npm --version))..."
        if ! (cd frontend && npm install); then
            warn "npm install failed — run 'cd frontend && npm install' manually."
        fi
    fi
else
    echo
    echo "Skipping frontend install (--skip-frontend)."
fi

# ---------------------------------------------------------------------------
# 7. Optional system tools
# ---------------------------------------------------------------------------
echo
echo "Checking optional system dependencies..."
for tool_spec in "ffmpeg:video thumbnail generation" "exiftool:video metadata extraction"; do
    tool="${tool_spec%%:*}"; purpose="${tool_spec#*:}"
    if command -v "$tool" >/dev/null 2>&1; then
        echo "  ✓ $tool ($purpose)"
    else
        warn "$tool not found — $purpose will be unavailable."
    fi
done

# ---------------------------------------------------------------------------
# 8. Verify
# ---------------------------------------------------------------------------
# The old script printed "Installation Complete" and exited 0 even when every
# single step had failed. Prove the environment actually imports before
# claiming success.
echo
echo "Verifying installation..."
if ! "$VENV_PY" - <<'PYCHECK'
import importlib
import sys

REQUIRED = [
    "fastapi", "uvicorn", "websockets", "PIL", "piexif", "pillow_heif",
    "ffmpeg", "cv2", "torch", "torchvision", "nltk", "watchdog", "yaml",
    "portalocker", "send2trash", "psutil", "httpx", "orjson",
    "open_clip", "faiss", "imagehash",
]

missing = []
for name in REQUIRED:
    try:
        importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - report anything that blocks import
        missing.append(f"{name}: {exc}")

if missing:
    print("Import check failed for:", file=sys.stderr)
    for line in missing:
        print(f"  - {line}", file=sys.stderr)
    sys.exit(1)

print(f"  ✓ {len(REQUIRED)} core packages import cleanly")
PYCHECK
then
    echo >&2
    echo "✗ The virtual environment is incomplete — see the import errors above." >&2
    exit 1
fi

# Surface version conflicts pip's sequential -r passes can leave behind (e.g.
# requirements-dev.txt pinning a package that a runtime dep constrains higher).
# A warning, not a failure: the env still imports.
if ! $PIP check >/tmp/metascan_pip_check.$$ 2>&1; then
    warn "pip reports dependency conflicts: $(tr '\n' '; ' </tmp/metascan_pip_check.$$)"
fi
rm -f /tmp/metascan_pip_check.$$

# ---------------------------------------------------------------------------
# 9. Summary
# ---------------------------------------------------------------------------
echo
if [ "${#WARNINGS[@]}" -gt 0 ]; then
    echo "=== Installation Complete (with ${#WARNINGS[@]} warning(s)) ==="
    for w in "${WARNINGS[@]}"; do echo "  ⚠ $w"; done
else
    echo "=== Installation Complete ==="
fi
echo
echo "To run Metascan (two terminals):"
echo "  1. Backend:  source $VENV_DIR/bin/activate && python run_server.py   # http://localhost:8700"
echo "  2. Frontend: cd frontend && npm run dev                        # http://localhost:5173"
echo
if [ "$INSTALL_DEV" -eq 1 ]; then
    echo "To verify the checkout:"
    echo "  source $VENV_DIR/bin/activate && make quality test"
    echo
fi
