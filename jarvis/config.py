from dotenv import load_dotenv
# import multiprocessing
import os
import sys

# Load .env file from the jarvis directory
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

class Config:
    # Vosk STT Configuration
    VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15")
    
    # LLM Configuration
    LLM_MODEL = os.getenv("LLM_MODEL")
    
    # Validate required configuration
    @classmethod
    def validate(cls):
        """Validate that required configuration values are set"""
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        template_path = os.path.join(os.path.dirname(__file__), 'config.env.template')
        
        if not cls.LLM_MODEL:
            error_msg = f"\n{'='*70}\n"
            error_msg += "ERROR: LLM_MODEL is not configured!\n\n"
            
            if not os.path.exists(env_path):
                error_msg += f"The configuration file is missing: {env_path}\n\n"
                error_msg += f"Please copy the template file to create your configuration:\n"
                error_msg += f"  cp {template_path} {env_path}\n\n"
                error_msg += "Then edit the .env file and set your LLM_MODEL.\n"
            else:
                error_msg += f"The .env file exists but LLM_MODEL is not set.\n"
                error_msg += f"Please edit {env_path} and set LLM_MODEL.\n\n"
            
            error_msg += "Example: LLM_MODEL=llama3.2:3b\n"
            error_msg += "\nSee config.env.template for more examples.\n"
            error_msg += f"{'='*70}\n"
            print(error_msg, file=sys.stderr)
            sys.exit(1)

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

Step 2 - SuperMCP Quick Reference:
Available commands (separated by semicolons):
- reload_servers() - Refresh available MCP servers  
- list_servers() - List all available servers
- call_server_tool(<server>, <tool>, {{<params>}}) - Execute a tool

Step 3 - Common Tool Examples:
For file operations, use FileSystemMCP:
- List files: "call_server_tool(FileSystemMCP, list_directory, {{directory_path: '.'}})"
- Read file: "call_server_tool(FileSystemMCP, read_text_file_tool, {{file_path: 'path/to/file.txt'}})"
- Write file: "call_server_tool(FileSystemMCP, write_text_file_tool, {{file_path: 'file.txt', content: 'text'}})"

For shell commands, use ShellMCP:
- Execute command: "call_server_tool(ShellMCP, execute_command, {{command: 'dir'}})"
- Get platform: "call_server_tool(ShellMCP, get_platform_info, {{}})"
- If a command fails with "not whitelisted":
  1. Ask the user if they want to add it: "The command 'X' is not whitelisted. Would you like me to add it?"
  2. If user approves, add it: "call_server_tool(ShellMCP, add_to_whitelist, {{command: 'X', securityLevel: 'safe', description: '...'}})"
  3. Then RETRY the original command immediately
- Security levels: 'safe' (runs immediately), 'requires_approval' (asks user), 'forbidden' (blocks)
- For read-only commands like 'python --version', use 'safe' level

For code generation, use CodeGenMCP:
- Create MCP server: "call_server_tool(CodeGenMCP, create_mcp_server, {{server_name: 'WeatherMCP', description: 'Weather tools', tools_description: 'get_weather(city, country) returns temp and conditions'}})"
- Generate function: "call_server_tool(CodeGenMCP, generate_python_function, {{function_name: 'parse_data', function_description: 'Parse JSON data', parameters: 'data: str'}})"
- List templates: "call_server_tool(CodeGenMCP, list_available_templates, {{}})"

For code analysis, use CodeAnalysisMCP:
- Initialize repo: "call_server_tool(CodeAnalysisMCP, initialize_repository, {{path: '/path'}})"

Step 4 - Processing Results:
You will receive CLEAR TEXT feedback like:
"[OK] Reloaded 4 MCP servers"
"[OK] Available servers: FileSystemMCP, ShellMCP, ..."
"[SUCCESS] Listed directory: Files: file1.txt, file2.py..."

When you see "[SUCCESS]" or "TASK COMPLETE", immediately return a Conversation response with the results.
Do NOT repeat the same command - the task is done!

Step 5 - Error Handling:
If you see "Error: Field required: <parameter_name>", retry with the correct parameter name.
If you see "Tool not found", try a different tool or ask for clarification.

Step 6 - Response after success:
{{
    "user_request": "Conversation",
    "output": "<summarize the successful results to the user>"
}}

Additional rules:
- ALWAYS return valid JSON only (no extra text, no markdown)
- Use the exact parameter names from the examples above
- NEVER run destructive commands (delete, move, write) without explicit confirmation
- For file paths: use '.' for current directory, or provide full paths
- When you get successful results with "[SUCCESS]" or "TASK COMPLETE", immediately return Conversation
- Don't repeat successful operations - if something worked, you're done!
"""

