# 🐳 JARVIS Docker Guide

Complete guide for running Project JARVIS in Docker for cross-platform testing and deployment.

---

## ⚡ Quick Start (5 Minutes)

### **Prerequisites**
1. Docker installed: [Get Docker](https://docs.docker.com/get-docker/)
2. Ollama running: `ollama serve`
3. LLM model: `ollama pull qwen3:4b` (or your preferred model)

### **Build & Run**

**Linux/Mac:**
```bash
./docker-build.sh           # Build image
./docker-run.sh chat        # Text chat mode (recommended first test)
./docker-run.sh             # Dual input (voice + socket)
```

**Windows:**
```bash
docker-build.bat            # Build image
docker-run.bat chat         # Text chat mode
docker-run.bat              # Dual input mode
```

**Using docker-compose (all platforms):**
```bash
docker-compose up --build
```

---

## 🛠️ Helper Scripts

### **Build Scripts**

**`docker-build.sh` / `docker-build.bat`**
- Validates Docker is installed
- Checks for models directory
- Creates `.env` from template if missing
- Builds the Docker image

### **Run Scripts**

**`docker-run.sh` / `docker-run.bat`**
- Auto-detects your OS (Linux/Mac/Windows)
- Checks if Ollama is running
- Detects audio devices (Linux)
- Supports mode: `chat` (text) or default (voice + socket)

**Usage:**
```bash
# Linux/Mac
./docker-run.sh chat    # Interactive text chat
./docker-run.sh         # Voice + socket (dual input)

# Windows
docker-run.bat chat     # Text chat
docker-run.bat          # Dual input (audio limited)
```

---

## 🖥️ Platform-Specific Instructions

### **Linux**

**Text Chat Mode:**
```bash
docker run -it --rm --network host jarvis-ai:latest python -m jarvis.main chat
```

**Dual Input (Voice + Socket):**
```bash
docker run -it --rm \
  --network host \
  --device /dev/snd \
  jarvis-ai:latest
```

**Voice with PulseAudio:**
```bash
docker run -it --rm \
  --network host \
  --device /dev/snd \
  -v /run/user/$(id -u)/pulse:/run/user/1000/pulse \
  -e PULSE_SERVER=unix:/run/user/1000/pulse/native \
  jarvis-ai:latest
```

### **macOS / Windows**

**Text Chat Mode:**
```bash
docker run -it --rm \
  -e OLLAMA_HOST=http://host.docker.internal:11434 \
  jarvis-ai:latest python -m jarvis.main chat
```

**Voice Mode:**
> ⚠️ **Note**: Audio passthrough is limited on Mac/Windows Docker.
> For best voice experience, run JARVIS natively or use text mode.

---

## 🎯 Common Usage Examples

### **1. Interactive Text Chat**
```bash
./docker-run.sh chat

# Or with docker-compose
docker-compose run jarvis python -m jarvis.main chat
```

### **2. Dual Input (Voice + Socket)**
```bash
./docker-run.sh
# Or: docker-compose up
```

### **3. One-Shot Question**
```bash
docker run -it --rm --network host jarvis-ai:latest python -m jarvis.main ask "What time is it?"
```

### **4. Custom Configuration**
```bash
cp jarvis/.env.example jarvis/.env
# Edit jarvis/.env with your settings

docker run -it --rm \
  --network host \
  -v $(pwd)/jarvis/.env:/app/jarvis/.env:ro \
  jarvis-ai:latest
```

### **5. Development Mode (Live Code Changes)**
```bash
docker run -it --rm \
  --network host \
  -v $(pwd)/jarvis:/app/jarvis \
  jarvis-ai:latest
```

### **6. Running Tests**
```bash
docker run -it --rm --network host jarvis-ai:latest python -m pytest tests/
```

---

## 🔧 Configuration

### **Environment Variables**

Override config at runtime:

```bash
docker run -it --rm \
  --network host \
  -e LLM_MODEL=qwen3:4b \
  -e OUTPUT_MODE=text \
  -e WAKE_WORDS="jarvis,hey jarvis" \
  jarvis-ai:latest
```

### **Available Variables**

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama API endpoint |
| `LLM_MODEL` | (from .env) | LLM model name |
| `OUTPUT_MODE` | `voice` | `voice` or `text` |
| `WAKE_WORDS` | `jarvis,hey jarvis,okay jarvis` | Wake words (comma-separated) |

---

## 📝 Note on dispatch/dmcp

The Docker image runs JARVIS in **conversation-only mode** when the `dispatch` and `dmcp` binaries are not available. Tool execution (MCP servers) requires those binaries to be built and available. For full functionality, run JARVIS natively or add dispatch/dmcp to the image.

---

## 🐛 Troubleshooting

### **Cannot connect to Ollama**

**Symptom**: `ConnectionError: Connection refused`

**Solutions:**
- **Linux**: Use `--network host` or ensure Ollama listens on `0.0.0.0:11434`
- **Mac/Windows**: Use `-e OLLAMA_HOST=http://host.docker.internal:11434`
- **Test**: `curl http://localhost:11434/api/version`

### **No audio devices**

**Symptom**: `No audio input/output devices found`

**Solutions:**
- Use `--device /dev/snd` (Linux only)
- Check host: `aplay -l`
- Try text mode: `./docker-run.sh chat`

### **Models not found**

**Symptom**: `FileNotFoundError` for Vosk or Piper models

**Solutions:**
- Ensure `models/` exists with Vosk and Piper model files before building
- See README.md for model download instructions
- Rebuild: `docker-compose build --no-cache`

---

## 📦 Image Details

- **Base**: Python 3.12-slim
- **User**: Non-root `jarvisuser` (UID 1000)
- **Working Dir**: `/app`
- **Install**: `pip install -e ".[voice]"` from pyproject.toml

---

## 📚 Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose](https://docs.docker.com/compose/)
- [Ollama Docs](https://ollama.com/)
- [Project JARVIS README](README.md)
