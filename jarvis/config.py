from dotenv import load_dotenv
# import multiprocessing
import os

# Load .env file from the jarvis directory
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

class Config:
    # Vosk STT Configuration
    VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15")
    
    # LLM Configuration
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")  # "ollama" or "api"
    LLM_MODEL = os.getenv("LLM_MODEL")
    
    # Ollama-specific configuration
    LLM_OLLAMA_URL = os.getenv("LLM_OLLAMA_URL", "http://localhost:11434")  # Optional custom Ollama URL
    LLM_AUTO_PULL = os.getenv("LLM_AUTO_PULL", "false").lower() == "true"  # Auto-pull missing models
    
    # API-based LLM configuration (for OpenAI, Claude, OpenRouter, custom servers)
    LLM_API_URL = os.getenv("LLM_API_URL")  # Base URL for API endpoint (e.g., https://api.openai.com)
    LLM_API_KEY = os.getenv("LLM_API_KEY")  # API key for authentication
    LLM_API_HEADERS = os.getenv("LLM_API_HEADERS")  # Optional custom headers (JSON string)

    TTS_MODEL_ONNX = os.getenv("TTS_MODEL_ONNX")
    TTS_MODEL_JSON = os.getenv("TTS_MODEL_JSON")
    
    # Voice Activation Configuration
    WAKE_WORDS = os.getenv("WAKE_WORDS", "jarvis,hey jarvis,okay jarvis").split(",")
    VOICE_ACTIVATION_SENSITIVITY = float(os.getenv("VOICE_ACTIVATION_SENSITIVITY", "0.8"))
    
    # CLI Output Mode Configuration
    OUTPUT_MODE = os.getenv("OUTPUT_MODE", "voice")  # voice or text
    
    # Conversation History Configuration
    RESET_HISTORY_AFTER_RESPONSE = os.getenv("RESET_HISTORY_AFTER_RESPONSE", "true").lower() == "true"

    # SuperMCP Configuration
    SUPERMCP_SERVER_PATH = os.getenv("SUPERMCP_SERVER_PATH", "SuperMCP/SuperMCP.py")
    SUPERMCP_TIMEOUT = int(os.getenv("SUPERMCP_TIMEOUT", "60"))  # seconds
    
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

{
  "user_request": "<Conversation|SuperMCP>",
  "output": "<string>"
}

Here is your previous response:
<insert the bad JSON here>

Now, return the corrected JSON."""

    LLM_RULE = """\
Below are the specs for the OS:
* System: {system}
* Release: {release}
* Version: {version}
* Machine: {machine}

You have access to a SuperMCP system that provides dynamic tool discovery and usage through specialized MCP servers.

Instructions:
You are expected to provide ALL of your responses in JSON text format:
{{
    "user_request": "",
    "output": ""
}}

Step 1 - Identify request type:
Given prompt from the user, first identify whether the user is trying to have a conversation or needs tool execution.

If the user is trying to have a conversation, respond EXACTLY in this format:
{{
    "user_request": "Conversation",
    "output": "<your response to the user's prompt>"
}}

If the user needs tool execution (commands, file operations, code analysis, etc.), respond EXACTLY in this format:
{{
    "user_request": "SuperMCP",
    "output": "<SuperMCP command sequence>"
}}

Step 2 - SuperMCP Command Format:
When user_request == "SuperMCP", the output should be a sequence of SuperMCP commands separated by semicolons (;).

Available SuperMCP commands:
- reload_servers() - Refresh available MCP servers
- list_servers() - List all available servers
- inspect_server(<server_name>) - Get server capabilities
- call_server_tool(<server_name>, <tool_name>, <arguments>) - Execute a tool

Example SuperMCP sequences:
- "reload_servers(); list_servers()"
- "call_server_tool(CodeAnalysisMCP, initialize_repository, {{path: '/path/to/repo'}})"
- "call_server_tool(ShellMCP, run_command, {{command: 'ls -la'}})"

Step 3 - Processing SuperMCP results:
After SuperMCP execution, you will receive the results. If the results indicate success and the user's request is fulfilled, return:
{{
    "user_request": "Conversation",
    "output": "<short confirmation + essential results>"
}}

If more steps are needed, continue with another SuperMCP command sequence.

Step 4 - Error handling:
If SuperMCP commands fail, analyze the error and either:
- Try alternative commands or servers
- Ask for clarification via "Conversation"
- Explain the limitation to the user

Additional rules:
- ALWAYS return valid JSON only (no extra text, no markdown).
- NEVER run destructive commands without explicit confirmation.
- Use absolute paths when possible.
- Prefer SuperMCP tools over direct shell commands when available.
- Always reload_servers() first to ensure you have the latest available tools.
"""

