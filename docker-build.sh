#!/bin/bash
# Docker build script for Project JARVIS

set -e

echo "Building JARVIS Docker Image..."
echo "=================================="

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed"
    echo "Please install Docker from https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if models directory exists
if [ ! -d "models" ]; then
    echo "Warning: models/ directory not found"
    echo "Models are required for voice features (Vosk, Piper)"
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

# Build the image
echo "Building Docker image..."
docker build -t jarvis-ai:latest .

if [ $? -eq 0 ]; then
    echo ""
    echo "Build successful!"
    echo ""
    echo "Image info:"
    docker images jarvis-ai --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
    echo ""
    echo "Quick start:"
    echo "  ./docker-run.sh text    # Text mode (recommended first test)"
    echo "  ./docker-run.sh         # Dual input (voice + socket)"
    echo ""
    echo "See DOCKER.md for more information"
else
    echo "Build failed!"
    exit 1
fi
