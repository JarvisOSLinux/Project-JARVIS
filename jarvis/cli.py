"""
CLI interface for JARVIS AI Assistant

Usage:
    jarvis                    # Start voice activation mode
    jarvis text               # Set text output mode
    jarvis voice              # Set voice output mode
    jarvis ask "<message>"    # Ask a question
    jarvis output-type        # Show current output mode
"""

import sys
import os
from pathlib import Path
from .config import Config
from .core.logger import get_logger

logger = get_logger(__name__)

ENV_FILE = Path(__file__).parent / ".env"


def set_output_mode(mode: str) -> None:
    """
    Update OUTPUT_MODE in .env file
    
    Args:
        mode: 'text' or 'voice'
    """
    if mode not in ["text", "voice"]:
        print(f"Error: Invalid mode '{mode}'. Must be 'text' or 'voice'")
        sys.exit(1)
    
    _update_env_setting("OUTPUT_MODE", mode)
    print(f"✓ Output mode set to: {mode}")


def set_history_reset(enabled: bool) -> None:
    """
    Update RESET_HISTORY_AFTER_RESPONSE in .env file
    
    Args:
        enabled: True to reset history, False to maintain context
    """
    value = "true" if enabled else "false"
    _update_env_setting("RESET_HISTORY_AFTER_RESPONSE", value)
    print(f"✓ History reset {'enabled' if enabled else 'disabled'}")


def _update_env_setting(key: str, value: str) -> None:
    """
    Update a setting in .env file
    
    Args:
        key: Environment variable name
        value: New value
    """
    # Read current .env or config.env.template
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()
    else:
        # If .env doesn't exist, try template
        template_file = Path(__file__).parent / "config.env.template"
        if template_file.exists():
            logger.info(f"Creating .env from template...")
            lines = template_file.read_text().splitlines()
        else:
            lines = []
    
    # Update or add setting
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}=") or line.startswith(f"#{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    
    if not found:
        lines.append(f"{key}={value}")
    
    # Write back to .env
    ENV_FILE.write_text("\n".join(lines) + "\n")
    
    # Update current process environment
    os.environ[key] = value
    setattr(Config, key, value)


def get_output_mode() -> str:
    """Get current output mode from config"""
    return Config.OUTPUT_MODE


def set_llm_provider(provider: str) -> None:
    """
    Update LLM_PROVIDER in .env file
    
    Args:
        provider: 'ollama' or 'api'
    """
    if provider not in ["ollama", "api"]:
        print(f"Error: Invalid provider '{provider}'. Must be 'ollama' or 'api'")
        sys.exit(1)
    
    _update_env_setting("LLM_PROVIDER", provider)
    print(f"✓ LLM provider set to: {provider}")


def set_llm_model(model: str) -> None:
    """
    Update LLM_MODEL in .env file
    
    Args:
        model: Model name/identifier
    """
    if not model:
        print("Error: Model name cannot be empty")
        sys.exit(1)
    
    _update_env_setting("LLM_MODEL", model)
    print(f"✓ LLM model set to: {model}")


def set_api_url(url: str) -> None:
    """
    Update LLM_API_URL in .env file
    
    Args:
        url: API base URL
    """
    if not url:
        print("Error: API URL cannot be empty")
        sys.exit(1)
    
    _update_env_setting("LLM_API_URL", url)
    print(f"✓ API URL set to: {url}")


def set_api_key(key: str) -> None:
    """
    Update LLM_API_KEY in .env file
    
    Args:
        key: API key
    """
    if not key:
        print("Error: API key cannot be empty")
        sys.exit(1)
    
    _update_env_setting("LLM_API_KEY", key)
    print(f"✓ API key set")


def set_ollama_url(url: str) -> None:
    """
    Update LLM_OLLAMA_URL in .env file
    
    Args:
        url: Ollama base URL
    """
    if not url:
        print("Error: Ollama URL cannot be empty")
        sys.exit(1)
    
    _update_env_setting("LLM_OLLAMA_URL", url)
    print(f"✓ Ollama URL set to: {url}")


def show_llm_config() -> None:
    """Display current LLM configuration"""
    print("Current LLM Configuration:")
    print("=" * 40)
    print(f"Provider: {getattr(Config, 'LLM_PROVIDER', 'ollama')}")
    print(f"Model: {getattr(Config, 'LLM_MODEL', 'Not set')}")
    
    provider = getattr(Config, 'LLM_PROVIDER', 'ollama')
    if provider == 'ollama':
        ollama_url = getattr(Config, 'LLM_OLLAMA_URL', 'http://localhost:11434')
        auto_pull = getattr(Config, 'LLM_AUTO_PULL', False)
        print(f"Ollama URL: {ollama_url}")
        print(f"Auto-pull: {'enabled' if auto_pull else 'disabled'}")
    elif provider == 'api':
        api_url = getattr(Config, 'LLM_API_URL', 'Not set')
        api_key = getattr(Config, 'LLM_API_KEY', None)
        print(f"API URL: {api_url}")
        if api_key:
            # Mask the key for security
            masked_key = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
            print(f"API Key: {masked_key}")
        else:
            print("API Key: Not set")


def show_usage() -> None:
    """Display usage information"""
    print("JARVIS AI Assistant - CLI Interface")
    print()
    print("Usage:")
    print("  jarvis                    # Start voice activation mode")
    print("  jarvis text               # Set text output mode")
    print("  jarvis voice              # Set voice output mode")
    print("  jarvis ask \"<message>\"    # Ask a question")
    print("  jarvis output-type        # Show current output mode")
    print("  jarvis history-reset on   # Enable history reset after each response")
    print("  jarvis history-reset off  # Disable history reset (maintain context)")
    print("  jarvis history-reset      # Show current history reset setting")
    print()
    print("LLM Configuration:")
    print("  jarvis provider <ollama|api>  # Set LLM provider")
    print("  jarvis model <model_name>     # Set LLM model")
    print("  jarvis api-url <url>          # Set API base URL (for API provider)")
    print("  jarvis api-key <key>          # Set API key (for API provider)")
    print("  jarvis ollama-url <url>       # Set Ollama URL (for Ollama provider)")
    print("  jarvis auto-pull on/off       # Enable/disable auto-pull missing models")
    print("  jarvis auto-pull              # Show current auto-pull setting")
    print("  jarvis llm-config             # Show current LLM configuration")
    print()
    print("Examples:")
    print("  jarvis                    # Start voice assistant")
    print("  jarvis text               # Switch to text output")
    print("  jarvis ask \"what is 2+2?\" # Ask a question")
    print("  jarvis history-reset off  # Maintain conversation context")
    print("  jarvis output-type        # Check current mode")
    print("  jarvis provider api       # Switch to API provider")
    print("  jarvis model gpt-4        # Set model to GPT-4")
    print("  jarvis api-url https://api.openai.com  # Set OpenAI API URL")
    print("  jarvis api-key sk-...     # Set API key")
    print("  jarvis llm-config         # Show LLM settings")


def main() -> None:
    """Main CLI entry point"""
    # Import here to avoid circular imports and to delay heavy imports
    from .main import Jarvis
    
    # No arguments - start voice activation
    if len(sys.argv) == 1:
        print("Starting JARVIS in voice activation mode...")
        jarvis = Jarvis()
        jarvis.listen_with_activation()
        return
    
    # Parse command
    command = sys.argv[1]
    
    if command == "text":
        set_output_mode("text")
        
    elif command == "voice":
        set_output_mode("voice")
        
    elif command == "output-type":
        mode = get_output_mode()
        print(f"Current output mode: {mode}")
        
    elif command == "history-reset":
        if len(sys.argv) == 2:
            # Show current setting
            enabled = Config.RESET_HISTORY_AFTER_RESPONSE
            print(f"History reset: {'enabled' if enabled else 'disabled'}")
        elif len(sys.argv) == 3:
            # Set new value
            value = sys.argv[2].lower()
            if value in ["on", "true", "1", "yes", "enable"]:
                set_history_reset(True)
            elif value in ["off", "false", "0", "no", "disable"]:
                set_history_reset(False)
            else:
                print(f"Error: Invalid value '{value}'. Use 'on' or 'off'")
                sys.exit(1)
        else:
            print("Usage: jarvis history-reset [on|off]")
            sys.exit(1)
        
    elif command == "ask":
        if len(sys.argv) < 3:
            print("Error: Message required")
            print("Usage: jarvis ask \"<message>\"")
            sys.exit(1)
        
        # Combine all remaining arguments as the message
        message = " ".join(sys.argv[2:])
        
        # Initialize JARVIS in text mode (skip voice components)
        jarvis = Jarvis(text_mode=True)
        # ask() now handles output based on Config.OUTPUT_MODE
        jarvis.ask(message)
    
    elif command == "provider":
        if len(sys.argv) < 3:
            print("Error: Provider type required")
            print("Usage: jarvis provider <ollama|api>")
            sys.exit(1)
        set_llm_provider(sys.argv[2])
    
    elif command == "model":
        if len(sys.argv) < 3:
            print("Error: Model name required")
            print("Usage: jarvis model <model_name>")
            sys.exit(1)
        set_llm_model(sys.argv[2])
    
    elif command == "api-url":
        if len(sys.argv) < 3:
            print("Error: API URL required")
            print("Usage: jarvis api-url <url>")
            sys.exit(1)
        set_api_url(sys.argv[2])
    
    elif command == "api-key":
        if len(sys.argv) < 3:
            print("Error: API key required")
            print("Usage: jarvis api-key <key>")
            sys.exit(1)
        set_api_key(sys.argv[2])
    
    elif command == "ollama-url":
        if len(sys.argv) < 3:
            print("Error: Ollama URL required")
            print("Usage: jarvis ollama-url <url>")
            sys.exit(1)
        set_ollama_url(sys.argv[2])
    
    elif command == "llm-config":
        show_llm_config()
    
    elif command == "auto-pull":
        if len(sys.argv) == 2:
            # Show current setting
            auto_pull = getattr(Config, 'LLM_AUTO_PULL', False)
            print(f"Auto-pull missing models: {'enabled' if auto_pull else 'disabled'}")
        elif len(sys.argv) == 3:
            # Set new value
            value = sys.argv[2].lower()
            if value in ["on", "true", "1", "yes", "enable"]:
                _update_env_setting("LLM_AUTO_PULL", "true")
                print("✓ Auto-pull enabled")
            elif value in ["off", "false", "0", "no", "disable"]:
                _update_env_setting("LLM_AUTO_PULL", "false")
                print("✓ Auto-pull disabled")
            else:
                print(f"Error: Invalid value '{value}'. Use 'on' or 'off'")
                sys.exit(1)
        else:
            print("Usage: jarvis auto-pull [on|off]")
            sys.exit(1)
        
    elif command in ["-h", "--help", "help"]:
        show_usage()
        
    else:
        print(f"Error: Unknown command '{command}'")
        print()
        show_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()

