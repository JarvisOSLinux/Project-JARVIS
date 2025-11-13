#!/bin/bash
# Docker run script for Project JARVIS
# This script detects your OS and runs JARVIS with appropriate settings

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}JARVIS Docker Launcher${NC}"
echo "=================================="

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed${NC}"
    echo "Please install Docker from https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if image exists
if ! docker image inspect jarvis-ai:latest &> /dev/null; then
    echo -e "${RED}Error: jarvis-ai:latest image not found${NC}"
    echo "Please build the image first:"
    echo "  ./docker-build.sh"
    echo "  OR"
    echo "  docker-compose build"
    exit 1
fi

# Check if Ollama is running
echo -n "Checking Ollama connection... "
if curl -s http://localhost:11434/api/version > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
    echo -e "${YELLOW}Warning: Cannot connect to Ollama at localhost:11434${NC}"
    echo "Please make sure Ollama is running:"
    echo "  ollama serve"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Detect OS
OS="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="mac"
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    OS="windows"
fi

echo "Detected OS: $OS"

# Parse mode argument
MODE="${1:-voice}"  # Default to voice mode

# Use latest image
IMAGE_NAME="jarvis-ai:latest"

# Build Docker command based on OS and mode
DOCKER_CMD="docker run -it --rm"

case "$OS" in
    linux)
        DOCKER_CMD="$DOCKER_CMD --network host"
        if [ "$MODE" != "text" ]; then
            # Add audio devices for voice mode
            if [ -d "/dev/snd" ]; then
                DOCKER_CMD="$DOCKER_CMD --device /dev/snd"
                echo -e "${GREEN}Audio devices found${NC}"
            else
                echo -e "${YELLOW}No audio devices found, running in text mode${NC}"
                MODE="text"
            fi
        fi
        ;;
    mac|windows)
        DOCKER_CMD="$DOCKER_CMD -e OLLAMA_HOST=http://host.docker.internal:11434"
        if [ "$MODE" != "text" ]; then
            echo -e "${YELLOW}Note: Audio passthrough is limited on $OS${NC}"
            echo "For best voice experience, consider running natively"
        fi
        ;;
esac

# Add image name and command based on mode
DOCKER_CMD="$DOCKER_CMD ${IMAGE_NAME}"
if [ "$MODE" == "text" ]; then
    DOCKER_CMD="$DOCKER_CMD python -m jarvis.main --text"
fi

# Show the command
echo ""
echo -e "${BLUE}Running command:${NC}"
echo "$DOCKER_CMD"
echo ""

# Execute
exec $DOCKER_CMD

