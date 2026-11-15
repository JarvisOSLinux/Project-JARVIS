# Project JARVIS

**An open-source, privacy-first AI assistant system with dynamic capability discovery.**

[![CI](https://github.com/JarvisOSLinux/Project-JARVIS/actions/workflows/ci.yml/badge.svg)](https://github.com/JarvisOSLinux/Project-JARVIS/actions/workflows/ci.yml)

Project JARVIS is a modular AI assistant that combines voice and text interfaces
with dynamic tool discovery through MCP (Model Context Protocol) orchestration.
Local models run on llama.cpp's `llama-server`, which JARVIS starts itself — no
cloud dependency required.
Optionally, remote OpenAI-compatible API providers (OpenAI, OpenRouter, etc.)
can be added to the provider failover pool; when a remote provider is used,
prompts are sent to that provider.

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
now beginning to recognize — including the novel "bloated context," where LLMs
silently drop security constraints across the context lifecycle, whether crowded
out of a saturated window or never durably stored across a refresh. These findings
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
But the system runs standalone on any platform: `pip install project-jarvis` on
any Linux, macOS, or Windows machine.

---

## Core Features

- **Voice-First Interface**: Real-time speech recognition and synthesis
- **Wake Word Detection**: Always-listening voice activation with customizable wake words
- **CLI Support**: Text-based interface with `jarvis ask` command for scripting and accessibility
- **Unified Orchestration**: a single ROOT loop handles dialogue, memory, tool discovery (semantic search over the MCP registry), and concurrent tool dispatch — no separate dispatch sub-chain
- **AI-Driven Tool Discovery**: Embedding-based semantic search across the MCP registry
- **Dynamic Capability Extension**: Add new tools without code changes via MCP servers
- **Local-First Processing**: STT, TTS, memory, and orchestration run locally; LLM inference runs on a local llama.cpp model JARVIS serves itself, with optional remote API providers
- **Cross-Platform**: Windows, Linux, macOS
- **Event-Driven Execution**: One event queue merges voice, CLI, socket, and dispatch signals
- **Flexible Output**: Text or voice responses

---

## Installation

### Quick Start (Minimal Install - CLI/Text Only)

For text-only mode without voice features:

```bash
# Install core package
pip install project-jarvis

# Install llama.cpp's llama-server (Arch: pacman -S llama-cpp), or set
# LLAMA_SERVER_BIN to a llama-server binary

# Add a local model (downloaded from Hugging Face on first start) and run
jarvis providers add --type llamacpp --model Qwen/Qwen3-4B-GGUF:Q4_K_M
# (or run `jarvis tui` and type /providers add for a guided interactive form)
jarvis
```

### Full Installation with Voice Features

1. **Clone the repository with the Rust submodules (dispatch, dmcp, contextor)**:
   ```bash
   git clone --recursive https://github.com/JarvisOSLinux/Project-JARVIS.git
   cd Project-JARVIS
   ```

2. **Create required folders**:
   ```bash
   mkdir -p ~/.local/share/jarvis/models/piper
   ```
   Models live under the platform data dir, **not** a `models/` folder in the
   checkout: `~/.local/share/jarvis/models` on Linux,
   `~/Library/Application Support/jarvis/models` on macOS,
   `%LOCALAPPDATA%\jarvis\models` on Windows. Override with `MODELS_DIR` or
   `JARVIS_MODELS_DIR`.

3. **Download a Piper TTS model** (for voice output):
   - Get both `.onnx` and `.onnx.json` files from [Piper samples](https://rhasspy.github.io/piper-samples/).  
   - Place them in `<models dir>/piper` (e.g. `~/.local/share/jarvis/models/piper`).  
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
   pip install "project-jarvis[voice]"
   ```
   
   **Option B: Install from source with voice support**:
   ```bash
   pip install -e ".[voice]"
   ```
   
   **Option C: Install minimal (CLI/Text only)**:
   ```bash
   pip install project-jarvis
   # or from source:
   pip install -e .
   ```
   
   **Option D: Install specific voice features**:
   ```bash
   pip install "project-jarvis[voice-input]"   # Speech-to-text only
   pip install "project-jarvis[voice-output]"  # Text-to-speech only
   ```

6. **Install llama.cpp** for local models: JARVIS starts its `llama-server`
   itself (Arch: `pacman -S llama-cpp`; elsewhere set `LLAMA_SERVER_BIN` to a
   `llama-server` binary). Not needed if you only use remote providers.

7. **Configure LLM provider**:
   ```bash
   # A local GGUF model from Hugging Face (user/repo[:quant]), downloaded on first
   # start or ahead of time with `jarvis model pull`
   jarvis providers add --type llamacpp --model Qwen/Qwen3-4B-GGUF:Q4_K_M
   # Or an OpenAI-compatible API (an Ollama server you run is one: --url http://localhost:11434)
   jarvis providers add --type api --model <m> --url <u> --key <k>
   jarvis providers add --type lmstudio --model <m>
   jarvis providers add --type ollama-cloud --model gpt-oss:120b --key <k>
   ```
   (Or run `jarvis tui` and type `/providers add` for a guided interactive form.)
   Providers (`llamacpp`, `api`, `lmstudio`, `ollama-cloud`) are stored in `~/.config/jarvis/providers.json`.
   Voice, logging, and other settings can be customized in `~/.config/jarvis/jarvis.conf`
   (see `jarvis/.env.example` for all available options).

8. **Run JARVIS**:
   ```bash
   jarvis
   ```

### Optional Dependency Groups

JARVIS supports optional dependencies for flexible installation:

- **`project-jarvis`** - Core package (CLI/Text mode only)
- **`project-jarvis[voice-input]`** - Add speech-to-text support (Vosk)
- **`project-jarvis[voice-output]`** - Add text-to-speech support
- **`project-jarvis[voice]`** - Full voice support (input + output)
- **`project-jarvis[voice-whisper]`** - faster-whisper speech-to-text (opt-in; includes voice-input, more accurate than Vosk but needs more RAM). Not part of `voice` or `all`.
- **`project-jarvis[tui]`** - Interactive terminal UI (`jarvis tui`)
- **`project-jarvis[voice-aec]`** - Acoustic echo cancellation for barge-in (includes voice; needs a C++ toolchain)
- **`project-jarvis[dev]`** - Development tools (pytest, black, etc.)
- **`project-jarvis[docs]`** - Documentation tools (sphinx, etc.)
- **`project-jarvis[all]`** - Everything (tui + voice-aec/voice + dev + docs)

**Note**: Voice features require audio devices. The system will gracefully degrade to text mode if audio is unavailable.

---

## Docker Support

Run JARVIS in Docker for easy cross-platform deployment and testing!

### **Quick Docker Setup**

```bash
# 1. Build the Docker image
./docker-build.sh        # Linux/Mac
docker-build.bat         # Windows

# 2. Add a provider once (the image has no llama-server: use api, lmstudio or
#    ollama-cloud; see DOCKER.md)

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
| `jarvis tui` | Interactive terminal UI with session sidebar (needs `[tui]` extra) |
| `jarvis providers ...` | Manage the LLM provider failover pool (list/add/remove/move/edit) |
| `jarvis confirm [approve <id> [0,2]\|deny <id>\|approve-all]` | List/resolve pending TLA tool confirmations; the optional index list approves only those items of a batch |
| `jarvis sudo [enable\|disable]` | Manage sudo access for privileged tools |
| `jarvis model pull\|list\|rm <user/repo[:quant]>` | Download, list or delete local GGUF models |
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

# Maintain conversation context (default)
$ jarvis history-reset off
$ jarvis ask "My name is John"
$ jarvis ask "What's my name?"
# JARVIS remembers: "Your name is John"

# Reset context after each response
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

# Vosk model path — absolute override. The default is
# <models dir>/vosk/vosk-model-small-en-us-0.15, where <models dir> is the
# platform data dir (Linux: ~/.local/share/jarvis/models), not cwd-relative.
# VOSK_MODEL_PATH=/home/you/.local/share/jarvis/models/vosk/vosk-model-small-en-us-0.15

# Speech-to-text engine for utterances: "vosk" (default) or "faster-whisper"
# (needs the voice-whisper extra; weights auto-download to <models dir>/whisper
# on first use). Wake-word detection stays on Vosk either way.
# STT_PROVIDER=vosk
# WHISPER_MODEL_SIZE=small      # tiny | base | small | medium | large-v3
# WHISPER_MODEL_DIR=/home/you/.local/share/jarvis/models/whisper

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
- **🎚️ Pluggable Transcription**: The utterance phase can run on Vosk (default) or faster-whisper (`STT_PROVIDER=faster-whisper`); if the package or model is missing at startup, JARVIS logs the reason and falls back to Vosk. Accuracy and latency figures for the whisper path have not yet been measured on real hardware.

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
- **GPU**: Optional, for acceleration of local llama.cpp models or Piper TTS
- **Additional Packages**: Install with `pip install "project-jarvis[voice]"`

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
- `docs/architecture.md` — state machine, event loop, module map
- `docs/dispatch-design.md` — dispatch loop and signal model
- `docs/tla-confirmation-design.md` — confirmation flow

---

## Security & Threat Model

Project JARVIS is also a security research platform, built to study what happens when an LLM agent gets real system privileges (see [Why This Exists](#why-this-exists)). Full details — asset table, attacker profiles, per-threat implementation status, and an OpenClaw CVE-by-CVE comparison — live in **[`docs/SECURITY-ARCHITECTURE.md`](docs/SECURITY-ARCHITECTURE.md)**, which is the canonical, code-verified status; treat any other doc's claims as secondary to it.

**TLA (Threat Level Access)** is the core enforcement mechanism: a userspace, non-blocking, human-in-the-loop confirmation gate (`jarvis/core/confirmation_manager.py`, `jarvis/runtime/dispatch_flow.py`) that requires explicit out-of-band user approval before privileged tool calls execute. The LLM is deliberately kept out of the confirmation loop so it cannot misrepresent an action.

Six threats identified through live operation, and their current status:

| Threat | Status |
|---|---|
| Malicious MCP servers | implemented — registry vetting + `dmcp` manifest-hash verify + agent source-confinement |
| Prompt injection | implemented — dispatch tags untrusted MCP output with a 128-bit CSPRNG boundary nonce, and the daemon verifies it on every EXIT/TIMEOUT (`jarvis/dispatch/boundary.py`, #165), marking any mismatch UNVERIFIED in-band; system prompts instruct the model to treat boundary content as data-only |
| Misleading MCP server usage | partial — official-tier review of tool descriptions + structured schema |
| Unauthorized sudo via MCP | implemented — the host floor in `jarvis/core/threat_level.py` rates command-execution tools (`run_command`, `exec`, `shell`, …) at least DANGEROUS regardless of what a manifest declares, so a shell server cannot under-declare its own threat level (#159/#162) |
| Sudo capability exploitation | implemented — same author-proof host floor, plus a dangerous-payload scan of tool params (`sudo`, `rm -rf`, `dd if=`, pipe-to-shell, …); TLA confirmation is goal-scoped |
| Bloated context (novel) | partial — the daemon's context window + rolling summary (#213), dispatch's rolling window, and contextor pruning bound the saturation face; the persistent constraint register at the dispatch gate (#214) covers path-prefix deny rules for the non-persistence face, which stays open for broader constraints |

**Bloated context** is the standout novel finding: a single context-lifecycle failure with two faces — security constraints crowded out of a saturated context window, and constraints never durably stored so a context refresh loses them structurally rather than incidentally. Both traced to one code path in the daemon that never ran; that path was repaired (#213), and the non-persistence face now has its first mechanical mitigation — a persistent constraint register enforced at the dispatch gate (#214), currently scoped to path-prefix deny rules, with broader constraints still open. (Forgetful Context was split out in 2026-07 and merged back here in 2026-08.) Full taxonomy and academic writeup: `docs/research.md`.

**JARVIS OS** (the Linux distribution built on this system) adds a kernel-level 4-tier policy engine (`/dev/jarvis`, SAFE/ELEVATED/DANGEROUS/FORBIDDEN) as a separate OS-embodiment layer — it is not yet consulted from this daemon's execution path.

Report vulnerabilities per [`SECURITY.md`](SECURITY.md); do not open public issues for unpatched findings.

### **Example MCP Servers**
- **ShellMCP** (bundled): shell command execution, app launch, web search
- **Registry catalog**: `jarvis-shell`, `brave-search`, `server-filesystem`, `sqlite-mcp`, `mcp-github`, `time-mcp` — installable from the community [mcp-registry](https://github.com/JarvisOSLinux/mcp-registry) via `dmcp`
- **[Extensible]**: Add custom MCP servers dynamically

---

## Project Quality and Contribution

To keep Project JARVIS clean, consistent, and professional:

- Contribution guide: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Engineering standards: [`docs/engineering-standards.md`](docs/engineering-standards.md)
- Security reporting policy: [`SECURITY.md`](SECURITY.md)

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

## Changelog — corrected claims

*2026-11-15:* Ollama references trued to the code after #291: local models run on llama.cpp's `llama-server`, which JARVIS starts itself; the provider types are `llamacpp`, `api`, `lmstudio` and `ollama-cloud` (an Ollama server you run is an `api` provider); `jarvis auto-pull` is gone, `jarvis model pull|list|rm` replaces it.

*2026-08-15:* threat table and the Bloated Context paragraph trued to the code: the daemon's two-tier context manager runs on every ROOT turn (#213 — the "never runs" wording below was written before the repair and had gone stale), and the persistent constraint register is shipped, not planned (#214) — path-prefix deny rules persisted at `$JARVIS_DATA_DIR/constraints.json`, enforced at the dispatch gate ahead of the confirmation mode, re-injected into every ROOT prompt, managed via `jarvis constrain`/`unconstrain`/`constraints`. The non-persistence face stays open for constraints beyond path rules.

*2026-08-01:* taxonomy merged back to **six** threats — Forgetful Context folded back into the novel Bloated Context, which now covers both faces of the context-lifecycle failure (constraints crowded out of a saturated window, and constraints never durably stored so a refresh loses them). The two are one failure with one dead code path behind them: the daemon's two-tier context manager never runs, so a single config flag (`RESET_HISTORY_AFTER_RESPONSE`) decides which face appears. Threat-table row and the intro reworded off "forgetful context" onto the merged Bloated Context, with the non-persistence face marked as the highest-priority open item and a persistent constraint register (enforced at the dispatch gate) as the planned mitigation. The 2026-07-22 seven-threat entry below is left intact as history.

*2026-07-27:* voice section now names both STT engines (#138) — Vosk stays the default and the only wake-word engine; faster-whisper is an opt-in `voice-whisper` extra for utterance transcription, excluded from `voice` and `all`. Its accuracy/latency on real hardware is unverified: no audio device or model was available where it was implemented.

*2026-07-24:* threat-table rows 4/5 settled to **implemented** to match the canonical `docs/SECURITY-ARCHITECTURE.md` — the `shellmcp` `run_command` gap (#159/#162) is closed by the author-proof host floor in `jarvis/core/threat_level.py`, which a manifest cannot lower; models base dir corrected from cwd-relative `models/` to the platform data dir (`~/.local/share/jarvis/models` on Linux) in both the install steps and the Vosk config sample, matching `Config.MODELS_DIR`.

*2026-07-22:* PyPI name corrected everywhere (`jarvis-ai` → `project-jarvis`, matching `pyproject.toml`); local-only wording softened to local-first with optional OpenAI-compatible providers in the failover pool; `jarvis /providers add` → `jarvis providers add ...` (the slash form is TUI-only); submodule note corrected (dispatch/dmcp/contextor, no "SuperMCP") and repo URLs moved to the JarvisOSLinux org; `history-reset` default corrected (context is maintained by default); CLI table extended with implemented commands (`tui`, `providers`, `confirmations`, `sudo`, `auto-pull`); dependency-group list extended (`tui`, `voice-aec`); dead doc links replaced with existing docs; example MCP servers now list real bundled/registry servers (former list was test fixtures); taxonomy formalized to seven threats with Forgetful Context split from Bloated Context (2026-07); Vosk path corrected to `models/vosk/...`.
