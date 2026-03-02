#!/bin/bash
# Docker build script for Project JARVIS

set -e  # Exit on error

# Parse arguments
TORCH_VARIANT="${1:-cpu}"  # Default to cpu if not specified
TORCH_VERSION="${2:-2.8.0}"

# Validate variant
if [[ ! "$TORCH_VARIANT" =~ ^(cpu|cuda|rocm)$ ]]; then
    echo "Error: Invalid TORCH_VARIANT '$TORCH_VARIANT'"
    echo "Valid options: cpu, cuda, rocm"
    echo ""
    echo "Usage: $0 [cpu|cuda|rocm] [torch_version]"
    echo "Examples:"
    echo "  $0              # CPU-only (default)"
    echo "  $0 cuda         # NVIDIA CUDA support"
    echo "  $0 rocm         # AMD ROCm support"
    exit 1
fi

echo "Building JARVIS Docker Image..."
echo "=================================="
echo "PyTorch Variant: $TORCH_VARIANT"
echo "PyTorch Version: $TORCH_VERSION"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed"
    echo "Please install Docker from https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if SuperMCP submodule is initialized
if [ ! -f "jarvis/SuperMCP/SuperMCP.py" ]; then
    echo "Warning: SuperMCP submodule not initialized"
    echo "Initializing submodules..."
    git submodule update --init --recursive
    if [ $? -ne 0 ]; then
        echo "Failed to initialize submodules"
        echo "Please run: git submodule update --init --recursive"
        exit 1
    fi
    echo "Submodules initialized"
fi

# Check if models directory exists
if [ ! -d "models" ]; then
    echo "Warning: models/ directory not found"
    echo "Models are required for voice features"
    echo "Please download models before building (see README.md)"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if .env exists, if not create from template
if [ ! -f "jarvis/.env" ]; then
    echo "Creating .env from template..."
    cp jarvis/.env.example jarvis/.env
    echo "Created jarvis/.env - Please edit it with your settings"
fi

# Detect PyTorch variant
TORCH_VARIANT="${TORCH_VARIANT:-cpu}"
TORCH_VERSION="${TORCH_VERSION:-2.8.0}"

# Build the image
echo "🔨 Building Docker image..."
echo "   Variant: ${TORCH_VARIANT}"
echo "   PyTorch: ${TORCH_VERSION}"
echo ""
docker build \
    --build-arg TORCH_VARIANT=$TORCH_VARIANT \
    --build-arg TORCH_VERSION=$TORCH_VERSION \
    -t jarvis-ai:$TORCH_VARIANT \
    -t jarvis-ai:latest \
    .

# Check build status
if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Build successful!"
    echo ""
    echo "📦 Image info:"
    docker images jarvis-ai --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
    echo ""
    echo "🎯 Built variant: ${TORCH_VARIANT}"
    if [ "$TORCH_VARIANT" = "cuda" ]; then
        echo "   ⚡ GPU: NVIDIA CUDA support"
        echo "   📋 Requires: nvidia-docker2"
    elif [ "$TORCH_VARIANT" = "rocm" ]; then
        echo "   ⚡ GPU: AMD ROCm support"
        echo "   📋 Requires: ROCm Docker runtime"
    else
        echo "   🖥️  Hardware: CPU only"
    fi
    echo ""
    echo "🚀 Quick start commands:"
    echo ""
    echo "  Using helper script (auto-detects hardware):"
    echo "    TORCH_VARIANT=${TORCH_VARIANT} ./docker-run.sh text"
    echo ""
    echo "  Using docker-compose:"
    echo "    TORCH_VARIANT=${TORCH_VARIANT} docker-compose up"
    echo ""
    echo "📖 See DOCKER.md for more information"
else
    echo "Build failed!"
    exit 1
fi

