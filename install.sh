#!/bin/bash
# Metascan Installation Script

echo "=== Metascan Installation ==="
echo

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
required_version="3.11"

if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
    echo "Error: Python 3.11 or higher is required. Found: $python_version"
    exit 1
fi

echo "✓ Python version: $python_version"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Setup NLTK data
echo "Setting up NLTK data..."
python setup_nltk.py

# Create necessary directories
echo "Creating directories..."
mkdir -p data
mkdir -p ~/.metascan/thumbnails

echo
echo "=== Installation Complete ==="
echo
echo "To run Metascan:"
echo "  1. Activate the virtual environment: source venv/bin/activate"
echo "  2. Run the application: python main.py"
echo
echo "Optional: Install system dependencies for enhanced functionality:"
echo "  - FFmpeg for video thumbnail generation"
echo "  - exiftool for video metadata extraction"