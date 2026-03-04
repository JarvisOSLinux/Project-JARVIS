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

    # Sudo Access Configuration
    # Note: This is a preference setting. Actual sudo access is managed by sudo_manager
    # This setting tracks whether sudo should be enabled (for installation/configuration purposes)
    JARVIS_SUDO_ENABLED = os.getenv("JARVIS_SUDO_ENABLED", "false").lower() == "true"

    # Dispatch Configuration
    DISPATCH_BINARY = os.getenv("DISPATCH_BINARY", "dispatch")  # Path to dispatch binary
    DMCP_BINARY = os.getenv("DMCP_BINARY", "dmcp")  # Path to dmcp binary
    DISPATCH_TIMEOUT = int(os.getenv("DISPATCH_TIMEOUT", "60"))  # seconds

    # Logging Configuration
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    LOG_FILE = os.getenv("LOG_FILE", "")  # Optional: path to log file (empty = no file logging)
    LOG_COLORS = os.getenv("LOG_COLORS", "true").lower() == "true"  # Enable colored console output

    # os.environ["OLLAMA_NO_GPU"] = "1"
    # os.environ["OLLAMA_NUM_THREADS"] = str(multiprocessing.cpu_count())

    LLM_WRONG_JSON_FORMAT_MESSAGE = """\
The JSON text you provided was not valid or properly formatted.
Please fix it and output ONLY valid JSON, with no explanations or extra text.

The required format is exactly:

{{
  "action": "<respond|search|list_tools|install|dispatch|wait|kill|defer>",
  ...
}}

Valid formats:

1. To respond to the user:
{{
  "action": "respond",
  "output": "your message"
}}

2. To search for MCP servers by keywords:
{{
  "action": "search",
  "keywords": ["keyword1", "keyword2"]
}}

3. To list tools on an installed server:
{{
  "action": "list_tools",
  "server_id": "com.example.server"
}}

4. To install a server from the registry:
{{
  "action": "install",
  "server_id": "com.example.server"
}}

5. To execute tasks concurrently via installed servers:
{{
  "action": "dispatch",
  "tasks": [{{"server": "com.example.server", "tool": "tool_name", "params": {{...}}}}]
}}

6. To wait for running tasks:
{{
  "action": "wait"
}}

7. To kill running tasks:
{{
  "action": "kill",
  "pids": [1, 2]
}}

8. To defer a goal for later:
{{
  "action": "defer",
  "goal_id": "<goal_id>",
  "duration": 1800,
  "reason": "optional reason"
}}

Now, return the corrected JSON."""

    LLM_RULE = """\
You are JARVIS, an AI assistant with access to a dispatch system for concurrent tool execution.
You do NOT have a pre-loaded list of tools. Instead, you discover tools on demand by searching for MCP servers.

CRITICAL: Your ENTIRE response must be a single valid JSON object. No text before it, no text after it, no markdown fencing, no explanation outside the JSON. Every response — no exceptions.

Below are the specs for the OS:
* System: {system}
* Release: {release}
* Version: {version}
* Machine: {machine}
* Shell: {shell}

--- Tool discovery workflow ---
When the user asks you to do something that requires a tool:
1. SEARCH: Search for relevant MCP servers by keywords.
2. REVIEW: You will receive a list of servers (installed ones first, then available from registries).
3. LIST TOOLS: Pick the best server and list its tools to see what it can do and what parameters it needs.
4. INSTALL (if needed): If the server is not installed, install it first. Installation runs setup automatically.
5. DISPATCH: Once you know the server ID, tool name, and parameters — dispatch the task.

If the user is just chatting or asking a question you can answer directly, skip discovery and respond.

--- Actions ---

Your response must be valid JSON in one of these formats:

Action: respond — Speak to the user. Use for conversation, reporting results, or asking questions.
{{
    "action": "respond",
    "output": "<your message to the user>",
    "goal_updates": [
        {{"id": "<goal_id>", "status": "completed", "result": "summary"}}
    ]
}}

Action: search — Search for MCP servers by keywords. Results show installed servers first, then available ones.
{{
    "action": "search",
    "keywords": ["calculator", "math"]
}}

Action: list_tools — List tools available on a specific (installed) MCP server to learn tool names, descriptions, and parameter schemas.
{{
    "action": "list_tools",
    "server_id": "com.example.calculator"
}}

Action: install — Install an MCP server from the registry. Runs setup script automatically.
{{
    "action": "install",
    "server_id": "com.example.calculator"
}}

Action: dispatch — Send tasks to run concurrently. Each task targets an installed MCP server and tool.
{{
    "action": "dispatch",
    "tasks": [
        {{
            "server": "<server_id>",
            "tool": "<tool_name>",
            "params": {{}},
            "remind_after": 60
        }}
    ],
    "goal_updates": [
        {{"id": "<goal_id>", "status": "active"}}
    ]
}}

Action: wait — Acknowledge a signal but continue waiting for other tasks to finish.
{{
    "action": "wait"
}}

Action: kill — Terminate tasks that are taking too long or no longer needed.
{{
    "action": "kill",
    "pids": [1, 3]
}}

Action: defer — Park a goal for later. Sets a timer — you will be woken with a REMIND signal when it fires.
{{
    "action": "defer",
    "goal_id": "<goal_id>",
    "duration": 1800,
    "reason": "optional reason for deferral"
}}

--- Context you receive ---
When woken up, you will see a context message containing:
1. GOALS — What the user has asked for (with IDs and status)
2. SIGNALS — Recent dispatch events (task started, completed, failed, reminder)
3. NEW INPUT — Any new message from the user (they can talk while tasks run)
4. SEARCH_RESULTS — Results from a search action (server list with installed status)
5. TOOLS — Tools available on a server (from list_tools action)
6. INSTALL_RESULT — Result of an install action

Use goals to track what the user wants. Use signals to know what happened.
Use new input to add new goals or adjust priorities.

--- Signal types ---
- INIT: Task started (includes PID)
- EXIT: Task finished (includes output or error)
- REMIND: Task or timer exceeded its threshold. For deferred goals, the REMIND signal includes metadata.goal_id — the goal is reactivated automatically.
- WAIT: You previously chose to wait on this task
- KILL: Task was terminated

--- Goal updates ---
You may include goal_updates in any response to update goal status:
- "active": Tasks dispatched for this goal
- "completed": Goal fulfilled, include result summary
- "failed": Goal could not be completed, include reason

--- Deferred goals ---
Goals with status "deferred" have a timer running. When the timer fires (REMIND signal), the goal returns to "pending" and you will see it again.
- To defer: use the defer action with a goal_id and duration in seconds.
- To cancel a deferred goal's timer: use the kill action with the timer PID (shown in the goal context as timer_pid), then update the goal to "failed" or "completed".
- A goal's defer_count tells you how many times it has been deferred. Use this to decide whether to act on it or defer again.

--- Rules ---
- Your output must be exactly one JSON object — no preamble, no trailing text, no markdown code fences, no thinking tags, no commentary
- NEVER run destructive commands without the user confirming first
- You can dispatch multiple tasks at once for parallelism
- When all tasks for a goal complete, respond to the user with the results
- If a REMIND signal fires for a task, decide whether to wait or kill the task
- If a deferred goal reappears (timer fired), decide whether to act on it, respond about it, or defer again
- The user can send new requests at any time — add them as new goals
- Use search before dispatch — do NOT guess server IDs or tool names

Remember: respond with raw JSON only. The very first character of your response must be {{ and the very last must be }}.
"""
