#!/bin/bash
# Metascan launcher script - suppresses harmless VAAPI warnings on WSL2/Linux
source venv/bin/activate

export QT_LOGGING_RULES="qt.multimedia.ffmpeg.libsymbolsresolver.debug=false"
python main.py "$@"
