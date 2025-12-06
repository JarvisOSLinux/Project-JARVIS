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


def set_sudo_access(enabled: bool) -> None:
    """
    Enable or disable sudo access for jarvis user
    
    Args:
        enabled: True to enable sudo, False to disable
    """
    from .core.sudo_manager import enable_sudo, disable_sudo
    
    # Update config preference
    value = "true" if enabled else "false"
    _update_env_setting("JARVIS_SUDO_ENABLED", value)
    
    # Actually configure system sudo access
    if enabled:
        if enable_sudo():
            print("✓ Sudo access enabled for jarvis user")
        else:
            print("✗ Failed to enable sudo access. Please run with sudo: sudo jarvis sudo enable")
            sys.exit(1)
    else:
        if disable_sudo():
            print("✓ Sudo access disabled for jarvis user")
        else:
            print("✗ Failed to disable sudo access. Please run with sudo: sudo jarvis sudo disable")
            sys.exit(1)


def get_sudo_status() -> None:
    """Show current sudo access status"""
    from .core.sudo_manager import is_sudo_enabled, get_sudo_status as get_system_status
    
    enabled = is_sudo_enabled()
    config_preference = Config.JARVIS_SUDO_ENABLED
    
    print(f"Sudo access: {'enabled' if enabled else 'disabled'}")
    print(f"Config preference: {'enabled' if config_preference else 'disabled'}")
    
    if enabled != config_preference:
        print("⚠ Warning: System state differs from config preference")
        print("  Run 'jarvis sudo enable' or 'jarvis sudo disable' to sync")


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


def set_llm_model(model_name: str) -> None:
    """
    Update LLM_MODEL in .env file
    
    Args:
        model_name: Ollama model name (e.g., 'qwen2.5:7b', 'llama3.2:3b')
    """
    if not model_name or not model_name.strip():
        print("Error: Model name cannot be empty")
        sys.exit(1)
    
    model_name = model_name.strip()
    _update_env_setting("LLM_MODEL", model_name)
    print(f"✓ LLM model set to: {model_name}")
    print(f"  Note: Make sure the model is available: ollama pull {model_name}")


def get_llm_model() -> str:
    """Get current LLM model from config"""
    return Config.LLM_MODEL or "(not set)"


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
    print("  jarvis sudo enable         # Enable sudo access for jarvis user (requires root)")
    print("  jarvis sudo disable       # Disable sudo access for jarvis user (requires root)")
    print("  jarvis sudo               # Show current sudo access status")
    print("  jarvis model               # Show current LLM model")
    print("  jarvis model -n '<model>'  # Set LLM model (e.g., 'qwen2.5:7b')")
    print("  jarvis model set '<model>' # Set LLM model (alternative syntax)")
    print()
    print("Examples:")
    print("  jarvis                    # Start voice assistant")
    print("  jarvis text               # Switch to text output")
    print("  jarvis ask \"what is 2+2?\" # Ask a question")
    print("  jarvis history-reset off  # Maintain conversation context")
    print("  jarvis output-type        # Check current mode")


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
    
    elif command == "sudo":
        if len(sys.argv) == 2:
            # Show current status
            get_sudo_status()
        elif len(sys.argv) == 3:
            # Set new value
            value = sys.argv[2].lower()
            if value in ["on", "true", "1", "yes", "enable"]:
                set_sudo_access(True)
            elif value in ["off", "false", "0", "no", "disable"]:
                set_sudo_access(False)
            else:
                print(f"Error: Invalid value '{value}'. Use 'enable' or 'disable'")
                print("Usage: jarvis sudo [enable|disable]")
                sys.exit(1)
        else:
            print("Usage: jarvis sudo [enable|disable]")
            sys.exit(1)
    
    elif command == "model":
        if len(sys.argv) == 2:
            # Show current model
            model = get_llm_model()
            print(f"Current LLM model: {model}")
            if model != "(not set)":
                print(f"  To change: jarvis model -n '<new_model>'")
                print(f"  To verify model exists: ollama list")
        elif len(sys.argv) == 3:
            # Handle: jarvis model set or jarvis model -n (missing value)
            if sys.argv[2] == "set" or sys.argv[2] == "-n" or sys.argv[2] == "--name":
                print("Error: Model name required")
                print("Usage: jarvis model -n '<model_name>'")
                print("   or: jarvis model set '<model_name>'")
                sys.exit(1)
            else:
                # Treat as model name (backward compatibility: jarvis model <model>)
                set_llm_model(sys.argv[2])
        elif len(sys.argv) == 4:
            # Handle: jarvis model -n '<model>' or jarvis model set '<model>'
            if sys.argv[2] == "-n" or sys.argv[2] == "--name":
                set_llm_model(sys.argv[3])
            elif sys.argv[2] == "set":
                set_llm_model(sys.argv[3])
            else:
                print(f"Error: Unknown option '{sys.argv[2]}'")
                print("Usage: jarvis model -n '<model_name>'")
                print("   or: jarvis model set '<model_name>'")
                sys.exit(1)
        else:
            print("Usage: jarvis model [-n '<model_name>'|set '<model_name>']")
            print("   or: jarvis model  (to show current model)")
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
        
    elif command in ["-h", "--help", "help"]:
        show_usage()
        
    else:
        print(f"Error: Unknown command '{command}'")
        print()
        show_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()

