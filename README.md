# Project JARVIS

**An open-source, privacy-first AI assistant system with dynamic capability discovery.**

[![CI](https://github.com/YakupAtahanov/Project-JARVIS/actions/workflows/ci.yml/badge.svg)](https://github.com/YakupAtahanov/Project-JARVIS/actions/workflows/ci.yml)

Project JARVIS is a modular AI assistant that combines voice and text interfaces
with dynamic tool discovery through MCP (Model Context Protocol) orchestration.
It runs entirely on local hardware — no cloud dependencies, no data leaving your
device.

---

## Why This Exists

AI assistants that understand and control your computer are coming regardless.
The question is who builds them and who controls the data.

Corporate implementations have inherent incentives — data collection, vendor
lock-in, surveillance potential — that cannot be resolved through policy alone.
The only structural solution is open-source: code that anyone can read, audit,
and verify. Following the model established by the Free Software Foundation,
Project JARVIS is built as community-owned infrastructure, not a product.

During development, we identified security threats that the industry is only
now beginning to recognize — including novel phenomena like "forgetful context,"
where LLMs silently drop security constraints mid-session. These findings
informed both the system's architecture and an ongoing academic research effort
at Washington State University.

---

## System vs. Implementation

Project JARVIS is the **system** — a modular stack of components that can be
used independently or composed together:

| Component | Role |
|-----------|------|
| **Project-JARVIS** | AI daemon: voice/text interface, LLM orchestration, TUI |
| [**dispatch**](https://github.com/JarvisOSLinux/dispatch) | Signal-driven parallel task executor (Rust) |
| [**dmcp**](https://github.com/JarvisOSLinux/dmcp) | MCP server manager — discover, install, run, invoke (Rust) |
| [**contextor**](https://github.com/JarvisOSLinux/contextor) | Long-term memory store with vector search (Rust) |
| [**mcp-registry**](https://github.com/JarvisOSLinux/mcp-registry) | Curated catalog of installable MCP servers |

[**JARVIS OS**](https://github.com/JarvisOSLinux/jarvisos) is one
*implementation* — a complete Linux distribution that instantiates the system at
the OS level, with kernel-integrated policy enforcement and hardware monitoring.
But the system runs standalone on any platform: `pip install jarvis-ai` on any
Linux, macOS, or Windows machine.

---

## Core Features

- **Voice-First Interface**: Real-time speech recognition and synthesis
- **Wake Word Detection**: Always-listening voice activation with customizable wake words
- **CLI Support**: Text-based interface with `jarvis ask` command for scripting and accessibility
- **Hierarchical Orchestration**: ROOT mode handles dialogue/memory, DISPATCH mode handles tool execution
- **AI-Driven Tool Discovery**: Embedding-based semantic search across the MCP registry
- **Dynamic Capability Extension**: Add new tools without code changes via MCP servers
- **Local Processing**: All operations run locally — no data leaves your device
- **Cross-Platform**: Windows, Linux, macOS
- **Event-Driven Execution**: One event queue merges voice, CLI, socket, and dispatch signals
- **Flexible Output**: Text or voice responses

---

## Installation

### Quick Start (Minimal Install - CLI/Text Only)

For text-only mode without voice features:

```bash
# Install core package
pip install jarvis-ai

# Install Ollama and pull an LLM model
# Install Ollama from https://ollama.com/
ollama pull qwen3:4b

# Add an LLM provider and run
jarvis /providers add          # interactive — or:
# jarvis providers add --type ollama --model qwen3:4b
jarvis
```

### Full Installation with Voice Features

1. **Clone the repository with SuperMCP submodule**:
   ```bash
   git clone --recursive https://github.com/YakupAtahanov/Project-JARVIS.git
   cd Project-JARVIS
   ```

2. **Create required folders**:
   ```bash
   mkdir -p models/piper
   ```

3. **Download a Piper TTS model** (for voice output):
   - Get both `.onnx` and `.onnx.json` files from [Piper samples](https://rhasspy.github.io/piper-samples/).  
   - Place them in `models/piper`.  
   - Example: [en_US-libritts_r-medium](https://rhasspy.github.io/piper-samples/#en_US-libritts_r-medium).  

4. **Create and activate a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate   # Linux/MacOS
   venv\Scripts\activate      # Windows
   ```

5. **Install dependencies**:
   
   **Option A: Install with voice support (recommended)**:
   ```bash
   pip install jarvis-ai[voice]
   ```
   
   **Option B: Install from source with voice support**:
   ```bash
   pip install -e ".[voice]"
   ```
   
   **Option C: Install minimal (CLI/Text only)**:
   ```bash
   pip install jarvis-ai
   # or from source:
   pip install -e .
   ```
   
   **Option D: Install specific voice features**:
   ```bash
   pip install jarvis-ai[voice-input]   # Speech-to-text only
   pip install jarvis-ai[voice-output] # Text-to-speech only
   ```

6. **Install Ollama and pull an LLM model**:
   ```bash
   # Install Ollama from https://ollama.com/
   ollama pull qwen3:4b
   ```

7. **Configure LLM provider**:
   ```bash
   jarvis /providers add
   ```
   Providers (Ollama, OpenAI-compatible APIs) are stored in `~/.config/jarvis/providers.json`.
   Voice, logging, and other settings can be customized in `~/.config/jarvis/jarvis.conf`
   (see `jarvis/.env.example` for all available options).

8. **Run JARVIS**:
   ```bash
   jarvis
   ```

### Optional Dependency Groups

JARVIS supports optional dependencies for flexible installation:

- **`jarvis-ai`** - Core package (CLI/Text mode only)
- **`jarvis-ai[voice-input]`** - Add speech-to-text support
- **`jarvis-ai[voice-output]`** - Add text-to-speech support  
- **`jarvis-ai[voice]`** - Full voice support (input + output)
- **`jarvis-ai[dev]`** - Development tools (pytest, black, etc.)
- **`jarvis-ai[docs]`** - Documentation tools (sphinx, etc.)
- **`jarvis-ai[all]`** - Everything (voice + dev + docs)

**Note**: Voice features require audio devices. The system will gracefully degrade to text mode if audio is unavailable.

---

## Docker Support

Run JARVIS in Docker for easy cross-platform deployment and testing!

### **Quick Docker Setup**

```bash
# 1. Make sure Ollama is running on your host
ollama serve

# 2. Build the Docker image
./docker-build.sh        # Linux/Mac
docker-build.bat         # Windows

# 3. Run JARVIS
./docker-run.sh chat     # Text chat mode (recommended first test)
./docker-run.sh          # Dual input (voice + socket)
# OR use docker-compose
docker-compose up
```

### **Docker Benefits**
- ✅ **Cross-platform**: Same environment on Linux, Mac, Windows
- ✅ **Isolated**: No OS-level dependencies to manage
- ✅ **Portable**: Models baked into image
- ✅ **Easy testing**: Quick setup for development

### **Docker Commands**

```bash
# Text chat mode (recommended for first test)
docker run -it --rm --network host jarvis-ai:latest python -m jarvis.main chat

# Dual input (voice + socket, Linux with audio)
docker run -it --rm --network host --device /dev/snd jarvis-ai:latest

# Using docker-compose
docker-compose up
```

📖 **See [DOCKER.md](DOCKER.md) for detailed instructions and troubleshooting**

---

## CLI Interface

JARVIS now supports a command-line interface for text-based interaction without voice input!

### **Quick Start**

```bash
# Ask a question (uses current output mode)
jarvis ask "what is the weather?"

# Switch to text output
jarvis text

# Ask questions with text output
jarvis ask "list files in current directory"

# Switch to voice output
jarvis voice

# Check current mode
jarvis output-type

# Start voice activation mode (default)
jarvis
```

### **CLI Commands**

| Command | Description |
|---------|-------------|
| `jarvis` | Start dual-input mode (voice + socket) |
| `jarvis run` | Same as `jarvis` — event loop with voice and socket |
| `jarvis send "<message>"` | Send message to running JARVIS (from another terminal) |
| `jarvis chat` | Interactive text chat (stdin only) |
| `jarvis ask "<message>"` | Ask a question (one-shot, no daemon) |
| `jarvis text` | Set output mode to text |
| `jarvis voice` | Set output mode to voice (TTS) |
| `jarvis output-type` | Show current output mode |
| `jarvis history-reset on` | Enable history reset after each response |
| `jarvis history-reset off` | Disable history reset (maintain context) |
| `jarvis history-reset` | Show current history reset setting |
| `jarvis --help` | Show help message |

### **Dual Input (Two Terminals)**

```bash
# Terminal 1: Start JARVIS (voice + socket)
$ jarvis
Starting JARVIS (dual input: voice + socket)...
  Say 'Hey Jarvis' for voice, or use 'jarvis send <msg>' from another terminal.

# Terminal 2: Send a message
$ jarvis send "what is 2+2?"
Sent.
```

### **Usage Examples**

```bash
# Quick text query
$ jarvis text
$ jarvis ask "what is 2+2?"
Four.

# Use in scripts or pipelines
$ jarvis ask "analyze system logs" | grep ERROR

# Voice output for accessibility
$ jarvis voice
$ jarvis ask "read me the news"
[TTS speaks the response]

# Maintain conversation context
$ jarvis history-reset off
$ jarvis ask "My name is John"
$ jarvis ask "What's my name?"
# JARVIS remembers: "Your name is John"

# Reset context after each response (default)
$ jarvis history-reset on
$ jarvis ask "What's my name?"
# JARVIS doesn't remember previous context
```

---

## Voice Activation Configuration

JARVIS features advanced voice activation capabilities with customizable wake words and sensitivity settings.

### **Configuration Options**

Edit `~/.config/jarvis/jarvis.conf` (or `jarvis/.env` in development) to customize voice activation:

```bash
# Wake words (comma-separated)
WAKE_WORDS=jarvis,hey jarvis,okay jarvis

# Voice activation sensitivity (0.0 to 1.0)
VOICE_ACTIVATION_SENSITIVITY=0.8

# Vosk model path
VOSK_MODEL_PATH=models/vosk-model-small-en-us-0.15

# Logging configuration (optional)
LOG_LEVEL=INFO                # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FILE=logs/jarvis.log      # Optional: enable file logging
LOG_COLORS=true               # Colored console output
```

### **Voice Activation Features**

- **🎯 Customizable Wake Words**: Set any wake words you prefer
- **🔊 Sensitivity Control**: Adjust detection sensitivity for your environment
- **⚡ Real-time Detection**: Uses Vosk for fast, accurate wake word recognition
- **🔄 Smart Switching**: Automatically switches between listening modes
- **📊 Detection Statistics**: Track wake word detection performance
- **🛡️ Privacy-First**: All processing happens locally on your device

### **Usage Modes**

**Voice Activation Mode (Default)**:
- Always listens for wake words
- Responds only when activated
- Energy efficient and privacy-focused

**Continuous Listening Mode**:
- Constantly processes speech
- No wake word required
- Higher resource usage

---

## System Requirements

### Core Requirements (CLI/Text Mode)
- **Python**: 3.10 or later
- **Memory**: 4GB RAM minimum (8GB recommended)
- **CPU**: x86_64 (Apple Silicon and ARM64 builds may work but are untested)
- **OS**: Windows 10/11, Linux (Ubuntu 20.04+ recommended), macOS

### Voice Features Requirements (Optional)
- **Audio Hardware**: Microphone (for voice input) and speakers/headphones (for voice output)
- **Memory**: 8GB RAM (16GB recommended for larger models)
- **GPU**: Optional, for acceleration of Ollama or Piper TTS models
- **Additional Packages**: Install with `pip install jarvis-ai[voice]`

**Note**: JARVIS works perfectly fine without audio hardware - it will automatically use text mode.

---

## How It Works

JARVIS is designed as a conversational controller that can either answer directly or use tools when needed.

### **High-Level Workflow**
1. **You speak or type a request** (voice wake word, chat input, or `jarvis send`).
2. **JARVIS interprets intent** and decides whether the request needs tools.
3. **If tools are needed, JARVIS discovers and runs them** (including parallel execution when useful).
4. **Results are gathered and summarized** back into a single assistant response.
5. **JARVIS responds in text or voice** and waits for your next request.

### **Memory and Context**
- JARVIS can keep session context and use memory to improve follow-up answers.
- Conversation and memory handling are scoped to your active session.
- Session controls are available with slash commands like `/new`, `/sessions`, and `/switch`.

### **Safety and Control**
- Potentially sensitive tool actions can require user approval.
- The assistant stays responsive while waiting for approvals or long-running tasks.

### **For Technical Details**
If you want internals (state machine, dispatch loop, signal model, confirmation flow), see:
- `docs/jarvis-workflow-investigation.md`
- `docs/README-dispatch-2.md`

---

## Security & Threat Model

Project JARVIS is also a security research platform, built to study what happens when an LLM agent gets real system privileges (see [Why This Exists](#why-this-exists)). Full details — asset table, attacker profiles, per-threat implementation status, and an OpenClaw CVE-by-CVE comparison — live in **[`docs/SECURITY-ARCHITECTURE.md`](docs/SECURITY-ARCHITECTURE.md)**, which is the canonical, code-verified status; treat any other doc's claims as secondary to it.

**TLA (Threat Level Access)** is the core enforcement mechanism: a userspace, non-blocking, human-in-the-loop confirmation gate (`jarvis/core/confirmation_manager.py`, `jarvis/runtime/dispatch_flow.py`) that requires explicit out-of-band user approval before privileged tool calls execute. The LLM is deliberately kept out of the confirmation loop so it cannot misrepresent an action.

Six threats identified through live operation, and their current status:

| Threat | Status |
|---|---|
| Malicious MCP servers | implemented — registry vetting + `dmcp` manifest-hash verify + agent source-confinement |
| Prompt injection | partial — dispatch tags untrusted MCP output with a 128-bit CSPRNG boundary nonce; daemon does not yet verify the tag |
| Misleading MCP server usage | partial — official-tier review of tool descriptions + structured schema |
| Unauthorized sudo via MCP | implemented, with a known gap — bundled `shellmcp` doesn't declare `confirmation_required` on `run_command`, so it's gated only by the sudo prompt, not TLA (#159) |
| Sudo capability exploitation | implemented, same gap — TLA is goal-scoped |
| Bloated context | partial — dispatch rolling window + contextor pruning; persistent constraint preservation not implemented |

A seventh, novel finding — **"forgetful context"**, where the LLM silently drops security constraints stated earlier in a session — has no mitigation yet; it's the highest-priority open item. Full taxonomy and academic writeup: `docs/research.md`.

**JARVIS OS** (the Linux distribution built on this system) adds a kernel-level 4-tier policy engine (`/dev/jarvis`, SAFE/ELEVATED/DANGEROUS/FORBIDDEN) as a separate OS-embodiment layer — it is not yet consulted from this daemon's execution path.

Report vulnerabilities per [`SECURITY.md`](SECURITY.md); do not open public issues for unpatched findings.

### **Example MCP Servers**
- **ShellMCP**: Terminal command execution
- **CodeAnalysisMCP**: Code repository analysis and file operations
- **EchoMCP**: Testing and validation
- **FileSystemMCP**: Advanced file system operations
- **[Extensible]**: Add custom MCP servers dynamically

---

## Project Quality and Contribution

To keep Project JARVIS clean, consistent, and professional:

- Contribution guide: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Engineering standards: [`docs/engineering-standards.md`](docs/engineering-standards.md)
- Security reporting policy: [`SECURITY.md`](SECURITY.md)
- Cleanup roadmap: [`docs/clean-and-professional-plan-04-19-26.md`](docs/clean-and-professional-plan-04-19-26.md)

### Local Quality Commands

Use these from repository root:

```bash
make check   # format+lint+typecheck+tests (CI baseline)
make fix     # auto-format and import sorting
make test    # deterministic local test subset
```

Note: these commands use tooling from `.venv/bin`. If needed, create a virtualenv and install dev dependencies first:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

When opening PRs or issues, use the repository templates for faster triage and review quality.

---
