from dotenv import load_dotenv
# import multiprocessing
import os

# Load config: JARVIS_CONFIG_DIR (system install) or jarvis/.env (dev)
_config_dir = os.getenv("JARVIS_CONFIG_DIR")
if _config_dir:
    _env_path = os.path.join(_config_dir, "jarvis.conf")
    if os.path.isfile(_env_path):
        load_dotenv(_env_path)
    else:
        load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
else:
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

    # Contextor (memory) binary — Rust subprocess over stdio
    CONTEXTOR_BINARY = os.getenv("CONTEXTOR_BINARY", "contextor")

    # Context window sustainability — cap goals sent to LLM to avoid overflow
    MAX_GOALS_IN_CONTEXT = int(os.getenv("MAX_GOALS_IN_CONTEXT", "20"))

    # Memory (contextor) pruning — age-based + FIFO per theme
    MEMORY_RETENTION_DAYS = int(os.getenv("MEMORY_RETENTION_DAYS", "90"))
    MAX_ENTRIES_PER_THEME = int(os.getenv("MAX_ENTRIES_PER_THEME", "500"))

    # --- Context Retrieval (RAG) ---
    # Embedding model for semantic search (runs on Ollama)
    EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
    # Ollama URL for embeddings — defaults to local instance, NOT LLM_URL
    # (the LLM may live on a remote server; embeddings always need local Ollama)
    EMBED_URL = os.getenv("EMBED_URL", "http://localhost:11434")
    # Enable semantic search via contextor binary (requires embed model)
    RAG_ENABLED = os.getenv("RAG_ENABLED", "true").lower() == "true"
    # Number of memories to retrieve per RAG query
    RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
    # Minimum cosine similarity for RAG results (0.0-1.0)
    RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.3"))

    # --- Semantic Tool Discovery ---
    # Master switch for vector-based tool search via dispatch/dmcp
    ALLOW_EMBEDDING_SEARCH = os.getenv("ALLOW_EMBEDDING_SEARCH", "true").lower() == "true"
    # Bypass threshold — use vector search regardless of server count
    ENFORCE_EMBEDDING_SEARCH = os.getenv("ENFORCE_EMBEDDING_SEARCH", "false").lower() == "true"
    # Minimum visible servers before vector search auto-enables
    EMBEDDING_SEARCH_THRESHOLD = int(os.getenv("EMBEDDING_SEARCH_THRESHOLD", "100"))

    # Data directory — when set (e.g. systemd JARVIS_DATA_DIR=/var/lib/jarvis),
    # memory, goal archive, and default socket use this base path
    _DEFAULT_DATA_DIR = os.path.join(
        os.getenv("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")),
        "jarvis",
    )
    JARVIS_DATA_DIR = os.getenv("JARVIS_DATA_DIR", _DEFAULT_DATA_DIR)

    # Dual input — Unix socket for "jarvis send" and app integration
    # Default: JARVIS_DATA_DIR/input.sock (or ~/.jarvis/input.sock)
    JARVIS_INPUT_SOCKET = os.getenv(
        "JARVIS_INPUT_SOCKET",
        os.path.join(JARVIS_DATA_DIR, "input.sock"),
    )

    # Output IPC — Unix socket for apps/widgets to receive responses
    # Clients connect and receive JSON lines: {"output": "...", ...}
    JARVIS_OUTPUT_SOCKET = os.getenv(
        "JARVIS_OUTPUT_SOCKET",
        os.path.join(JARVIS_DATA_DIR, "output.sock"),
    )

    # Tool-Level Action (TLA) Confirmation
    # - "allow_all": never ask, run everything (power users / trusted environments)
    # - "smart":     only ask when tool has confirmation_required=true (default)
    # - "ask_all":   ask for every tool call, regardless of metadata
    CONFIRMATION_MODE = os.getenv("CONFIRMATION_MODE", "smart")

    # Default notification style for confirmation prompts:
    # - false: show desktop notification (notify-send)
    # - true:  suppress desktop notification; only use socket/CLI
    #          (lets external apps render their own confirmation UI)
    NOTIFICATION_SILENT = os.getenv("NOTIFICATION_SILENT", "false").lower() == "true"

    # Timeout (seconds) for user to respond to a confirmation prompt
    CONFIRMATION_TIMEOUT = int(os.getenv("CONFIRMATION_TIMEOUT", "30"))

    # Logging Configuration
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    LOG_FILE = os.getenv("LOG_FILE", "")  # Optional: path to log file (empty = no file logging)
    LOG_COLORS = os.getenv("LOG_COLORS", "true").lower() == "true"  # Enable colored console output

    # os.environ["OLLAMA_NO_GPU"] = "1"
    # os.environ["OLLAMA_NUM_THREADS"] = str(multiprocessing.cpu_count())

    # ------------------------------------------------------------------
    # Hierarchical prompt system: ROOT → DISPATCH
    # Memory actions (store, recall, search, list) are ROOT-level —
    # no separate CONTEXTOR LLM sub-chain.
    # ------------------------------------------------------------------

    LLM_WRONG_JSON_FORMAT_MESSAGE = """\
Your response was not valid JSON. Output ONLY a single JSON object — nothing else.
No thinking, no explanation, no markdown fences. Just the JSON.

Valid formats:

{{"action": "respond", "output": "your message", "goal_updates": []}}

{{"action": "dispatch", "intent": "what to accomplish"}}

{{"action": "store", "theme": "topic", "content": "fact", "goal_updates": []}}

{{"action": "recall", "theme": "topic", "goal_updates": []}}

{{"action": "search_memory", "query": "natural language query", "goal_updates": []}}

{{"action": "list_memory", "goal_updates": []}}

{{"action": "done", "summary": "result summary"}}

The very first character must be {{ and the very last must be }}.
Now, return ONLY the corrected JSON."""

    LLM_ROOT_PROMPT = """\
You are JARVIS, a personal assistant. Route or respond. Output ONLY valid JSON — no text before or after.

OS: {system} {release} ({machine}), Shell: {shell}

--- When to use each action ---

respond — Direct reply. Use for chat, greetings, general knowledge, or after a subsystem returns a result.
store — Remember a personal fact or preference under a topic theme.
recall — Recall stored facts by exact theme name.
search_memory — Search all memories by meaning (semantic search). Use when you need context.
list_memory — List all stored memory themes.
{data_consent_note}
dispatch — Run tools (calc, files, web, etc.). Use when user wants to DO something that needs external tools.

--- Actions (exact format) ---

{{
    "action": "respond",
    "output": "<your message>",
    "goal_updates": [{{"id": "<goal_id>", "status": "completed", "result": "summary"}}]
}}

{{
    "action": "store",
    "theme": "<topic>",
    "content": "<concise fact to remember>",
    "goal_updates": []
}}

{{
    "action": "recall",
    "theme": "<topic>",
    "goal_updates": []
}}

{{
    "action": "search_memory",
    "query": "<natural language query>",
    "top_k": 5,
    "offset": 0,
    "min_score": 0.3,
    "goal_updates": []
}}

{{
    "action": "list_memory",
    "goal_updates": []
}}

{{
    "action": "dispatch",
    "intent": "<what to accomplish>"
}}

--- Context ---
You receive: GOALS (with IDs), NEW INPUT, and optionally DISPATCH_SUMMARY from dispatch.
Memory operation results appear as STORE_RESULT, RECALL_RESULT, SEARCH_MEMORY_RESULT, LIST_MEMORY_RESULT.
RELEVANT MEMORIES may be included automatically based on user input (RAG retrieval).
Include goal_updates in respond: "completed" or "failed" with result.

--- Memory guidelines ---
- Choose descriptive theme names (e.g. "user_preferences", "school_schedule")
- When storing, extract the key fact — be concise
- Use search_memory with natural language — it searches by meaning, not keywords
- If search results aren't relevant, try again with a higher offset to dig deeper

Output exactly one JSON object. First char {{, last char }}.
"""

    LLM_ROOT_PROMPT_NO_CONTEXTOR = """\
You are JARVIS, a personal assistant. Route or respond. Output ONLY valid JSON — no text before or after.

OS: {system} {release} ({machine}), Shell: {shell}

--- When to use each action ---

respond — Direct reply. Use for chat, greetings, general knowledge, or after a subsystem returns a summary.
dispatch — Run tools (calc, files, web, etc.). Use when user wants to DO something that needs external tools.
Memory is disabled. Do not use store, recall, search_memory, or list_memory.

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

CRITICAL: Your ENTIRE response must be a single valid JSON object.

--- Workflow ---
1. plan: ALWAYS start here. Break down your intent into sub-tasks with keywords for each.
   The system will search for matching tools (semantic + keyword) and return AVAILABLE TOOLS.
2. If AVAILABLE TOOLS are provided: skip search/list_tools and dispatch directly.
3. If no tools were found: fall back to search → list_tools → install → dispatch.
4. done: Return a summary to the root system when finished.

--- Actions ---

Plan sub-tasks (ALWAYS start with this):
{{
    "action": "plan",
    "tasks": [
        {{"intent": "get weather forecast for Berlin", "keywords": ["weather", "forecast"], "top_k": 3, "min_score": 0.7}},
        {{"intent": "convert 50 EUR to USD", "keywords": ["currency", "exchange", "convert"], "top_k": 3, "min_score": 0.7}}
    ]
}}
- intent: Natural language description of what this sub-task needs
- keywords: Fallback keywords if semantic search finds nothing
- top_k: Max results per sub-task (default 5)
- min_score: Min relevance threshold 0.0-1.0 (default 0.3, use higher for precise queries)

Search for servers (fallback — use only if plan returned no tools):
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
- AVAILABLE_TOOLS: Tools matched by semantic/keyword search (after plan action)
- SEARCH_RESULTS: Server search results (legacy keyword search)
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
- ALWAYS start with "plan" to break down the intent — even for single tasks
- If AVAILABLE TOOLS appear after planning, dispatch directly (skip search/list_tools)
- Fall back to "search" only when plan returned no matching tools
- Do NOT guess server IDs or tool names — let the system find them
- You can dispatch multiple tasks for parallelism
- When tasks complete, use "done" to return the summary to root
- Output exactly one JSON object — no preamble, no trailing text

The very first character of your response must be {{ and the very last must be }}.
"""

