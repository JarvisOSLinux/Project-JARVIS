# JARVIS Docker Image
#
# Build: docker build -t jarvis-ai .
# Run:   docker run -it --rm --network host jarvis-ai
#
# See DOCKER.md for detailed setup guide

FROM python:3.12-slim

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

COPY pyproject.toml README.md LICENSE ./
COPY jarvis/ ./jarvis/
COPY tests/ ./tests/
COPY models/ ./models/
COPY pytest.ini ./

RUN pip install --no-cache-dir -e ".[voice]"

RUN cp jarvis/.env.example jarvis/.env || true

RUN useradd -m -u 1000 jarvisuser && \
    chown -R jarvisuser:jarvisuser /app

USER jarvisuser

CMD ["python", "-m", "jarvis.main"]
