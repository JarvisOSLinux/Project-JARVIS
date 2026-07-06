# import multiprocessing
import os

from dotenv import load_dotenv

# Load config: JARVIS_CONFIG_DIR (system install) or jarvis/.env (dev)
_config_dir = os.getenv("JARVIS_CONFIG_DIR")
if _config_dir:
    _env_path = os.path.join(_config_dir, "jarvis.conf")
    if os.path.isfile(_env_path):
        load_dotenv(_env_path)
    else:
        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
else:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


class Config:
    # Models base directory
    MODELS_DIR = os.getenv("MODELS_DIR", "models")

    # Voice Provider Configuration
    STT_PROVIDER = os.getenv("STT_PROVIDER", "vosk")  # "vosk" (add more later)
    TTS_PROVIDER = os.getenv("TTS_PROVIDER", "piper")  # "piper" (add more later)
    ACTIVATION_PROVIDER = os.getenv(
        "ACTIVATION_PROVIDER", "vosk"
    )  # "vosk" (add more later)

    # Vosk STT Configuration
    VOSK_MODEL_PATH = os.getenv(
        "VOSK_MODEL_PATH",
        os.path.join(MODELS_DIR, "vosk", "vosk-model-small-en-us-0.15"),
    )

    # Ollama-specific
    LLM_AUTO_PULL = os.getenv("LLM_AUTO_PULL", "false").lower() == "true"
    OLLAMA_AUTO_START = os.getenv("OLLAMA_AUTO_START", "true").lower() == "true"

    # Sampling temperature for LLM responses (0.0 = deterministic, 1.0+ = creative)
    # Default 0.7 gives a balance of consistency and variety.
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))

    # Ollama extended thinking mode. "true" keeps reasoning in the separate
    # `thinking` field (prevents leaking into `content`). "false" disables
    # thinking entirely. Unset = let the model decide. Ignored by models that
    # don't support the option.
    _llm_think_raw = os.getenv("LLM_THINK", "").lower()
    LLM_THINK = (
        True
        if _llm_think_raw == "true"
        else False if _llm_think_raw == "false" else None
    )

    # Ollama strict JSON mode (format="json"). Default off because reasoning
    # models (qwen3, gpt-oss, deepseek-r1, ...) emit thinking tokens that
    # conflict with grammar-constrained decoding and return empty content.
    # JARVIS's _extract_json strips thinking tags, code fences, and trailing
    # noise — it does not need server-side JSON enforcement. Set true only
    # when using a non-reasoning model that benefits from strict mode.
    LLM_STRICT_JSON = os.getenv("LLM_STRICT_JSON", "false").lower() == "true"

    from .platform import current as _platform

    _DEFAULT_CONFIG_DIR = os.getenv(
        "JARVIS_CONFIG_DIR",
        str(_platform.config_dir()),
    )
    PROVIDERS_FILE = os.getenv(
        "PROVIDERS_FILE",
        os.path.join(_DEFAULT_CONFIG_DIR, "providers.json"),
    )

    TTS_MODEL_ONNX = os.getenv("TTS_MODEL_ONNX")
    TTS_MODEL_JSON = os.getenv("TTS_MODEL_JSON")

    # Voice Activation Configuration
    WAKE_WORDS = os.getenv("WAKE_WORDS", "jarvis,hey jarvis,okay jarvis").split(",")
    VOICE_ACTIVATION_SENSITIVITY = float(
        os.getenv("VOICE_ACTIVATION_SENSITIVITY", "0.8")
    )
    # Seconds of silence after a wake word before giving up and returning to
    # wake-word mode. Disabled (never times out) when <= 0.
    VOICE_ACTIVATION_TIMEOUT = float(os.getenv("VOICE_ACTIVATION_TIMEOUT", "4.0"))

    # CLI Output Mode Configuration
    OUTPUT_MODE = os.getenv("OUTPUT_MODE", "voice")  # voice or text

    # Conversation History Configuration
    # false: multi-turn chat (Claude-like); true: one-shot / stateless per reply
    RESET_HISTORY_AFTER_RESPONSE = (
        os.getenv("RESET_HISTORY_AFTER_RESPONSE", "false").lower() == "true"
    )

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
    DATA_CONSENT_NOTE = (
        _DATA_CONSENT_NOTE_TRUE if DATA_CONSENT else _DATA_CONSENT_NOTE_FALSE
    )

    # Contextor (memory) subsystem
    # - true: Enable long-term memory — ROOT can route to contextor
    # - false: Disable memory — ROOT only has respond and dispatch
    CONTEXTOR_ENABLED = os.getenv("CONTEXTOR_ENABLED", "true").lower() == "true"

    # Sudo Access Configuration
    # Note: This is a preference setting. Actual sudo access is managed by sudo_manager
    # This setting tracks whether sudo should be enabled (for installation/configuration purposes)
    JARVIS_SUDO_ENABLED = os.getenv("JARVIS_SUDO_ENABLED", "false").lower() == "true"

    # Dispatch Configuration
    DISPATCH_BINARY = os.getenv(
        "DISPATCH_BINARY", "dispatch"
    )  # Path to dispatch binary
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

    # --- Token Tracking ---
    # Model context window size (tokens). Used for ctx: X/Y display in the TUI.
    # 0 = unknown (shows raw token count without a denominator).
    LLM_CONTEXT_WINDOW = int(os.getenv("LLM_CONTEXT_WINDOW", "0"))

    # --- Semantic Tool Discovery ---
    # Master switch for vector-based tool search via dispatch/dmcp
    ALLOW_EMBEDDING_SEARCH = (
        os.getenv("ALLOW_EMBEDDING_SEARCH", "true").lower() == "true"
    )
    # Bypass threshold — use vector search regardless of server count
    ENFORCE_EMBEDDING_SEARCH = (
        os.getenv("ENFORCE_EMBEDDING_SEARCH", "false").lower() == "true"
    )
    # Minimum visible servers before vector search auto-enables.
    # Default is 3 so embedding fires as soon as any servers are indexed.
    EMBEDDING_SEARCH_THRESHOLD = int(os.getenv("EMBEDDING_SEARCH_THRESHOLD", "3"))

    # Data directory — when set (e.g. systemd JARVIS_DATA_DIR=/var/lib/jarvis),
    # memory, goal archive, and default socket use this base path
    _DEFAULT_DATA_DIR = os.getenv("JARVIS_DATA_DIR", str(_platform.data_dir()))
    JARVIS_DATA_DIR = _DEFAULT_DATA_DIR

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

    # GUI IPC — bidirectional socket for desktop apps (jarvisos-app, widgets).
    # Structured JSON protocol with state, streaming responses, confirmations.
    JARVIS_GUI_SOCKET = os.getenv(
        "JARVIS_GUI_SOCKET",
        os.path.join(JARVIS_DATA_DIR, "jarvis.sock"),
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

    # Timeout (seconds) for user to respond to a confirmation prompt before
    # auto-denying. 0 disables the timeout entirely -- pending confirmations
    # are tracked in a persistent, queryable list (CLI/socket) instead, so
    # nothing needs to expire just because nobody answered within N seconds.
    # Set a positive value to restore the old auto-deny behavior for
    # unattended/headless setups.
    CONFIRMATION_TIMEOUT = int(os.getenv("CONFIRMATION_TIMEOUT", "0"))

    # Logging Configuration
    LOG_LEVEL = os.getenv(
        "LOG_LEVEL", "INFO"
    ).upper()  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    LOG_FILE = os.getenv(
        "LOG_FILE", ""
    )  # Optional: path to log file (empty = no file logging)
    LOG_COLORS = (
        os.getenv("LOG_COLORS", "true").lower() == "true"
    )  # Enable colored console output
    LLM_IO_LOG = os.getenv("LLM_IO_LOG", "")  # Optional: path to log LLM I/O as JSONL

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

{{"action": "search_tools", "capability": "<domain or service — e.g. web search, github api, file access>", "goal_updates": []}}

{{"action": "get_server_docs", "server_id": "some-server", "goal_updates": []}}

{{"action": "install_server", "server_id": "some-server", "goal_updates": []}}

{{"action": "configure_server", "server_id": "some-server", "config": {{"KEY": "value"}}, "goal_updates": []}}

{{"action": "dispatch", "tasks": [{{"server": "s", "tool": "t", "params": {{}}}}], "goal_updates": []}}

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

respond — Direct reply. Use for chat, greetings, or after a result comes back.
store — Remember a personal fact or preference.
recall — Recall stored facts by exact theme name.
search_memory — Search all memories by meaning. Use when you need context.
list_memory — List all stored memory themes.
{data_consent_note}
--- Tool use (multi-step) ---

search_tools — Find MCP servers by the domain or service you need.
  Describe WHAT YOU NEED (the domain/service), not HOW to implement it manually.
  The registry contains servers for every domain — web search, GitHub, email,
  music, databases, smart home, and more. It grows independently; never assume
  only installed servers exist.
  Shell is the last resort — only search for "shell commands" when the task is
  genuinely about running a script with no specialized alternative available.
  Good capability strings: "web search", "brave search", "github API",
    "read and write files", "execute shell commands"
  Bad:  "check python version"  ← too literal, not a domain
  Bad:  "execute shell commands"  ← when you actually need web search or a specific API

get_server_docs — Fetch full tool list for a server shown in SEARCH_RESULTS.
  Only use this on servers marked [INSTALLED].
  {{"action": "get_server_docs", "server_id": "<id from SEARCH_RESULTS>"}}

install_server — Install a server shown in SEARCH_RESULTS as [available].
  After install, SERVER_DOCS are provided automatically — no extra step needed.
  {{"action": "install_server", "server_id": "<id>"}}

uninstall_server — Remove an installed MCP server from the system.
  Use when the user asks to uninstall, remove, or delete an installed server.
  {{"action": "uninstall_server", "server_id": "<id>"}}

configure_server — Set required config values on an installed server.
  Use when SERVER_DOCS indicates "server requires configuration" (e.g. missing API key).
  If you do not know the required value, use respond to ask the user for it first.
  Never invent or guess API keys — always ask the user when the value is unknown.
  {{"action": "configure_server", "server_id": "<id>", "config": {{"KEY": "value"}}}}

dispatch — Execute tool calls. Only after seeing SERVER_DOCS.
  Use exact tool names and server id from SERVER_DOCS.
  ⚠ RULE — PARALLEL EXECUTION: Every task in one dispatch call starts at the same instant.
  Before batching two tasks, ask yourself: "Does task 2 need task 1 to finish first?"
  If YES → two separate dispatch calls. Get DISPATCH_RESULT from the first, then send the second.
  If NO → safe to batch.
  Common failure — whitelist/setup before use:
    WRONG (race): {{"action":"dispatch","tasks":[{{"tool":"add_to_whitelist",...}},{{"tool":"execute_command",...}}]}}
      execute_command starts BEFORE add_to_whitelist writes the entry → fails every time.
    CORRECT (sequential): dispatch [add_to_whitelist] → wait for success EXIT → dispatch [execute_command]
  Other sequential patterns: install → configure, create_file → read_file, grant_permission → use_resource.

--- Actions (exact format) ---

{{
    "action": "respond",
    "output": "<your message>",
    "goal_updates": [{{"id": "<goal_id>", "status": "completed", "result": "summary"}}]
}}

{{
    "action": "search_tools",
    "capability": "<capability needed, not user's words>",
    "goal_updates": []
}}

{{
    "action": "get_server_docs",
    "server_id": "<server id from SEARCH_RESULTS>",
    "goal_updates": []
}}

{{
    "action": "install_server",
    "server_id": "<server id from SEARCH_RESULTS>",
    "goal_updates": []
}}

{{
    "action": "configure_server",
    "server_id": "<server id>",
    "config": {{"KEY": "value"}},
    "goal_updates": []
}}

{{
    "action": "dispatch",
    "tasks": [{{"server": "<server_id>", "tool": "<tool_name>", "params": {{}}}}],
    "goal_updates": []
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

--- Context ---
You receive: GOALS (with IDs), NEW INPUT, SEARCH_RESULTS, SERVER_DOCS, DISPATCH_RESULT, WAIT_RESULT.
Memory results: STORE_RESULT, RECALL_RESULT, SEARCH_MEMORY_RESULT, LIST_MEMORY_RESULT.
RELEVANT MEMORIES may be included automatically (RAG).
Include goal_updates in respond: "completed" or "failed" with result.
DISPATCH_RESULT shows only the signals for YOUR CURRENT BATCH. EXIT signals in it confirm
success or failure. No EXIT yet means tasks are still running — dispatch again to re-check or
proceed only when you have a success EXIT.

--- Tool search rules ---
- SEARCH_RESULTS empty → retry search_tools with a different capability description.
- Make at least 2 genuinely different attempts before telling the user you cannot help.
- No installed server fits → use install_server, then dispatch using SERVER_DOCS.
- You may search for multiple servers in sequence for complex multi-tool tasks.

--- Memory guidelines ---
- Choose descriptive theme names (e.g. "user_preferences", "school_schedule")
- When storing, extract the key fact — be concise
- Use search_memory with natural language — it searches by meaning, not keywords
- If search results aren't relevant, try again with a higher offset to dig deeper

Output exactly one JSON object. First char {{, last char }}.
"""

    # Aliases used by component_factory
    LLM_ROOT_PROMPT_UNIFIED = LLM_ROOT_PROMPT
    LLM_ROOT_PROMPT_NO_CONTEXTOR = """\
You are JARVIS, a personal assistant. Route or respond. Output ONLY valid JSON — no text before or after.

OS: {system} {release} ({machine}), Shell: {shell}

--- When to use each action ---

respond — Direct reply. Use for chat, greetings, or after a result comes back.
Memory is disabled. Do not use store, recall, search_memory, or list_memory.

--- Tool use (multi-step) ---

search_tools — Find MCP servers by the domain or service you need.
  Describe WHAT YOU NEED (the domain/service), not HOW to implement it manually.
  The registry contains servers for every domain — web search, GitHub, email,
  music, databases, smart home, and more. It grows independently; never assume
  only installed servers exist.
  Shell is the last resort — only search for "shell commands" when the task is
  genuinely about running a script with no specialized alternative available.
  Good capability strings: "web search", "brave search", "github API",
    "read and write files", "execute shell commands"
  Bad:  "check python version"  ← too literal, not a domain
  Bad:  "execute shell commands"  ← when you actually need web search or a specific API

get_server_docs — Fetch full tool list for a server shown in SEARCH_RESULTS.
  Only use this on servers marked [INSTALLED].
  {{"action": "get_server_docs", "server_id": "<id from SEARCH_RESULTS>"}}

install_server — Install a server shown in SEARCH_RESULTS as [available].
  After install, SERVER_DOCS are provided automatically — no extra step needed.
  {{"action": "install_server", "server_id": "<id>"}}

configure_server — Set required config values on an installed server.
  {{"action": "configure_server", "server_id": "<id>", "config": {{"KEY": "value"}}}}

dispatch — Execute tool calls. Only after seeing SERVER_DOCS.
  ⚠ RULE — PARALLEL EXECUTION: Every task in one dispatch call starts at the same instant.
  Before batching two tasks, ask yourself: "Does task 2 need task 1 to finish first?"
  If YES → two separate dispatch calls. Get DISPATCH_RESULT from the first, then send the second.
  If NO → safe to batch.
  Common failure — whitelist/setup before use:
    WRONG (race): {{"action":"dispatch","tasks":[{{"tool":"add_to_whitelist",...}},{{"tool":"execute_command",...}}]}}
      execute_command starts BEFORE add_to_whitelist writes the entry → fails every time.
    CORRECT (sequential): dispatch [add_to_whitelist] → wait for success EXIT → dispatch [execute_command]
  Other sequential patterns: install → configure, create_file → read_file, grant_permission → use_resource.

--- Actions (exact format) ---

{{
    "action": "respond",
    "output": "<your message>",
    "goal_updates": [{{"id": "<goal_id>", "status": "completed", "result": "summary"}}]
}}

{{
    "action": "search_tools",
    "capability": "<capability needed, not user's words>",
    "goal_updates": []
}}

{{
    "action": "get_server_docs",
    "server_id": "<server id from SEARCH_RESULTS>",
    "goal_updates": []
}}

{{
    "action": "install_server",
    "server_id": "<server id from SEARCH_RESULTS>",
    "goal_updates": []
}}

{{
    "action": "configure_server",
    "server_id": "<server id>",
    "config": {{"KEY": "value"}},
    "goal_updates": []
}}

{{
    "action": "dispatch",
    "tasks": [{{"server": "<server_id>", "tool": "<tool_name>", "params": {{}}}}],
    "goal_updates": []
}}

--- Context ---
You receive: GOALS (with IDs), NEW INPUT, SEARCH_RESULTS, SERVER_DOCS, DISPATCH_RESULT, WAIT_RESULT.
Include goal_updates in respond: "completed" or "failed" with result.
DISPATCH_RESULT shows only the signals for YOUR CURRENT BATCH. EXIT signals in it confirm
success or failure. No EXIT yet means tasks are still running.

--- Tool search rules ---
- SEARCH_RESULTS empty → retry search_tools with a different capability description.
- Make at least 2 genuinely different attempts before telling the user you cannot help.
- No installed server fits → use install_server, then dispatch using SERVER_DOCS.

Output exactly one JSON object. First char {{, last char }}.
"""

    LLM_ROOT_PROMPT_UNIFIED_NO_CONTEXTOR = LLM_ROOT_PROMPT_NO_CONTEXTOR

    # ------------------------------------------------------------------
    # Dispatch mode: two variants selected per-turn by the system.
    #
    # JARVIS picks which prompt is active based on the current tool-
    # discovery backend (see DispatchAdapter.select_discovery_mode).
    # The LLM never sees the words "embedding", "semantic", "vector",
    # or "keyword" — it just follows whichever schema is in front of it.
    # ------------------------------------------------------------------

    LLM_DISPATCH_PROMPT_KEYWORD = """\
You are operating in DISPATCH mode. Your job is to find and execute MCP server tools.

CRITICAL: Your ENTIRE response must be a single valid JSON object.

--- Workflow ---
1. plan: ALWAYS start here. Break the intent into sub-tasks. For each sub-task,
   give a short intent and a few keywords the system can look up.
2. The system returns MATCHED_TOOLS (ready to dispatch) and/or
   CANDIDATE_SERVERS (need installation first).
3. If MATCHED_TOOLS is present → dispatch those tools with correct params.
4. If only CANDIDATE_SERVERS is present → install a promising one,
   then list_tools, then dispatch.
5. If nothing matched → search with different keywords, or done with a failure summary.
6. done: Return a concise summary to root.

--- Actions ---

Plan sub-tasks (ALWAYS start with this):
{{
    "action": "plan",
    "tasks": [
        {{"intent": "get weather forecast for Berlin", "keywords": ["weather", "forecast"]}},
        {{"intent": "convert 50 EUR to USD", "keywords": ["currency", "convert"]}}
    ]
}}

Search servers by keyword (use only if plan returned nothing useful):
{{"action": "search", "keywords": ["calculator", "math"]}}

List tools on an installed server:
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
- INTENT: What root asked you to do
- GOALS: Active goals
- MATCHED_TOOLS: Tools ready to dispatch — server_id/tool_name plus params schema
- CANDIDATE_SERVERS: Servers that look relevant but are NOT installed —
                     use install + list_tools to make them dispatchable
- TOOLS: Tool listings from a server (after list_tools)
- SEARCH_RESULTS: Server search results (after search)
- INSTALL_RESULT: Installation outcome
- DISPATCH_RESULT / DISPATCH_ERROR: Task execution results
- SIGNAL: Dispatch event (INIT, EXIT, REMIND, WAIT, KILL)

--- Signal types ---
- INIT: Task started (includes PID)
- EXIT: Task finished (includes output or error)
- REMIND: Task exceeded its reminder threshold
- WAIT: You previously chose to wait
- KILL: Task was terminated

--- Rules ---
- ALWAYS start with "plan" — even for a single task.
- If MATCHED_TOOLS is present, dispatch those. Never invent tool names.
- If only CANDIDATE_SERVERS is present, install + list_tools first.
- If nothing matched, try search with different keywords, or done with a failure summary.
- You can dispatch multiple tasks in one action for parallelism.
- Output exactly one JSON object — no preamble, no trailing text.

The very first character of your response must be {{ and the very last must be }}.
"""

    LLM_DISPATCH_PROMPT_EMBEDDING = """\
You are operating in DISPATCH mode. Your job is to find and execute MCP server tools.

CRITICAL: Your ENTIRE response must be a single valid JSON object.

--- Workflow ---
1. plan: ALWAYS start here. Break the intent into sub-tasks. For each sub-task,
   describe in natural language what it needs. Be specific — the clearer the
   intent, the better the matches.
2. The system returns MATCHED_TOOLS (ready to dispatch) and/or
   CANDIDATE_SERVERS (need installation first).
3. If MATCHED_TOOLS is present → dispatch those tools with correct params.
4. If only CANDIDATE_SERVERS is present → install a promising one,
   then list_tools, then dispatch.
5. If nothing matched → re-plan with more specific sub-task intents,
   or done with a failure summary.
6. done: Return a concise summary to root.

--- Actions ---

Plan sub-tasks (ALWAYS start with this):
{{
    "action": "plan",
    "tasks": [
        {{"intent": "get weather forecast for Berlin"}},
        {{"intent": "convert 50 EUR to USD"}}
    ]
}}

List tools on an installed server:
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
- INTENT: What root asked you to do
- GOALS: Active goals
- MATCHED_TOOLS: Tools ready to dispatch — server_id/tool_name plus params schema
- CANDIDATE_SERVERS: Servers that look relevant but are NOT installed —
                     use install + list_tools to make them dispatchable
- TOOLS: Tool listings from a server (after list_tools)
- INSTALL_RESULT: Installation outcome
- DISPATCH_RESULT / DISPATCH_ERROR: Task execution results
- SIGNAL: Dispatch event (INIT, EXIT, REMIND, WAIT, KILL)

--- Signal types ---
- INIT: Task started (includes PID)
- EXIT: Task finished (includes output or error)
- REMIND: Task exceeded its reminder threshold
- WAIT: You previously chose to wait
- KILL: Task was terminated

--- Rules ---
- ALWAYS start with "plan" — even for a single task.
- If MATCHED_TOOLS is present, dispatch those. Never invent tool names.
- If only CANDIDATE_SERVERS is present, install + list_tools first.
- If nothing matched, re-plan with more specific sub-task intents,
  or done with a failure summary.
- You can dispatch multiple tasks in one action for parallelism.
- Output exactly one JSON object — no preamble, no trailing text.

The very first character of your response must be {{ and the very last must be }}.
"""
