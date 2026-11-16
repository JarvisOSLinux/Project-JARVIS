# JARVIS Docker Image
#
# Build: docker build -t jarvis-ai .
# Run:   docker run -it --rm --network host jarvis-ai
#
# See DOCKER.md for detailed setup guide

# llama-server for local (llamacpp) providers: llama.cpp's own CPU build,
# pinned by digest, with its shared libraries and CPU backends.
FROM ghcr.io/ggml-org/llama.cpp:server-b11913@sha256:8ee4ec7ce58a921313d07390c6699af13aa5ec083f3cdf67e5f4155767047373 AS llama

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
    libgomp1 \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Its RUNPATH is the build dir plus the working dir, so it only starts from
# /app in the upstream image; the wrapper points it at its own libraries.
COPY --from=llama /app/ /opt/llama.cpp/
RUN printf '#!/bin/sh\nLD_LIBRARY_PATH=/opt/llama.cpp exec /opt/llama.cpp/llama-server "$@"\n' \
    > /usr/local/bin/llama-server && chmod 755 /usr/local/bin/llama-server

COPY pyproject.toml README.md LICENSE ./
COPY jarvis/ ./jarvis/
COPY tests/ ./tests/
COPY models/ ./models/
COPY pytest.ini ./

# .dockerignore drops .git, so setuptools-scm cannot read the version itself.
ARG JARVIS_VERSION=0.0.0
RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PROJECT_JARVIS="$JARVIS_VERSION" \
    pip install --no-cache-dir -e ".[voice]"

RUN cp jarvis/.env.example jarvis/.env || true

RUN useradd -m -u 1000 jarvisuser && \
    chown -R jarvisuser:jarvisuser /app

USER jarvisuser

CMD ["python", "-m", "jarvis.main"]
