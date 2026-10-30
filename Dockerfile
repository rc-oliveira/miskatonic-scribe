# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install ffmpeg (required by openai-whisper to decode Telegram .ogg/Opus voice files)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install CPU-only PyTorch first to avoid downloading ~2.5GB of unused NVIDIA CUDA libs
# Works on both x86_64 (AMD Micro) and ARM64/aarch64 (Ampere A1) on Oracle Cloud Always Free
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu torch && \
    pip install -r requirements.txt

# Copy application code and maintenance scripts
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY main.py ./

# Prepare runtime directories
RUN mkdir -p /app/data /app/temp_audio /root/.cache/whisper

CMD ["python", "main.py"]
