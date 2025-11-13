@echo off
REM Docker build script for Project JARVIS (Windows)

echo Building JARVIS Docker Image...
echo ==================================
echo.

REM Check if Docker is installed
docker --version >nul 2>&1
if errorlevel 1 (
    echo Error: Docker is not installed
    echo Please install Docker Desktop from https://docs.docker.com/desktop/install/windows-install/
    exit /b 1
)

REM Check if SuperMCP submodule is initialized
if not exist "jarvis\SuperMCP\SuperMCP.py" (
    echo Warning: SuperMCP submodule not initialized
    echo Initializing submodules...
    git submodule update --init --recursive
    if errorlevel 1 (
        echo Failed to initialize submodules
        echo Please run: git submodule update --init --recursive
        exit /b 1
    )
    echo Submodules initialized
)

REM Check if models directory exists
if not exist "models\" (
    echo Warning: models\ directory not found
    echo Models are required for voice features
    echo Please download models before building (see README.md)
    set /p CONTINUE="Continue anyway? (y/N) "
    if /i not "%CONTINUE%"=="y" exit /b 1
)

REM Check if .env exists, if not create from template
if not exist "jarvis\.env" (
    echo Creating .env from template...
    copy jarvis\config.env.template jarvis\.env
    echo Created jarvis\.env - Please edit it with your settings
)

REM Build the image
echo Building Docker image...
docker build -t jarvis-ai:latest .

if errorlevel 0 (
    echo.
    echo Build successful!
    echo.
    echo Image info:
    docker images jarvis-ai
    echo.
    echo Quick start commands:
    echo.
    echo   Text mode (recommended for first test^):
    echo     docker run -it --rm -e OLLAMA_HOST=http://host.docker.internal:11434 jarvis-ai python -m jarvis.main --text
    echo.
    echo   Using docker-compose:
    echo     docker-compose up
    echo.
    echo See DOCKER_USAGE.md for more information
) else (
    echo Build failed!
    exit /b 1
)

