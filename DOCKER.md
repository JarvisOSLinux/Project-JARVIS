# 🐳 JARVIS Docker Guide

Complete guide for running Project JARVIS in Docker for cross-platform testing and deployment.

---

## ⚡ Quick Start (5 Minutes)

### **Prerequisites**
1. Docker installed: [Get Docker](https://docs.docker.com/get-docker/)
2. A model the container can reach. The image has no `llama-server`, so a local
   `llamacpp` provider does not run in it: use an `api` provider (an Ollama or
   `llama-server` on the host, or a cloud API), `lmstudio` or `ollama-cloud`.

### **Configure an LLM provider (first run)**

A fresh container has no provider configured — the model/endpoint come from
`~/.config/jarvis/providers.json`, not env vars. Persist the config in a
volume and add a provider once:

```bash
# e.g. an Ollama server on the host, reached as localhost with --network host (Linux)
docker run -it --rm -v jarvis-config:/home/jarvisuser/.config/jarvis \
  jarvis-ai:latest jarvis providers add --type api --model qwen3:4b --url http://localhost:11434
# then reuse the same volume for every run: -v jarvis-config:/home/jarvisuser/.config/jarvis
# (docker-run.sh, docker-run.bat and docker-compose.yml mount it)
```

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
- Mounts the `jarvis-config` volume, where `providers.json` lives
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
docker run -it --rm -v jarvis-config:/home/jarvisuser/.config/jarvis \
  jarvis-ai:latest jarvis providers add --type api --model qwen3:4b \
    --url http://host.docker.internal:11434   # once; Ollama on the host
docker run -it --rm -v jarvis-config:/home/jarvisuser/.config/jarvis \
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
  -v jarvis-config:/home/jarvisuser/.config/jarvis \
  -e OUTPUT_MODE=text \
  -e WAKE_WORDS="jarvis,hey jarvis" \
  jarvis-ai:latest
```

### **Available Variables**

| Variable | Default | Description |
|----------|---------|-------------|
| `OUTPUT_MODE` | `voice` | `voice` or `text` |
| `WAKE_WORDS` | `jarvis,hey jarvis,okay jarvis` | Wake words (comma-separated) |
| `JARVIS_MODELS_DIR` | `<data dir>/models` (in-container: `/home/jarvisuser/.local/share/jarvis/models`) | Base directory for Vosk/Piper model files. `MODELS_DIR` is accepted as a legacy alias and takes precedence if both are set. Mount a volume here if you want models to survive `--rm`. |
| `JARVIS_OPENAI_SERVER_ENABLED` | `false` | Opt-in OpenAI-compatible HTTP endpoint (`jarvis/server/openai_compat.py`). Nothing listens unless this is `true`. |
| `JARVIS_OPENAI_SERVER_HOST` | `127.0.0.1` | Bind address for that endpoint. |
| `JARVIS_OPENAI_SERVER_PORT` | `8317` | Bind port for that endpoint. Publish it (`-p 8317:8317`) only together with the two settings below. |
| `JARVIS_OPENAI_SERVER_ALLOW_NONLOCAL` | `false` | Second, separate opt-in required to bind anything other than loopback. Setting `_HOST=0.0.0.0` alone is refused — which is what you need inside a container if you intend to publish the port. |
| `JARVIS_OPENAI_SERVER_TOKEN_FILE` | `<config dir>/openai_server_token` (in-container: `/home/jarvisuser/.config/jarvis/openai_server_token`) | Bearer token file, generated `0600` on first use. Every request needs the token; there is no anonymous mode. Mount the config volume to keep it stable across runs. |

> ⚠️ The OpenAI-compatible endpoint is the one deliberate TCP listener in
> JARVIS — every other IPC surface is a filesystem object. Exposing it from a
> container puts it on the Docker network; read the security notes in
> `jarvis/server/openai_compat.py` and `docs/SECURITY-ARCHITECTURE.md` first.

Model and endpoint selection are **not** env vars: they come from the provider
pool (`jarvis providers add --type <api|lmstudio|ollama-cloud> --model <m> [--url <u>]`,
stored in `providers.json`). `OLLAMA_HOST`/`LLM_MODEL` are not read by the daemon.

---

## 📝 Note on dispatch/dmcp

The Docker image runs JARVIS in **conversation-only mode** when the `dispatch` and `dmcp` binaries are not available. Tool execution (MCP servers) requires those binaries to be built and available. For full functionality, run JARVIS natively or add dispatch/dmcp to the image.

---

## 🐛 Troubleshooting

### **Cannot reach the model server on the host**

**Symptom**: `ConnectionError: Connection refused`

**Solutions:**
- **Linux**: Use `--network host` or ensure the server (e.g. Ollama) listens on `0.0.0.0`
- **Mac/Windows**: point the provider at the host: `jarvis providers add --type api --model qwen3:4b --url http://host.docker.internal:11434`
- **Test** (Ollama): `curl http://localhost:11434/api/version`

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

---

## Changelog — corrected claims

*2026-11-15:* Ollama is no longer built in (#291): there is no `ollama` provider type, and the image has no `llama-server`, so a container uses an `api` (a host Ollama included), `lmstudio` or `ollama-cloud` provider. The run scripts no longer check for Ollama or pass the unread `OLLAMA_HOST`; they and docker-compose mount the `jarvis-config` volume that holds `providers.json`.

*2026-07-24:* env table extended with the variables the merged tree actually reads — `JARVIS_MODELS_DIR` (plus the legacy `MODELS_DIR` alias; base dir is the platform data dir, not a cwd-relative `models/`) and the five `JARVIS_OPENAI_SERVER_*` settings for the opt-in OpenAI-compatible listener, with its non-loopback double opt-in and mandatory bearer token called out. Names/defaults verified against `jarvis/config.py`.

*2026-07-22:* first-run provider configuration added (fresh containers have no provider; `providers.json` is the only model/endpoint source); `LLM_MODEL`/`OLLAMA_HOST` env guidance removed — neither is read by the daemon; examples updated to persist `~/.config/jarvis` in a volume.
