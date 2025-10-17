# JARVIS Docker Image
# 
# Usage examples:
#   docker build -t jarvis-ai .
#
# See DOCKER.md for detailed setup guide

FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OLLAMA_HOST=http://host.docker.internal:11434

RUN apt-get update && apt-get install -y --no-install-recommends \
    portaudio19-dev \
    libsndfile1 \
    alsa-utils \
    libasound2 \
    libasound2-plugins \
    pulseaudio-utils \
    ffmpeg \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./

RUN pip install --no-cache-dir -r requirements.txt

COPY jarvis/ ./jarvis/
COPY examples/ ./examples/
COPY tests/ ./tests/

COPY models/ ./models/

COPY pytest.ini run_tests.py README.md LICENSE ./

RUN cp jarvis/config.env.template jarvis/.env || true

RUN useradd -m -u 1000 jarvisuser && \
    chown -R jarvisuser:jarvisuser /app

USER jarvisuser

CMD ["python", "-m", "jarvis.main"]

