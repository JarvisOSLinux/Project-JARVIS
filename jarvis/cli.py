"""
CLI interface for JARVIS AI Assistant

Usage:
    jarvis                    # Start dual-input mode (voice + socket, or chat)
    jarvis run                # Same as jarvis — event loop with voice + socket
    jarvis tui                # Interactive TUI (OpenClaw-style, needs [tui] extra)
    jarvis chat               # Interactive text chat (stdin only)
    jarvis send "<message>"   # Send message to running jarvis (via socket)
    jarvis ask "<message>"    # Ask a single question (one-shot, no daemon)
    jarvis text               # Set text output mode
    jarvis voice              # Set voice output mode
    jarvis output-type        # Show current output mode
"""

import socket
import sys
import os
import asyncio
from pathlib import Path
from .config import Config
from .core.logger import get_logger

logger = get_logger(__name__)

ENV_FILE = Path(__file__).parent / ".env"


def _cmd_send() -> None:
    """Send a message to a running JARVIS instance via Unix socket."""
    if len(sys.argv) < 3:
        print("Usage: jarvis send <message>")
        print("  Sends the message to a running JARVIS instance.")
        print("  Start JARVIS with 'jarvis' or 'jarvis run' first.")
        sys.exit(1)
    msg = " ".join(sys.argv[2:]).strip()
    if not msg:
        print("Error: Message cannot be empty")
        sys.exit(1)
    candidates = [Config.JARVIS_INPUT_SOCKET, "/run/jarvis/input.sock"]
    path = None
    for p in candidates:
        if p and os.path.exists(p):
            path = p
            break
    if not path:
        print("Error: JARVIS socket not found.")
        print("  Tried:", ", ".join(p for p in candidates if p))
        print("  Is JARVIS running? Start with 'jarvis' or 'jarvis run'.")
        sys.exit(1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(path)
            sock.sendall((msg + "\n").encode("utf-8"))
        print("Sent.")
    except (socket.error, OSError) as e:
        print(f"Error: Could not send to JARVIS: {e}")
        sys.exit(1)


def set_output_mode(mode: str) -> None:
    """
    Update OUTPUT_MODE in .env file.

    Args:
        mode: 'text' or 'voice'
    """
    if mode not in ["text", "voice"]:
        print(f"Error: Invalid mode '{mode}'. Must be 'text' or 'voice'")
        sys.exit(1)

    _update_env_setting("OUTPUT_MODE", mode)
    print(f"Output mode set to: {mode}")


def set_history_reset(enabled: bool) -> None:
    """
    Update RESET_HISTORY_AFTER_RESPONSE in .env file.

    Args:
        enabled: True to reset history, False to maintain context
    """
    value = "true" if enabled else "false"
    _update_env_setting("RESET_HISTORY_AFTER_RESPONSE", value)
    print(f"History reset {'enabled' if enabled else 'disabled'}")


def set_sudo_access(enabled: bool) -> None:
    """
    Enable or disable sudo access for jarvis user.

    Args:
        enabled: True to enable sudo, False to disable
    """
    from .core.sudo_manager import enable_sudo, disable_sudo

    value = "true" if enabled else "false"
    _update_env_setting("JARVIS_SUDO_ENABLED", value)

    if enabled:
        if enable_sudo():
            print("Sudo access enabled for jarvis user")
        else:
            print("Failed to enable sudo access. Please run with sudo: sudo jarvis sudo enable")
            sys.exit(1)
    else:
        if disable_sudo():
            print("Sudo access disabled for jarvis user")
        else:
            print("Failed to disable sudo access. Please run with sudo: sudo jarvis sudo disable")
            sys.exit(1)


def get_sudo_status() -> None:
    """Show current sudo access status."""
    from .core.sudo_manager import is_sudo_enabled

    enabled = is_sudo_enabled()
    config_preference = Config.JARVIS_SUDO_ENABLED

    print(f"Sudo access: {'enabled' if enabled else 'disabled'}")
    print(f"Config preference: {'enabled' if config_preference else 'disabled'}")

    if enabled != config_preference:
        print("Warning: System state differs from config preference")
        print("  Run 'jarvis sudo enable' or 'jarvis sudo disable' to sync")


def set_llm_provider(provider: str) -> None:
    """Set LLM provider (ollama or api)."""
    provider = provider.lower().strip()
    if provider not in ["ollama", "api"]:
        print(f"Error: Invalid provider '{provider}'. Must be 'ollama' or 'api'")
        sys.exit(1)

    _update_env_setting("LLM_PROVIDER", provider)
    print(f"LLM provider set to: {provider}")

    if provider == "api":
        print("  Note: Set URL and key with 'jarvis llm-url' and 'jarvis api-key'")


def set_llm_model(model_name: str) -> None:
    """
    Update LLM_MODEL in .env file.

    Args:
        model_name: Model name (e.g., 'qwen2.5:7b', 'gpt-4')
    """
    if not model_name or not model_name.strip():
        print("Error: Model name cannot be empty")
        sys.exit(1)

    model_name = model_name.strip()
    _update_env_setting("LLM_MODEL", model_name)
    print(f"LLM model set to: {model_name}")


def get_llm_model() -> str:
    """Get current LLM model from config."""
    return Config.LLM_MODEL or "(not set)"


def set_llm_url(url: str) -> None:
    """Set LLM base URL (used by all providers)."""
    if not url or not url.strip():
        print("Error: URL cannot be empty")
        sys.exit(1)

    _update_env_setting("LLM_URL", url.strip())
    print(f"LLM URL set to: {url.strip()}")


def set_api_key(key: str) -> None:
    """Set API key (used by all providers that require authentication)."""
    if not key or not key.strip():
        print("Error: API key cannot be empty")
        sys.exit(1)

    _update_env_setting("LLM_API_KEY", key.strip())
    print(f"API key set (length: {len(key.strip())} chars)")


def show_llm_config() -> None:
    """Show current LLM configuration."""
    print("LLM Configuration:")
    print(f"  Provider: {Config.LLM_PROVIDER}")
    print(f"  Model: {Config.LLM_MODEL or '(not set)'}")
    print(f"  URL: {Config.LLM_URL}")
    print(f"  API Key: {'set' if Config.LLM_API_KEY else '(not set)'}")

    if Config.LLM_PROVIDER == "ollama":
        print(f"  Auto-pull: {'enabled' if Config.LLM_AUTO_PULL else 'disabled'}")

    print(f"  Dispatch binary: {Config.DISPATCH_BINARY}")
    print(f"  Dispatch timeout: {Config.DISPATCH_TIMEOUT}s")


def _update_env_setting(key: str, value: str) -> None:
    """
    Update a setting in .env file.

    Args:
        key: Environment variable name
        value: New value
    """
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()
    else:
        template_file = Path(__file__).parent / ".env.example"
        if template_file.exists():
            logger.info("Creating .env from template...")
            lines = template_file.read_text().splitlines()
        else:
            lines = []

    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}=") or line.startswith(f"#{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(lines) + "\n")

    os.environ[key] = value
    setattr(Config, key, value)


def get_output_mode() -> str:
    """Get current output mode from config."""
    return Config.OUTPUT_MODE


def _check_capabilities() -> dict:
    """Check system capabilities for voice features."""
    capabilities = {
        'voice_input': False,
        'voice_output': False,
    }

    try:
        from .voice.audio import check_audio_input_available, check_audio_output_available
        capabilities['voice_input'] = check_audio_input_available()
        capabilities['voice_output'] = check_audio_output_available()
    except Exception as e:
        logger.debug(f"Error checking capabilities: {e}")

    return capabilities


def show_usage() -> None:
    """Display usage information."""
    print("JARVIS AI Assistant - CLI Interface")
    print()
    print("Usage:")
    print("  jarvis                    # Start voice activation mode")
    print("  jarvis tui                # Interactive TUI with session sidebar")
    print("  jarvis chat               # Start interactive text chat (stdin)")
    print("  jarvis text               # Set text output mode")
    print("  jarvis voice              # Set voice output mode")
    print('  jarvis ask "<message>"    # Ask a single question')
    print("  jarvis output-type        # Show current output mode")
    print("  jarvis history-reset on   # Enable history reset after each response")
    print("  jarvis history-reset off  # Disable history reset (maintain context)")
    print("  jarvis history-reset      # Show current history reset setting")
    print("  jarvis sudo enable        # Enable sudo access (requires root)")
    print("  jarvis sudo disable       # Disable sudo access (requires root)")
    print("  jarvis sudo               # Show current sudo access status")
    print()
    print("LLM Configuration:")
    print("  jarvis provider <ollama|api>  # Set LLM provider")
    print("  jarvis model                  # Show current LLM model")
    print("  jarvis model <model_name>     # Set LLM model")
    print("  jarvis llm-url <url>          # Set LLM base URL")
    print("  jarvis api-key <key>          # Set API key")
    print("  jarvis auto-pull on/off       # Enable/disable auto-pull missing models")
    print("  jarvis llm-config             # Show current LLM configuration")


def main() -> None:
    """Main CLI entry point."""
    from .main import Jarvis

    # No arguments or "run" — dual-input mode (voice + socket + optional stdin)
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] == "run"):
        if len(sys.argv) > 1:
            sys.argv.pop(1)
        if not Config.LLM_MODEL or not str(Config.LLM_MODEL).strip():
            print("Error: LLM model not configured.")
            print("  Run: jarvis model <model-name>")
            print("  Example: jarvis model qwen3:4b")
            sys.exit(1)
        print("Starting JARVIS (dual input: voice + socket)...")
        print("  Say 'Hey Jarvis' for voice, or use 'jarvis send <msg>' from another terminal.")
        print("  Press Ctrl+C to stop.\n")

        try:
            jarvis = Jarvis()
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\nGoodbye.")
        except Exception as e:
            logger.error(f"Failed to start JARVIS: {e}")
            print(f"\nError: {e}")
            print("\nTip: Use 'jarvis chat' for text-only mode.")
            sys.exit(1)
        return

    command = sys.argv[1]

    if command == "send":
        _cmd_send()
        return

    if command == "chat":
        if not Config.LLM_MODEL or not str(Config.LLM_MODEL).strip():
            print("Error: LLM model not configured. Run: jarvis model <model-name>")
            sys.exit(1)
        print("Starting JARVIS interactive chat...")
        print("Type your messages. Press Ctrl+C to exit.\n")
        try:
            jarvis = Jarvis(text_mode=True)
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\nGoodbye.")
        except Exception as e:
            logger.error(f"Failed to start JARVIS: {e}")
            print(f"\nError: {e}")
            sys.exit(1)

    elif command == "tui":
        if not Config.LLM_MODEL or not str(Config.LLM_MODEL).strip():
            print("Error: LLM model not configured. Run: jarvis model <model-name>")
            sys.exit(1)
        try:
            from .tui import run_tui
        except ImportError as e:
            print("Error: TUI dependencies are not installed.")
            print("  Install with: pip install 'jarvis-ai[tui]'")
            print(f"  (missing: {e.name if hasattr(e, 'name') else e})")
            sys.exit(1)
        try:
            run_tui()
        except KeyboardInterrupt:
            print("\nGoodbye.")
        except Exception as e:
            logger.error(f"TUI crashed: {e}", exc_info=True)
            print(f"\nError: {e}")
            sys.exit(1)

    elif command == "text":
        set_output_mode("text")

    elif command == "voice":
        capabilities = _check_capabilities()
        if not capabilities['voice_output']:
            print("Warning: No audio output devices detected.")
            print("  Voice output will fallback to text mode.")
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
            enabled = Config.RESET_HISTORY_AFTER_RESPONSE
            print(f"History reset: {'enabled' if enabled else 'disabled'}")
        elif len(sys.argv) == 3:
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
            get_sudo_status()
        elif len(sys.argv) == 3:
            value = sys.argv[2].lower()
            if value in ["on", "true", "1", "yes", "enable"]:
                set_sudo_access(True)
            elif value in ["off", "false", "0", "no", "disable"]:
                set_sudo_access(False)
            else:
                print(f"Error: Invalid value '{value}'. Use 'enable' or 'disable'")
                sys.exit(1)
        else:
            print("Usage: jarvis sudo [enable|disable]")
            sys.exit(1)

    elif command == "model":
        if len(sys.argv) == 2:
            model = get_llm_model()
            print(f"Current LLM model: {model}")
        elif len(sys.argv) == 3:
            if sys.argv[2] in ["-n", "--name", "set"]:
                print("Error: Model name required")
                print("Usage: jarvis model <model_name>")
                sys.exit(1)
            else:
                set_llm_model(sys.argv[2])
        elif len(sys.argv) == 4 and sys.argv[2] in ["-n", "--name", "set"]:
            set_llm_model(sys.argv[3])
        else:
            print("Usage: jarvis model [<model_name>]")
            sys.exit(1)

    elif command == "ask":
        if len(sys.argv) < 3:
            print("Error: Message required")
            print('Usage: jarvis ask "<message>"')
            sys.exit(1)

        message = " ".join(sys.argv[2:])
        jarvis = Jarvis(text_mode=True)
        jarvis.ask(message)

    elif command == "provider":
        if len(sys.argv) < 3:
            print(f"Current provider: {Config.LLM_PROVIDER}")
            print("Usage: jarvis provider <ollama|api>")
            sys.exit(1)
        set_llm_provider(sys.argv[2])

    elif command == "llm-url":
        if len(sys.argv) < 3:
            print(f"Current LLM URL: {Config.LLM_URL}")
            sys.exit(1)
        set_llm_url(sys.argv[2])

    elif command == "api-key":
        if len(sys.argv) < 3:
            print(f"API key: {'set' if Config.LLM_API_KEY else '(not set)'}")
            sys.exit(1)
        set_api_key(sys.argv[2])

    elif command == "llm-config":
        show_llm_config()

    elif command == "auto-pull":
        if len(sys.argv) == 2:
            auto_pull = getattr(Config, 'LLM_AUTO_PULL', False)
            print(f"Auto-pull missing models: {'enabled' if auto_pull else 'disabled'}")
        elif len(sys.argv) == 3:
            value = sys.argv[2].lower()
            if value in ["on", "true", "1", "yes", "enable"]:
                _update_env_setting("LLM_AUTO_PULL", "true")
                print("Auto-pull enabled")
            elif value in ["off", "false", "0", "no", "disable"]:
                _update_env_setting("LLM_AUTO_PULL", "false")
                print("Auto-pull disabled")
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
