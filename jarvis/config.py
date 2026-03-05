from dotenv import load_dotenv
# import multiprocessing
import os

# Load .env file from the jarvis directory
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

class Config:
    # Models base directory
    MODELS_DIR = os.getenv("MODELS_DIR", "models")

    # Voice Provider Configuration
    STT_PROVIDER = os.getenv("STT_PROVIDER", "vosk")               # "vosk" (add more later)
    TTS_PROVIDER = os.getenv("TTS_PROVIDER", "piper")              # "piper" (add more later)
    ACTIVATION_PROVIDER = os.getenv("ACTIVATION_PROVIDER", "vosk") # "vosk" (add more later)

    # Vosk STT Configuration
    VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", os.path.join(MODELS_DIR, "vosk", "vosk-model-small-en-us-0.15"))

    # LLM Configuration
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")  # "ollama" or "api"
    LLM_MODEL = os.getenv("LLM_MODEL")

    # Unified LLM connection settings (used by all providers)
    LLM_URL = os.getenv("LLM_URL", "http://localhost:11434")
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_API_HEADERS = os.getenv("LLM_API_HEADERS")

    # Ollama-specific
    LLM_AUTO_PULL = os.getenv("LLM_AUTO_PULL", "false").lower() == "true"

    TTS_MODEL_ONNX = os.getenv("TTS_MODEL_ONNX")
    TTS_MODEL_JSON = os.getenv("TTS_MODEL_JSON")

    # Voice Activation Configuration
    WAKE_WORDS = os.getenv("WAKE_WORDS", "jarvis,hey jarvis,okay jarvis").split(",")
    VOICE_ACTIVATION_SENSITIVITY = float(os.getenv("VOICE_ACTIVATION_SENSITIVITY", "0.8"))

    # CLI Output Mode Configuration
    OUTPUT_MODE = os.getenv("OUTPUT_MODE", "voice")  # voice or text

    # Conversation History Configuration
    RESET_HISTORY_AFTER_RESPONSE = os.getenv("RESET_HISTORY_AFTER_RESPONSE", "true").lower() == "true"

    # Data consent for memory (contextor)
    # - true: Proactively remember what the user shares (name, preferences, etc.)
    # - false: Only remember when the user explicitly says "remember this"
    DATA_CONSENT = os.getenv("DATA_CONSENT", "true").lower() == "true"

    _DATA_CONSENT_NOTE_TRUE = (
        "The user has consented to memory. When they share personal details "
        "(name, school, preferences), remember them proactively. "
        "When they ask what you know about them, recall from memory."
    )
    _DATA_CONSENT_NOTE_FALSE = (
        "Only remember when the user explicitly says 'remember this' or 'remember that'. "
        "Otherwise respond without using memory."
    )
    DATA_CONSENT_NOTE = _DATA_CONSENT_NOTE_TRUE if DATA_CONSENT else _DATA_CONSENT_NOTE_FALSE

    # Contextor (memory) subsystem
    # - true: Enable long-term memory — ROOT can route to contextor
    # - false: Disable memory — ROOT only has respond and dispatch
    CONTEXTOR_ENABLED = os.getenv("CONTEXTOR_ENABLED", "true").lower() == "true"

    # Sudo Access Configuration
    # Note: This is a preference setting. Actual sudo access is managed by sudo_manager
    # This setting tracks whether sudo should be enabled (for installation/configuration purposes)
    JARVIS_SUDO_ENABLED = os.getenv("JARVIS_SUDO_ENABLED", "false").lower() == "true"

    # Dispatch Configuration
    DISPATCH_BINARY = os.getenv("DISPATCH_BINARY", "dispatch")  # Path to dispatch binary
    DMCP_BINARY = os.getenv("DMCP_BINARY", "dmcp")  # Path to dmcp binary
    DISPATCH_TIMEOUT = int(os.getenv("DISPATCH_TIMEOUT", "60"))  # seconds

    # Context window sustainability — cap goals sent to LLM to avoid overflow
    MAX_GOALS_IN_CONTEXT = int(os.getenv("MAX_GOALS_IN_CONTEXT", "20"))

    # Logging Configuration
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    LOG_FILE = os.getenv("LOG_FILE", "")  # Optional: path to log file (empty = no file logging)
    LOG_COLORS = os.getenv("LOG_COLORS", "true").lower() == "true"  # Enable colored console output

    # os.environ["OLLAMA_NO_GPU"] = "1"
    # os.environ["OLLAMA_NUM_THREADS"] = str(multiprocessing.cpu_count())

    # ------------------------------------------------------------------
    # Hierarchical prompt system: ROOT → DISPATCH / CONTEXTOR
    # ------------------------------------------------------------------

    LLM_WRONG_JSON_FORMAT_MESSAGE = """\
Your response was not valid JSON. Output ONLY a single JSON object — nothing else.
No thinking, no explanation, no markdown fences. Just the JSON.

Valid formats:

{{"action": "respond", "output": "your message", "goal_updates": []}}

{{"action": "contextor", "intent": "what to store or recall"}}

{{"action": "dispatch", "intent": "what to accomplish"}}

{{"action": "search", "keywords": ["keyword1"]}}

{{"action": "store", "theme": "topic", "content": "fact"}}

{{"action": "recall", "theme": "topic"}}

{{"action": "done", "summary": "result summary"}}

The very first character must be {{ and the very last must be }}.
Now, return ONLY the corrected JSON."""

    LLM_ROOT_PROMPT = """\
You are JARVIS, a personal assistant. Route or respond. Output ONLY valid JSON — no text before or after.

OS: {system} {release} ({machine}), Shell: {shell}

--- When to use each action ---

respond — Direct reply. Use for chat, greetings, general knowledge, or after a subsystem returns a summary.
contextor — Remember or recall personal info. Use your memory system (contextor) to help the user.
{data_consent_note}
dispatch — Run tools (calc, files, web, etc.). Use when user wants to DO something that needs external tools.

--- Actions (exact format) ---

{{
    "action": "respond",
    "output": "<your message>",
    "goal_updates": [{{"id": "<goal_id>", "status": "completed", "result": "summary"}}]
}}

{{
    "action": "contextor",
    "intent": "<what to remember or recall>"
}}

{{
    "action": "dispatch",
    "intent": "<what to accomplish>"
}}

--- Context ---
You receive: GOALS (with IDs), NEW INPUT, and optionally DISPATCH_SUMMARY or CONTEXTOR_SUMMARY from subsystems.
Include goal_updates in respond: "completed" or "failed" with result.

Output exactly one JSON object. First char {{, last char }}.
"""

    LLM_ROOT_PROMPT_NO_CONTEXTOR = """\
You are JARVIS, a personal assistant. Route or respond. Output ONLY valid JSON — no text before or after.

OS: {system} {release} ({machine}), Shell: {shell}

--- When to use each action ---

respond — Direct reply. Use for chat, greetings, general knowledge, or after a subsystem returns a summary.
dispatch — Run tools (calc, files, web, etc.). Use when user wants to DO something that needs external tools.
Memory is disabled. Do not use contextor.

--- Actions (exact format) ---

{{
    "action": "respond",
    "output": "<your message>",
    "goal_updates": [{{"id": "<goal_id>", "status": "completed", "result": "summary"}}]
}}

{{
    "action": "dispatch",
    "intent": "<what to accomplish>"
}}

--- Context ---
You receive: GOALS (with IDs), NEW INPUT, and optionally DISPATCH_SUMMARY from subsystems.
Include goal_updates in respond: "completed" or "failed" with result.

Output exactly one JSON object. First char {{, last char }}.
"""

    LLM_DISPATCH_PROMPT = """\
You are operating in DISPATCH mode. Your job is to find and execute MCP server tools.
You do NOT have a pre-loaded list of tools. Discover them on demand by searching.

CRITICAL: Your ENTIRE response must be a single valid JSON object.

--- Workflow ---
1. search: Find MCP servers by keywords
2. list_tools: See what tools a server offers and their parameter schemas
3. install: Install a server that isn't installed yet (runs setup automatically)
4. dispatch: Execute tasks on installed servers
5. done: Return a summary to the root system when finished

--- Actions ---

Search for servers:
{{"action": "search", "keywords": ["calculator", "math"]}}

List tools on a server:
{{"action": "list_tools", "server_id": "com.example.server"}}

Install a server:
{{"action": "install", "server_id": "com.example.server"}}

Dispatch tasks (concurrent execution):
{{
    "action": "dispatch",
    "tasks": [
        {{"server": "<server_id>", "tool": "<tool_name>", "params": {{}}, "remind_after": 60}}
    ]
}}

Wait for running tasks:
{{"action": "wait"}}

Kill running tasks:
{{"action": "kill", "pids": [1, 3]}}

Defer a goal:
{{"action": "defer", "goal_id": "<id>", "duration": 1800, "reason": "optional"}}

Return to root with results:
{{"action": "done", "summary": "<concise result summary for the root system>"}}

--- Context you receive ---
- INTENT: What the root system asked you to do
- GOALS: Active goals
- SEARCH_RESULTS: Server search results
- TOOLS: Tool listings from a server
- INSTALL_RESULT: Installation outcome
- DISPATCH_RESULT: Task execution results (signal window with PIDs, INIT/EXIT events)
- DISPATCH_ERROR: Error from task execution
- SIGNAL: Dispatch event (INIT, EXIT, REMIND, WAIT, KILL)

--- Signal types ---
- INIT: Task started (includes PID)
- EXIT: Task finished (includes output or error)
- REMIND: Task exceeded its reminder threshold
- WAIT: You previously chose to wait
- KILL: Task was terminated

--- Rules ---
- Do NOT guess server IDs or tool names — always search first
- You can dispatch multiple tasks for parallelism
- When tasks complete, use "done" to return the summary to root
- Output exactly one JSON object — no preamble, no trailing text

The very first character of your response must be {{ and the very last must be }}.
"""

    LLM_CONTEXTOR_PROMPT = """\
You are operating in CONTEXTOR mode. Your job is to manage long-term memory.
You are a personal assistant — remembering what the user tells you is a core part of your service.
Memory is organized by theme — each theme is a topic or subject area.

CRITICAL: Your ENTIRE response must be a single valid JSON object.

--- When to remember (store) ---
Remember when the user shares personal facts, preferences, project context, or anything
they might want you to recall later. Choose a clear, reusable theme name.
The user expects you to remember — do not refuse. This is an enabled feature.

--- When to recall ---
Recall when you need context about a topic to answer accurately. Search when the
exact theme is unknown.

--- Actions ---

Store information under a theme:
{{"action": "store", "theme": "<topic>", "content": "<concise fact to remember>"}}

Recall entries by theme:
{{"action": "recall", "theme": "<topic>"}}

Search across all memory by keywords:
{{"action": "search_memory", "keywords": ["keyword1", "keyword2"]}}

List all stored themes:
{{"action": "list_memory"}}

Return to root with results:
{{"action": "done", "summary": "<what was stored or recalled — include the actual data>"}}

--- Context you receive ---
- INTENT: What the root system asked you to do
- STORE_RESULT: Confirmation after storing
- RECALL_RESULT: Entries retrieved for a theme (includes "found" boolean)
- SEARCH_MEMORY_RESULT: Keyword search results
- LIST_MEMORY_RESULT: All available themes with entry counts

--- Rules ---
- Choose descriptive theme names (e.g. "user_preferences", "school_schedule", "project_jarvis")
- When storing, be concise — extract the key fact, don't store the entire conversation
- You can chain multiple actions: recall first, then store updates, then done
- Always finish with "done" and include the relevant data in the summary
- Output exactly one JSON object — no preamble, no trailing text

The very first character of your response must be {{ and the very last must be }}.
"""
