@echo off
REM Docker run script for Project JARVIS (Windows)

echo JARVIS Docker Launcher
echo ==================================

REM Check if Docker is installed
docker --version >nul 2>&1
if errorlevel 1 (
    echo Error: Docker is not installed
    echo Please install Docker Desktop from https://docs.docker.com/desktop/install/windows-install/
    exit /b 1
)

REM Check if image exists
docker image inspect jarvis-ai:latest >nul 2>&1
if errorlevel 1 (
    echo Error: jarvis-ai:latest image not found
    echo Please build the image first:
    echo   docker-build.bat
    echo   OR
    echo   docker-compose build
    exit /b 1
)

REM Check if Ollama is running
echo Checking Ollama connection...
curl -s http://localhost:11434/api/version >nul 2>&1
if errorlevel 1 (
    echo Warning: Cannot connect to Ollama at localhost:11434
    echo Please make sure Ollama is running
    set /p CONTINUE="Continue anyway? (y/N) "
    if /i not "%CONTINUE%"=="y" exit /b 1
)

REM Parse mode argument
set MODE=%1
if "%MODE%"=="" set MODE=voice

REM Detect PyTorch variant from environment (default: cpu)
if "%TORCH_VARIANT%"=="" set TORCH_VARIANT=cpu
set IMAGE_NAME=jarvis-ai:%TORCH_VARIANT%

REM Check if specific variant image exists
docker image inspect %IMAGE_NAME% >nul 2>&1
if errorlevel 1 set IMAGE_NAME=jarvis-ai:latest

REM Build Docker command
set DOCKER_CMD=docker run -it --rm -e OLLAMA_HOST=http://host.docker.internal:11434

REM Add GPU support based on TORCH_VARIANT
if "%TORCH_VARIANT%"=="cuda" (
    REM Check for nvidia-smi
    where nvidia-smi >nul 2>&1
    if not errorlevel 1 (
        set DOCKER_CMD=%DOCKER_CMD% --gpus all
        echo ✓ NVIDIA GPU support enabled
    ) else (
        echo Warning: CUDA variant selected but nvidia-smi not found
        echo Install nvidia-docker2 for GPU support, or use CPU variant
    )
)

if "%TORCH_VARIANT%"=="rocm" (
    REM AMD GPU support (limited on Windows)
    set DOCKER_CMD=%DOCKER_CMD% --device=/dev/kfd --device=/dev/dri
    echo ✓ AMD ROCm GPU support enabled
)

set DOCKER_CMD=%DOCKER_CMD% %IMAGE_NAME%

if "%MODE%"=="text" (
    set DOCKER_CMD=%DOCKER_CMD% python -m jarvis.main --text
    echo Running in TEXT mode...
) else (
    set DOCKER_CMD=%DOCKER_CMD%
    echo Running in VOICE mode...
    echo Note: Audio passthrough is limited on Windows Docker
    echo For best voice experience, consider running natively
)

echo.
echo Running command:
echo %DOCKER_CMD%
echo.

REM Execute
%DOCKER_CMD%

