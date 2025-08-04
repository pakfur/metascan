#!/usr/bin/env fish

# Activate the virtual environment for the metascan project
# Usage: source activate_venv.fish

if test -f venv/bin/activate.fish
    source venv/bin/activate.fish
    echo "✅ Virtual environment activated (Python 3.11)"
    echo "📦 Available packages: PyQt6, Pillow, watchdog, dataclasses-json, pytest, black, mypy"
    echo "⚠️  Note: plyvel has runtime linking issues - LevelDB functionality may not work"
    echo ""
    echo "To deactivate: run 'deactivate'"
else
    echo "❌ Virtual environment not found. Run 'python3.11 -m venv venv' first."
end
