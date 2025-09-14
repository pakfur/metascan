# Single stage build for metascan
FROM python:3.11-slim

# Install runtime and build dependencies
RUN apt-get update && apt-get install -y \
    # Build essentials
    build-essential \
    gcc \
    g++ \
    cmake \
    git \
    wget \
    curl \
    # OpenGL and graphics
    libgl1 \
    libegl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libgtk-3-0 \
    # X11 libraries for GUI
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcb-cursor0 \
    libxcb-cursor-dev \
    libxcb-util1 \
    libxcb-render0 \
    libxcb-shape0 \
    libxcb-xfixes0 \
    libxcb-keysyms1 \
    libxcb-image0 \
    libxcb-icccm4 \
    libxcb-sync1 \
    libxcb-xinerama0 \
    libxcb-randr0 \
    libfontconfig1 \
    libfreetype6 \
    libxkbcommon0 \
    libxkbcommon-x11-0 \
    # FFmpeg for video processing
    ffmpeg \
    # VNC and web interface components
    x11vnc \
    xvfb \
    fluxbox \
    novnc \
    websockify \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -u 1000 metascan && \
    mkdir -p /app /data /models && \
    chown -R metascan:metascan /app /data /models

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt requirements-dev.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt || \
    (echo "Some packages failed to install, continuing..." && \
     pip install --no-cache-dir \
        PyQt6 \
        qt-material \
        Pillow \
        piexif \
        ffmpeg-python \
        nltk \
        joblib \
        watchdog \
        dataclasses-json \
        marshmallow \
        typing-inspect \
        typing_extensions) && \
    pip install --no-cache-dir -r requirements-dev.txt || true

# Copy application code
COPY --chown=metascan:metascan . /app/

# Setup NLTK data as metascan user
USER metascan
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords'); nltk.download('wordnet')" || \
    echo "NLTK data download failed, will retry on first run"

# Download models and setup (allow failure for initial setup)
RUN python setup_models.py || echo "Model setup will complete on first run"

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV MODELS_DIR=/models
ENV DATA_DIR=/data
ENV QT_QPA_PLATFORM=vnc
ENV DISPLAY=:1

# Create VNC startup script as root
USER root
RUN echo '#!/bin/bash\n\
# Start noVNC web server\n\
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &\n\
\n\
# Wait for websockify to start\n\
sleep 2\n\
\n\
# Start metascan with Qt VNC platform (will bind to port 5900)\n\
cd /app && python main.py\n\
' > /start-vnc.sh && chmod +x /start-vnc.sh

# Switch back to metascan user for volume ownership
USER metascan

# Create volume mount points
VOLUME ["/data", "/models"]

# Expose ports for VNC and web interface
EXPOSE 8080 5900 6080

# Default command
CMD ["/start-vnc.sh"]