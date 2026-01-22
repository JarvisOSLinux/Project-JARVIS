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
    print("Examples:")
    print("  jarvis                    # Start voice assistant")
    print("  jarvis text               # Switch to text output")
    print("  jarvis ask \"what is 2+2?\" # Ask a question")
    print("  jarvis history-reset off  # Maintain conversation context")
    print("  jarvis output-type        # Check current mode")


def _check_capabilities() -> dict:
    """
    Check system capabilities for voice features
    
    Returns:
        Dictionary with capability flags
    """
    capabilities = {
        'voice_input': False,
        'voice_output': False,
    }
    
    try:
        from .core.audio_detection import check_audio_input_available, check_audio_output_available
        capabilities['voice_input'] = check_audio_input_available()
        capabilities['voice_output'] = check_audio_output_available()
    except Exception as e:
        logger.debug(f"Error checking capabilities: {e}")
    
    return capabilities


def main() -> None:
    """Main CLI entry point"""
    # Import here to avoid circular imports and to delay heavy imports
    from .main import Jarvis
    
    # No arguments - start voice activation
    if len(sys.argv) == 1:
        print("Starting JARVIS in voice activation mode...")
        
        # Check capabilities and warn if unavailable
        capabilities = _check_capabilities()
        if not capabilities['voice_input']:
            print("⚠️  Warning: No audio input devices detected.")
            print("   Voice activation will not work. Use 'jarvis ask' for text mode.")
            print()
        
        try:
            jarvis = Jarvis()
            if not jarvis.voice_manager:
                print("⚠️  Voice manager unavailable. Falling back to text-only mode.")
                print("   Use 'jarvis ask \"<message>\"' to interact via text.")
                sys.exit(1)
            jarvis.listen_with_activation()
        except Exception as e:
            logger.error(f"Failed to start JARVIS: {e}")
            print(f"\n❌ Error: {e}")
            print("\nTip: Use 'jarvis ask \"<message>\"' for text-only mode.")
            sys.exit(1)
        return
    
    # Parse command
    command = sys.argv[1]
    
    if command == "text":
        set_output_mode("text")
        
    elif command == "voice":
        # Check if voice output is available
        capabilities = _check_capabilities()
        if not capabilities['voice_output']:
            print("⚠️  Warning: No audio output devices detected.")
            print("   Voice output will fallback to text mode.")
            response = input("Continue anyway? (y/n): ").strip().lower()
            if response not in ['y', 'yes']:
                print("Cancelled.")
                sys.exit(0)
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
        
    elif command in ["-h", "--help", "help"]:
        show_usage()
        
    else:
        print(f"Error: Unknown command '{command}'")
        print()
        show_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()

