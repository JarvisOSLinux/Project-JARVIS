@echo off
REM Docker build script for Project JARVIS (Windows)

echo Building JARVIS Docker Image...
echo ==================================

REM Check if Docker is installed
docker --version >nul 2>&1
if errorlevel 1 (
    echo Error: Docker is not installed
    echo Please install Docker Desktop from https://docs.docker.com/desktop/install/windows-install/
    exit /b 1
)

REM Check if models directory exists
if not exist "models\" (
    echo Warning: models\ directory not found
    echo Models are required for voice features (Vosk, Piper)
    echo Please download models before building (see README.md)
    set /p CONTINUE="Continue anyway? (y/N) "
    if /i not "%CONTINUE%"=="y" exit /b 1
)

REM Check if .env exists, if not create from template
if not exist "jarvis\.env" (
    echo Creating .env from template...
    copy jarvis\.env.example jarvis\.env
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
    echo Quick start:
    echo   docker-run.bat text    # Text mode (recommended first test)
    echo   docker-run.bat        # Dual input (voice + socket)
    echo.
    echo See DOCKER.md for more information
) else (
    echo Build failed!
    exit /b 1
)
