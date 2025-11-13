#!/bin/bash
# Docker build script for Project JARVIS

set -e  # Exit on error

echo "Building JARVIS Docker Image..."
echo "=================================="
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
    cp jarvis/config.env.template jarvis/.env
    echo "Created jarvis/.env - Please edit it with your settings"
fi

# Build the image
echo "🔨 Building Docker image..."
echo ""
docker build -t jarvis-ai:latest .

# Check build status
if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Build successful!"
    echo ""
    echo "📦 Image info:"
    docker images jarvis-ai --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
    echo ""
    echo "🚀 Quick start commands:"
    echo ""
    echo "  Using helper script:"
    echo "    ./docker-run.sh text"
    echo ""
    echo "  Using docker-compose:"
    echo "    docker-compose up"
    echo ""
    echo "📖 See DOCKER.md for more information"
else
    echo "Build failed!"
    exit 1
fi

