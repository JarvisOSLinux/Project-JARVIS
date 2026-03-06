# Project JARVIS

**AI-Native Voice Assistant with Dynamic Capability Discovery**

Project JARVIS is an innovative AI-powered voice assistant that combines natural language processing with dynamic tool discovery through SuperMCP (Super Model Context Protocol) orchestration. Featuring always-listening wake word detection, it can understand spoken commands, discover available tools dynamically, and execute them through specialized MCP servers - all while maintaining complete privacy through local processing.

---

## ✨ Revolutionary Features

- **🎤 Voice-First Interface**: Real-time speech recognition and synthesis
- **👂 Wake Word Detection**: Always-listening voice activation with customizable wake words ("Jarvis", "Hey Jarvis", etc.)
- **💻 CLI Support**: Text-based interface with `jarvis ask` command for scripting and accessibility
- **🧠 AI-Driven Tool Discovery**: Automatically discovers and uses available MCP servers
- **🔧 Dynamic Capability Extension**: Add new tools without code changes
- **🛡️ Secure Local Processing**: All operations run locally for privacy
- **🌐 Cross-Platform Support**: Windows, Linux, macOS compatibility
- **⚡ Self-Extending AI**: Can create new MCP servers on demand
- **🎯 Smart Audio Management**: Automatically switches between wake word detection and command processing
- **🔄 Flexible Output**: Choose between text or voice output for responses  

---

## ⚡ Installation

### Quick Start (Minimal Install - CLI/Text Only)

For text-only mode without voice features:

```bash
# Install core package
pip install jarvis-ai

# Install Ollama and pull an LLM model
# Install Ollama from https://ollama.com/
ollama pull qwen3:4b

# Configure and run
cp jarvis/.env.example jarvis/.env
cd jarvis
python main.py
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

7. **Configure environment variables**:  
   - Copy `jarvis/.env.example` to `jarvis/.env`
   - Adjust settings as needed for your system

8. **Run JARVIS**:  
   ```bash
   cd jarvis
   python main.py
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

## 🐳 Docker Support

Run JARVIS in Docker for easy cross-platform deployment and testing!

### **Quick Docker Setup**

```bash
# 1. Make sure Ollama is running on your host
ollama serve

# 2. Build the Docker image (includes models)
./docker-build.sh        # Linux/Mac
docker-build.bat         # Windows

# 3. Run JARVIS
./docker-run.sh          # Linux/Mac - auto-detects voice/text
docker-run.bat           # Windows
# OR use docker-compose
docker-compose up
```

### **Docker Benefits**
- ✅ **Cross-platform**: Same environment on Linux, Mac, Windows
- ✅ **Isolated**: No OS-level dependencies to manage
- ✅ **Portable**: Models baked into image (~2-3GB)
- ✅ **Easy testing**: Quick setup for development

### **Docker Commands**

```bash
# Text mode (recommended for first test)
docker run -it --rm --network host jarvis-ai python -m jarvis.main --text

# Voice mode (Linux with audio)
docker run -it --rm --network host --device /dev/snd jarvis-ai

# Using docker-compose
docker-compose up
```

📖 **See [DOCKER.md](DOCKER.md) for detailed instructions and troubleshooting**

---

## 💻 CLI Interface

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

## 🎤 Voice Activation Configuration

JARVIS features advanced voice activation capabilities with customizable wake words and sensitivity settings.

### **Configuration Options**

Edit `jarvis/.env` to customize voice activation:

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

## 🖥️ System Requirements

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

## 🔧 How It Works

### **Voice Activation Mode (Default)**
1. **👂 Always Listening**: Continuously monitors for wake words ("Jarvis", "Hey Jarvis", etc.)
2. **🎯 Wake Word Detection**: Vosk-based real-time wake word recognition
3. **🎤 Voice Input**: After wake word, switches to command processing mode
4. **🧠 AI Processing**: LLM (via Ollama) analyzes the request and determines required tools
5. **🔍 Dynamic Discovery**: SuperMCP discovers available MCP servers and their capabilities
6. **⚡ Tool Execution**: Appropriate MCP servers execute the requested operations
7. **🔊 Voice Output**: Piper TTS converts the response back to speech
8. **🔄 Return to Listening**: Automatically returns to wake word detection mode

### **Legacy Continuous Mode**
1. **🎤 Voice Input**: User speaks into microphone, Vosk converts speech to text
2. **🧠 AI Processing**: LLM (via Ollama) analyzes the request and determines required tools
3. **🔍 Dynamic Discovery**: SuperMCP discovers available MCP servers and their capabilities
4. **⚡ Tool Execution**: Appropriate MCP servers execute the requested operations
5. **🔊 Voice Output**: Piper TTS converts the response back to speech

**Revolutionary Architecture**:
```
Wake Word → Voice Activation → STT → LLM → SuperMCP → MCP Servers → Response → TTS → Return to Listening
```

### **Available MCP Servers**
- **ShellMCP**: Terminal command execution
- **CodeAnalysisMCP**: Code repository analysis and file operations
- **EchoMCP**: Testing and validation
- **FileSystemMCP**: Advanced file system operations
- **[Extensible]**: Add custom MCP servers dynamically

---

## 🚀 Innovation & Future Vision

### **Current Capabilities**
- ✅ **Voice-First AI Assistant**: Real-time speech processing
- ✅ **Wake Word Detection**: Always-listening voice activation system
- ✅ **CLI Interface**: Text-based command interface for scripting
- ✅ **Flexible Output**: Text or voice response modes
- ✅ **Dynamic Tool Discovery**: SuperMCP orchestration
- ✅ **Local Privacy**: All processing on-device
- ✅ **Cross-Platform**: Windows, Linux, macOS support
- ✅ **Extensible Architecture**: Plugin-based MCP servers
- ✅ **Smart Audio Management**: Automatic mode switching
- ✅ **Customizable Wake Words**: User-defined activation phrases
- ✅ **Professional Logging**: Configurable logging system with colored output and file logging support

### **Revolutionary Features in Development**
- 🔄 **AI-Driven MCP Generation**: AI creates new tools on demand
- 🧠 **Intelligent Server Routing**: Automatic tool selection
- 🌐 **MCP Registry Integration**: Access to ecosystem of tools
- 🔒 **Enhanced Security**: Sandboxed execution environments
- 📊 **Performance Analytics**: Usage tracking and optimization

### **Future Applications**
- **🏠 Smart Home Integration**: Voice-controlled IoT devices
- **💼 Enterprise Automation**: Business process automation
- **🎓 Educational Tools**: Interactive learning assistants
- **🔬 Research Platform**: Scientific computation and analysis
- **🎨 Creative Workflows**: Content creation and design

---

## 🏆 Why This Matters

JARVIS represents a **paradigm shift** in AI assistant technology:

- **🎯 True Intelligence**: Not just command execution, but dynamic capability discovery
- **🔧 Self-Extending**: Grows its own capabilities based on user needs
- **🛡️ Privacy-First**: Local processing ensures data never leaves your device
- **🌱 Open Ecosystem**: Community-driven tool development and sharing

This is the future of human-computer interaction - **AI that truly understands and adapts**.

---
