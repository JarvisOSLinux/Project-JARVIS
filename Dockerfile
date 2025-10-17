# JARVIS Docker Image with Configurable PyTorch Backend
# 
# Build arguments:
#   TORCH_VARIANT: cpu (default), cuda, or rocm
#   TORCH_VERSION: PyTorch version (default: 2.8.0)
#
# Usage examples:
#   docker build -t jarvis-ai:cpu .
#   docker build --build-arg TORCH_VARIANT=cuda -t jarvis-ai:cuda .
#   docker build --build-arg TORCH_VARIANT=rocm -t jarvis-ai:rocm .
#
# See DOCKER.md for detailed setup guide

FROM python:3.13-slim

# Build arguments for PyTorch variant (cuda, rocm, cpu)
ARG TORCH_VARIANT=cpu
ARG TORCH_VERSION=2.8.0

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

RUN if [ "$TORCH_VARIANT" = "cuda" ]; then \
        pip install --no-cache-dir torch==${TORCH_VERSION} --index-url https://download.pytorch.org/whl/cu124; \
    elif [ "$TORCH_VARIANT" = "rocm" ]; then \
        pip install --no-cache-dir torch==${TORCH_VERSION} --index-url https://download.pytorch.org/whl/rocm6.2; \
    else \
        pip install --no-cache-dir torch==${TORCH_VERSION} --index-url https://download.pytorch.org/whl/cpu; \
    fi

RUN grep -v "^torch==" requirements.txt > requirements_no_torch.txt && \
    pip install --no-cache-dir -r requirements_no_torch.txt && \
    rm requirements_no_torch.txt

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

