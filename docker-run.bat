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

REM Build Docker command
set DOCKER_CMD=docker run -it --rm -e OLLAMA_HOST=http://host.docker.internal:11434

set DOCKER_CMD=%DOCKER_CMD% jarvis-ai:latest

if "%MODE%"=="text" set MODE=chat
if "%MODE%"=="chat" (
    set DOCKER_CMD=%DOCKER_CMD% python -m jarvis.main chat
    echo Running in text chat mode...
) else (
    echo Running in dual input mode (voice + socket)...
    echo Note: Audio passthrough is limited on Windows Docker
    echo For best voice experience, consider running natively
)

echo.
echo Running command:
echo %DOCKER_CMD%
echo.

%DOCKER_CMD%
