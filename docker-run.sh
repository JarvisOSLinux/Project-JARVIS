#!/bin/bash
# Docker run script for Project JARVIS

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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
    echo -e "${YELLOW}✗${NC}"
    echo -e "${YELLOW}Warning: Cannot connect to Ollama at localhost:11434${NC}"
    echo "Please make sure Ollama is running:"
    echo "  ollama serve"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Parse mode argument
MODE="${1:-voice}"

# Build Docker command
DOCKER_CMD="docker run -it --rm"

# OS-specific settings
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    DOCKER_CMD="$DOCKER_CMD --network host"
    if [ "$MODE" != "text" ] && [ "$MODE" != "chat" ]; then
        if [ -d "/dev/snd" ]; then
            DOCKER_CMD="$DOCKER_CMD --device /dev/snd"
            echo -e "${GREEN}Audio devices found${NC}"
        else
            echo -e "${YELLOW}No audio devices found, running in text mode${NC}"
            MODE="chat"
        fi
    fi
elif [[ "$OSTYPE" == "darwin"* ]] || [[ "$OSTYPE" == "msys"* ]] || [[ "$OSTYPE" == "cygwin"* ]]; then
    DOCKER_CMD="$DOCKER_CMD -e OLLAMA_HOST=http://host.docker.internal:11434"
    if [ "$MODE" != "text" ] && [ "$MODE" != "chat" ]; then
        echo -e "${YELLOW}Note: Audio passthrough is limited on this OS${NC}"
        echo "For best voice experience, consider running natively"
    fi
fi

# Add image and command
DOCKER_CMD="$DOCKER_CMD jarvis-ai:latest"
if [ "$MODE" == "text" ] || [ "$MODE" == "chat" ]; then
    DOCKER_CMD="$DOCKER_CMD python -m jarvis.main chat"
    echo -e "${BLUE}Running in text chat mode...${NC}"
else
    echo -e "${BLUE}Running in dual input mode (voice + socket)...${NC}"
fi

echo ""
echo -e "${BLUE}Command:${NC}"
echo "$DOCKER_CMD"
echo ""

exec $DOCKER_CMD
