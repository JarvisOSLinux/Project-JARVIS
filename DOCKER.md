# 🐳 JARVIS Docker Guide

Complete guide for running Project JARVIS in Docker for cross-platform testing and deployment.

---

## ⚡ Quick Start (5 Minutes)

### **Prerequisites**
1. Docker installed: [Get Docker](https://docs.docker.com/get-docker/)
2. Ollama running: `ollama serve`
3. LLM model: `ollama pull codegemma:7b-instruct-q5_K_M`

### **Build & Run**

**Linux/Mac:**
```bash
./docker-build.sh           # Build image with CPU support (default, smallest)
./docker-build.sh cuda      # Build with NVIDIA GPU support
./docker-build.sh rocm      # Build with AMD GPU support
./docker-run.sh text        # Run in text mode (recommended first test)
./docker-run.sh             # Run in voice mode
```

**Windows:**
```bash
docker-build.bat            # Build image with CPU support (default)
docker-build.bat cuda       # Build with NVIDIA GPU support
docker-run.bat text         # Run in text mode
```

**Using docker-compose (all platforms):**
```bash
# CPU-only (default)
docker-compose up --build

# NVIDIA CUDA
TORCH_VARIANT=cuda docker-compose up --build

# AMD ROCm
TORCH_VARIANT=rocm docker-compose up --build
```

---

## 🔥 PyTorch Variants (CPU/CUDA/ROCm)

The Dockerfile supports building with different PyTorch backends to match your hardware.

### **Available Variants**

| Variant | Image Size | Hardware | Use Case |
|---------|-----------|----------|----------|
| **cpu** (default) | ~2-3 GB | Any system | No GPU, testing, smallest size |
| **cuda** | ~5-7 GB | NVIDIA GPUs | RTX/GTX/Tesla cards, best performance |
| **rocm** | ~5-7 GB | AMD GPUs | Radeon RX/Pro/Instinct (Linux only) |

### **How to Build Different Variants**

```bash
# CPU-only (default, smallest)
./docker-build.sh
# or explicitly:
./docker-build.sh cpu

# NVIDIA CUDA
./docker-build.sh cuda

# AMD ROCm
./docker-build.sh rocm

# Specify custom PyTorch version
./docker-build.sh cuda 2.8.0
```

**Manual Docker build:**
```bash
# CPU
docker build --build-arg TORCH_VARIANT=cpu -t jarvis-ai:cpu .

# CUDA
docker build --build-arg TORCH_VARIANT=cuda -t jarvis-ai:cuda .

# ROCm
docker build --build-arg TORCH_VARIANT=rocm -t jarvis-ai:rocm .
```

### **What This Does**

The Dockerfile installs PyTorch from different index URLs based on variant:

- **CPU**: `pip install torch --index-url https://download.pytorch.org/whl/cpu`
- **CUDA**: `pip install torch --index-url https://download.pytorch.org/whl/cu124`
- **ROCm**: `pip install torch --index-url https://download.pytorch.org/whl/rocm6.2`

This ensures you get the right PyTorch wheel for your hardware without unnecessary CUDA/ROCm libraries.

### **Running GPU Variants**

**NVIDIA CUDA:**
```bash
# Requires: nvidia-docker2 installed
docker run -it --rm --network host --gpus all jarvis-ai:cuda python -m jarvis.main --text
```

**AMD ROCm:**
```bash
# Requires: ROCm drivers installed
docker run -it --rm --network host \
  --device /dev/kfd --device /dev/dri --group-add video \
  jarvis-ai:rocm python -m jarvis.main --text
```

---

## 🛠️ Helper Scripts

We provide smart helper scripts that handle all the complexity for you:

### **Build Scripts**

**`docker-build.sh` / `docker-build.bat`**
- ✅ Validates Docker is installed
- ✅ Auto-initializes SuperMCP submodule
- ✅ Checks for required models
- ✅ Creates `.env` from template
- ✅ Builds the Docker image

### **Run Scripts**

**`docker-run.sh` / `docker-run.bat`**
- 🔍 Auto-detects your OS (Linux/Mac/Windows)
- 🔍 Checks if Ollama is running
- 🔍 Detects audio devices
- 🎯 Builds the correct Docker command automatically
- 💡 Supports mode argument: `./docker-run.sh text` or `./docker-run.sh`

**Usage:**
```bash
# Linux/Mac
./docker-run.sh text    # Text mode
./docker-run.sh         # Voice mode (auto-detects audio)

# Windows
docker-run.bat text     # Text mode
docker-run.bat          # Voice mode (limited)
```

---

## 🖥️ Platform-Specific Instructions

### **Linux**

**Text Mode:**
```bash
docker run -it --rm --network host jarvis-ai python -m jarvis.main --text
```

**Voice Mode:**
```bash
docker run -it --rm \
  --network host \
  --device /dev/snd \
  jarvis-ai
```

**Voice Mode with PulseAudio:**
```bash
docker run -it --rm \
  --network host \
  --device /dev/snd \
  -v /run/user/$(id -u)/pulse:/run/user/1000/pulse \
  -e PULSE_SERVER=unix:/run/user/1000/pulse/native \
  jarvis-ai
```

### **macOS / Windows**

**Text Mode:**
```bash
docker run -it --rm \
  -e OLLAMA_HOST=http://host.docker.internal:11434 \
  jarvis-ai python -m jarvis.main --text
```

**Voice Mode:**
> ⚠️ **Note**: Audio passthrough is limited on Mac/Windows Docker.
> For best voice experience, run JARVIS natively or use text mode.

---

## 🎯 Common Usage Examples

### **1. Interactive Voice Activation (Default)**
```bash
# Using helper script (recommended)
./docker-run.sh

# Or using docker-compose
docker-compose up

# Or manual Docker command (Linux)
docker run -it --rm --network host --device /dev/snd jarvis-ai
```

### **2. Text-Only CLI Mode**
```bash
# Using helper script
./docker-run.sh text

# Interactive text mode
docker run -it --rm --network host jarvis-ai python -m jarvis.main --text

# Ask a question directly
docker run -it --rm --network host jarvis-ai python -m jarvis.cli ask "What time is it?"
```

### **3. Development Mode (Live Code Changes)**
Mount your source code as a volume to edit code without rebuilding:
```bash
docker run -it --rm \
  --network host \
  --device /dev/snd \
  -v $(pwd)/jarvis:/app/jarvis \
  jarvis-ai
```

### **4. Custom Configuration**
```bash
# Create and edit your .env
cp jarvis/.env.example jarvis/.env
nano jarvis/.env

# Run with custom config
docker run -it --rm \
  --network host \
  -v $(pwd)/jarvis/.env:/app/jarvis/.env:ro \
  jarvis-ai
```

### **5. Running Tests**
```bash
docker run -it --rm --network host jarvis-ai python -m pytest tests/
```

---

## 🔧 Configuration

### **Environment Variables**

Override config values at runtime:

```bash
docker run -it --rm \
  --network host \
  -e LLM_MODEL=llama3:8b \
  -e OUTPUT_MODE=text \
  -e WAKE_WORDS="jarvis,hey jarvis" \
  jarvis-ai
```

### **Available Variables**

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama API endpoint |
| `LLM_MODEL` | (from .env) | LLM model name |
| `OUTPUT_MODE` | `voice` | Output mode: `voice` or `text` |
| `WAKE_WORDS` | `jarvis,hey jarvis,okay jarvis` | Wake words (comma-separated) |
| `VOICE_ACTIVATION_SENSITIVITY` | `0.8` | Sensitivity (0.0-1.0) |
| `VOSK_MODEL_PATH` | `models/vosk-model-small-en-us-0.15` | Vosk model path |

---

## 🐛 Troubleshooting

### **Cannot connect to Ollama**

**Symptom**: `ConnectionError: [Errno 111] Connection refused`

**Solutions:**
- **Linux**: Use `--network host` or ensure Ollama listens on `0.0.0.0:11434`
- **Mac/Windows**: Use `-e OLLAMA_HOST=http://host.docker.internal:11434`
- **Test connection**: `curl http://localhost:11434/api/version`

### **No audio devices in container**

**Symptom**: `No audio input/output devices found`

**Solutions:**
- Use `--device /dev/snd` (Linux only)
- Check host audio: `aplay -l`
- Try PulseAudio socket mounting (see platform instructions)
- **Mac/Windows**: Use text mode or run natively

### **Models not found**

**Symptom**: `FileNotFoundError: models/vosk-model-small-en-us-0.15`

**Solutions:**
- Models are baked into image during build
- Ensure `models/` directory exists before building
- Rebuild: `docker-compose build --no-cache`

### **Permission denied on audio**

**Symptom**: `PermissionError: /dev/snd/...`

**Solutions:**
```bash
# Add user to audio group (Linux)
sudo usermod -a -G audio $USER
newgrp audio

# Or run with --privileged (not recommended)
docker run --privileged ...
```

### **Ollama connection works on host but not in container**

**Solutions:**
- **Linux**: Make sure Ollama listens on all interfaces:
  ```bash
  OLLAMA_HOST=0.0.0.0:11434 ollama serve
  ```
- **Mac/Windows**: Verify `host.docker.internal` resolves:
  ```bash
  docker run --rm alpine ping -c 1 host.docker.internal
  ```

---

## 🎤 Audio Setup (Linux)

### **Check Audio Devices**
```bash
# List devices
aplay -l
ls -la /dev/snd

# Test in container
docker run -it --rm --device /dev/snd python:3.11-slim \
  bash -c "apt-get update && apt-get install -y alsa-utils && aplay -l"
```

### **PulseAudio Configuration**
If using PulseAudio, share the socket:
```bash
docker run -it --rm \
  --network host \
  --device /dev/snd \
  -v /run/user/$(id -u)/pulse:/run/user/1000/pulse \
  -e PULSE_SERVER=unix:/run/user/1000/pulse/native \
  jarvis-ai
```

### **ALSA Configuration**
Create `~/.asoundrc` if needed:
```
pcm.!default {
    type pulse
}
ctl.!default {
    type pulse
}
```

---

## 📦 Docker Image Details

- **Base**: Python 3.11-slim
- **Size**: ~2-3 GB (includes Vosk + Piper models)
- **User**: Non-root `jarvisuser` (UID 1000)
- **Working Dir**: `/app`
- **Models**:
  - Vosk STT: `models/vosk-model-small-en-us-0.15/`
  - Piper TTS: `models/piper/`

---

## 🔐 Security Best Practices

1. **Non-root user**: Container runs as UID 1000
2. **Read-only mounts**: Use `:ro` flag when mounting configs
3. **Network isolation**: Prefer bridge network over `host` when possible
4. **No credentials in image**: Use environment variables or volumes
5. **Minimal device access**: Only grant necessary devices

---

## 💡 Tips & Best Practices

1. **Use helper scripts** - They handle OS detection and validation automatically
2. **Test text mode first** - Easier to debug without audio complications
3. **Use docker-compose** - Simplifies configuration management
4. **Mount .env for secrets** - Never bake credentials into images
5. **Development with volumes** - Mount source code for live changes
6. **Clean up regularly** - `docker system prune` to free space
7. **Check logs** - `docker logs jarvis` or `docker-compose logs -f`

---

## 🚀 Advanced Usage

### **Using docker-compose**

**Basic usage:**
```bash
docker-compose up          # Run in foreground
docker-compose up -d       # Run in background
docker-compose logs -f     # View logs
docker-compose down        # Stop and remove
```

**Override settings:**
```bash
# Edit docker-compose.yml to uncomment environment variables
# Or create docker-compose.override.yml (git-ignored)
```

### **Building for Different Platforms**
```bash
# Build for specific platform
docker buildx build --platform linux/amd64 -t jarvis-ai:amd64 .
docker buildx build --platform linux/arm64 -t jarvis-ai:arm64 .
```

### **Publishing to Docker Hub**
```bash
# Tag
docker tag jarvis-ai:latest yourusername/jarvis-ai:latest
docker tag jarvis-ai:latest yourusername/jarvis-ai:1.0.0

# Push
docker push yourusername/jarvis-ai:latest
docker push yourusername/jarvis-ai:1.0.0
```

---

## 🆘 Getting Help

**Check logs:**
```bash
docker logs jarvis                    # If run with --name jarvis
docker-compose logs -f                # If using compose
```

**Test Ollama from container:**
```bash
docker exec -it jarvis curl http://host.docker.internal:11434/api/version
```

**Interactive debugging:**
```bash
docker run -it --rm --network host jarvis-ai bash
```

**Open an issue on GitHub with:**
- Your OS and Docker version
- Error messages and logs
- Steps to reproduce

---

## 📚 Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose](https://docs.docker.com/compose/)
- [Ollama Docs](https://ollama.com/)
- [Project JARVIS README](README.md)

---

**Happy Dockerizing! 🚀**

