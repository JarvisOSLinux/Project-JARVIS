"""
CLI interface for JARVIS AI Assistant

Usage:
    jarvis                      # Start voice activation mode (legacy)
    jarvis "<message>"          # Quick query (connects to daemon)
    jarvis ask "<message>"      # Ask a question (legacy direct mode)

Daemon Commands:
    jarvis daemon start         # Start the JARVIS daemon
    jarvis daemon stop          # Stop the daemon
    jarvis daemon status        # Show daemon status
    jarvis voice-service        # Start voice service (connects to daemon)

Configuration:
    jarvis text                 # Set text output mode
    jarvis voice                # Set voice output mode
    jarvis output-type          # Show current output mode
    jarvis model                # Show/set LLM model
    jarvis provider             # Set LLM provider
"""

import sys
import os
import subprocess
from pathlib import Path
from .config import Config
from .core.logger import get_logger

logger = get_logger(__name__)

ENV_FILE = Path(__file__).parent / ".env"

# Daemon settings
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 18789


# =============================================================================
# Daemon Commands
# =============================================================================

def daemon_start(foreground: bool = False) -> None:
    """Start the JARVIS daemon"""
    from .clients.cli_client import is_daemon_running_quiet

    if is_daemon_running_quiet(DAEMON_HOST, DAEMON_PORT):
        print("Daemon is already running")
        return

    if foreground:
        # Run in foreground (blocking)
        print(f"Starting JARVIS daemon on {DAEMON_HOST}:{DAEMON_PORT} (foreground)...")
        print("Press Ctrl+C to stop.")
        from .daemon.gateway import run_gateway
        run_gateway(DAEMON_HOST, DAEMON_PORT)
        return

    print(f"Starting JARVIS daemon on {DAEMON_HOST}:{DAEMON_PORT}...")

    # Start daemon in background
    python_path = sys.executable
    daemon_cmd = [
        python_path, "-m", "jarvis.daemon.gateway",
        "--host", DAEMON_HOST,
        "--port", str(DAEMON_PORT)
    ]

    try:
        # Start as background process
        if sys.platform == "win32":
            # Windows: use CREATE_NO_WINDOW to hide console
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                daemon_cmd,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        else:
            # Unix: use nohup-like behavior
            subprocess.Popen(
                daemon_cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        # Wait a moment and check if it started
        import time
        for i in range(5):
            time.sleep(1)
            if is_daemon_running_quiet(DAEMON_HOST, DAEMON_PORT):
                print(f"Daemon started successfully on {DAEMON_HOST}:{DAEMON_PORT}")
                print("Use 'jarvis daemon status' to check status")
                print("Use 'jarvis daemon stop' to stop")
                return

        print("Daemon may have failed to start.")
        print("Try running in foreground to see errors:")
        print("  jarvis daemon start --foreground")

    except Exception as e:
        print(f"Failed to start daemon: {e}")
        sys.exit(1)


def daemon_stop() -> None:
    """Stop the JARVIS daemon"""
    from .clients.cli_client import is_daemon_running

    if not is_daemon_running(DAEMON_HOST, DAEMON_PORT):
        print("Daemon is not running")
        return

    print("Stopping JARVIS daemon...")

    # Find and kill the daemon process
    if sys.platform == "win32":
        # Windows: find process by port
        import subprocess
        try:
            # Get PID from netstat
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if f":{DAEMON_PORT}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    # Kill the process
                    subprocess.run(["taskkill", "/PID", pid, "/F"],
                                   capture_output=True, shell=True)
                    print(f"Daemon stopped (PID {pid})")
                    return
            print("Could not find daemon process")
        except Exception as e:
            print(f"Error stopping daemon: {e}")
            print("Try manually: taskkill /F /IM python.exe (careful!)")
    else:
        # Unix: use pkill
        import subprocess
        try:
            subprocess.run(["pkill", "-f", "jarvis.daemon.gateway"], check=True)
            print("Daemon stopped")
        except subprocess.CalledProcessError:
            print("Could not stop daemon. Try: pkill -f 'jarvis.daemon.gateway'")


def daemon_status() -> None:
    """Show daemon status"""
    from .clients.cli_client import status

    result = status(DAEMON_HOST, DAEMON_PORT)

    if result:
        print("JARVIS Daemon Status")
        print("=" * 40)
        print(f"Status: {result.get('status', 'unknown')}")
        print(f"Queries processed: {result.get('query_count', 0)}")
        print(f"Connected clients: {result.get('connected_clients', 0)}")
        print(f"LLM Provider: {result.get('llm_provider', 'unknown')}")
        print(f"LLM Model: {result.get('llm_model', 'unknown')}")
        print(f"Pending approvals: {result.get('pending_approvals', 0)}")
    else:
        print("Daemon is not running")
        print(f"Start with: jarvis daemon start")


def voice_service_start() -> None:
    """Start the voice service"""
    from .clients.cli_client import is_daemon_running

    if not is_daemon_running(DAEMON_HOST, DAEMON_PORT):
        print("Daemon is not running. Start it first:")
        print("  jarvis daemon start")
        sys.exit(1)

    print("Starting JARVIS Voice Service...")
    print("Say 'Jarvis' to activate!")
    print("Press Ctrl+C to stop.")

    from .services.voice_service import run_voice_service
    run_voice_service(DAEMON_HOST, DAEMON_PORT)


# =============================================================================
# Query Commands
# =============================================================================

def query_daemon(message: str) -> None:
    """Send a query to the daemon"""
    from .clients.cli_client import query, is_daemon_running

    if not is_daemon_running(DAEMON_HOST, DAEMON_PORT):
        print("Daemon is not running. Using direct mode...")
        print("(Start daemon with 'jarvis daemon start' for better performance)")
        print()
        # Fall back to direct mode
        query_direct(message)
        return

    result = query(message, DAEMON_HOST, DAEMON_PORT)
    if result:
        print(result)


def query_direct(message: str) -> None:
    """Direct query without daemon (legacy mode)"""
    from .main import Jarvis

    jarvis = Jarvis(text_mode=True)
    jarvis.ask(message)


# =============================================================================
# Configuration Commands
# =============================================================================

def set_output_mode(mode: str) -> None:
    """Update OUTPUT_MODE in .env file"""
    if mode not in ["text", "voice"]:
        print(f"Error: Invalid mode '{mode}'. Must be 'text' or 'voice'")
        sys.exit(1)

    _update_env_setting("OUTPUT_MODE", mode)
    print(f"Output mode set to: {mode}")


def set_history_reset(enabled: bool) -> None:
    """Update RESET_HISTORY_AFTER_RESPONSE in .env file"""
    value = "true" if enabled else "false"
    _update_env_setting("RESET_HISTORY_AFTER_RESPONSE", value)
    print(f"History reset {'enabled' if enabled else 'disabled'}")


def set_sudo_access(enabled: bool) -> None:
    """Enable or disable sudo access for jarvis user"""
    from .core.sudo_manager import enable_sudo, disable_sudo

    value = "true" if enabled else "false"
    _update_env_setting("JARVIS_SUDO_ENABLED", value)

    if enabled:
        if enable_sudo():
            print("Sudo access enabled for jarvis user")
        else:
            print("Failed to enable sudo access. Please run with sudo.")
            sys.exit(1)
    else:
        if disable_sudo():
            print("Sudo access disabled for jarvis user")
        else:
            print("Failed to disable sudo access. Please run with sudo.")
            sys.exit(1)


def get_sudo_status() -> None:
    """Show current sudo access status"""
    from .core.sudo_manager import is_sudo_enabled

    enabled = is_sudo_enabled()
    config_preference = Config.JARVIS_SUDO_ENABLED

    print(f"Sudo access: {'enabled' if enabled else 'disabled'}")
    print(f"Config preference: {'enabled' if config_preference else 'disabled'}")

    if enabled != config_preference:
        print("Warning: System state differs from config preference")


def _update_env_setting(key: str, value: str) -> None:
    """Update a setting in .env file"""
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()
    else:
        template_file = Path(__file__).parent / "config.env.template"
        if template_file.exists():
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
    """Get current output mode from config"""
    return Config.OUTPUT_MODE


def set_llm_model(model_name: str) -> None:
    """Update LLM_MODEL in .env file"""
    if not model_name or not model_name.strip():
        print("Error: Model name cannot be empty")
        sys.exit(1)

    model_name = model_name.strip()
    _update_env_setting("LLM_MODEL", model_name)
    print(f"LLM model set to: {model_name}")
    print(f"  Note: Make sure the model is available: ollama pull {model_name}")


def set_llm_provider(provider: str) -> None:
    """Update LLM_PROVIDER in .env file"""
    if provider not in ["ollama", "api"]:
        print(f"Error: Invalid provider '{provider}'. Must be 'ollama' or 'api'")
        sys.exit(1)

    _update_env_setting("LLM_PROVIDER", provider)
    print(f"LLM provider set to: {provider}")


def set_api_url(url: str) -> None:
    """Update LLM_API_URL in .env file"""
    _update_env_setting("LLM_API_URL", url)
    print(f"API URL set to: {url}")


def set_api_key(key: str) -> None:
    """Update LLM_API_KEY in .env file"""
    _update_env_setting("LLM_API_KEY", key)
    print("API key set (hidden for security)")


def set_ollama_url(url: str) -> None:
    """Update LLM_OLLAMA_URL in .env file"""
    _update_env_setting("LLM_OLLAMA_URL", url)
    print(f"Ollama URL set to: {url}")


def show_llm_config() -> None:
    """Show current LLM configuration"""
    print("LLM Configuration")
    print("=" * 40)
    print(f"Provider: {Config.LLM_PROVIDER}")
    print(f"Model: {Config.LLM_MODEL or '(not set)'}")
    print(f"Ollama URL: {Config.LLM_OLLAMA_URL}")
    print(f"API URL: {Config.LLM_API_URL or '(not set)'}")
    print(f"API Key: {'*****' if Config.LLM_API_KEY else '(not set)'}")
    print(f"Auto-pull: {'enabled' if Config.LLM_AUTO_PULL else 'disabled'}")


def get_llm_model() -> str:
    """Get current LLM model from config"""
    return Config.LLM_MODEL or "(not set)"


def _check_capabilities() -> dict:
    """Check system capabilities for voice features"""
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


def show_usage() -> None:
    """Display usage information"""
    print("JARVIS AI Assistant - CLI Interface")
    print()
    print("Quick Usage:")
    print('  jarvis "your question"      # Query via daemon (recommended)')
    print("  jarvis ask \"your question\"  # Query directly (legacy)")
    print()
    print("Daemon Commands (for manual control):")
    print("  jarvis daemon start             # Start daemon (foreground)")
    print("  jarvis daemon start --foreground # Start with logs visible")
    print("  jarvis daemon stop              # Stop the daemon")
    print("  jarvis daemon status            # Show daemon status")
    print()
    print("Systemd (recommended for Arch Linux):")
    print("  systemctl --user start jarvis-daemon   # Start daemon")
    print("  systemctl --user enable jarvis-daemon  # Auto-start on login")
    print("  systemctl --user start jarvis-voice    # Start voice service")
    print()
    print("Voice Commands:")
    print("  jarvis                      # Start voice mode (legacy direct)")
    print("  jarvis voice-service        # Start voice service (daemon mode)")
    print()
    print("Configuration:")
    print("  jarvis text                 # Set text output mode")
    print("  jarvis voice                # Set voice output mode")
    print("  jarvis output-type          # Show current output mode")
    print("  jarvis model                # Show current LLM model")
    print("  jarvis model <name>         # Set LLM model")
    print("  jarvis provider <type>      # Set LLM provider (ollama|api)")
    print("  jarvis llm-config           # Show all LLM settings")
    print()
    print("Advanced:")
    print("  jarvis api-url <url>        # Set API base URL")
    print("  jarvis api-key <key>        # Set API key")
    print("  jarvis ollama-url <url>     # Set Ollama URL")
    print("  jarvis history-reset on/off # Toggle history reset")
    print("  jarvis auto-pull on/off     # Toggle auto-pull models")
    print()
    print("Examples:")
    print('  jarvis "what time is it?"')
    print("  jarvis daemon start && jarvis voice-service")
    print("  jarvis model qwen2.5:7b")


# =============================================================================
# Main Entry Point
# =============================================================================

def main() -> None:
    """Main CLI entry point"""

    # No arguments - start voice activation (legacy mode)
    if len(sys.argv) == 1:
        print("Starting JARVIS in voice activation mode (legacy)...")
        print("Tip: For daemon mode, use 'jarvis daemon start' then 'jarvis voice-service'")
        print()

        capabilities = _check_capabilities()
        if not capabilities['voice_input']:
            print("Warning: No audio input devices detected.")
            print("Use 'jarvis ask \"message\"' for text mode.")
            print()

        try:
            from .main import Jarvis
            jarvis = Jarvis()
            if not jarvis.voice_manager:
                print("Voice manager unavailable. Use 'jarvis ask \"message\"' instead.")
                sys.exit(1)
            jarvis.listen_with_activation()
        except Exception as e:
            logger.error(f"Failed to start JARVIS: {e}")
            print(f"\nError: {e}")
            sys.exit(1)
        return

    # Parse command
    command = sys.argv[1]

    # ==========================================================================
    # Daemon Commands
    # ==========================================================================
    if command == "daemon":
        if len(sys.argv) < 3:
            print("Usage: jarvis daemon <start|stop|status>")
            sys.exit(1)

        subcommand = sys.argv[2]
        if subcommand == "start":
            # Check for --foreground flag
            foreground = "--foreground" in sys.argv or "-f" in sys.argv
            daemon_start(foreground=foreground)
        elif subcommand == "stop":
            daemon_stop()
        elif subcommand == "status":
            daemon_status()
        else:
            print(f"Unknown daemon command: {subcommand}")
            print("Usage: jarvis daemon <start|stop|status>")
            sys.exit(1)

    elif command == "voice-service":
        voice_service_start()

    # ==========================================================================
    # Query Commands
    # ==========================================================================
    elif command == "ask":
        if len(sys.argv) < 3:
            print("Error: Message required")
            print('Usage: jarvis ask "message"')
            sys.exit(1)

        message = " ".join(sys.argv[2:])
        query_direct(message)  # Legacy direct mode

    elif command.startswith('"') or command.startswith("'"):
        # Quoted message - use daemon
        message = " ".join(sys.argv[1:]).strip('"\'')
        query_daemon(message)

    # ==========================================================================
    # Configuration Commands
    # ==========================================================================
    elif command == "text":
        set_output_mode("text")

    elif command == "voice":
        capabilities = _check_capabilities()
        if not capabilities['voice_output']:
            print("Warning: No audio output devices detected.")
            response = input("Continue anyway? (y/n): ").strip().lower()
            if response not in ['y', 'yes']:
                sys.exit(0)
        set_output_mode("voice")

    elif command == "output-type":
        print(f"Current output mode: {get_output_mode()}")

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
            set_llm_model(sys.argv[2])
        elif len(sys.argv) == 4 and sys.argv[2] in ["-n", "--name", "set"]:
            set_llm_model(sys.argv[3])
        else:
            print("Usage: jarvis model [<model_name>]")
            sys.exit(1)

    elif command == "provider":
        if len(sys.argv) < 3:
            print(f"Current provider: {Config.LLM_PROVIDER}")
            print("Usage: jarvis provider <ollama|api>")
            sys.exit(1)
        set_llm_provider(sys.argv[2])

    elif command == "api-url":
        if len(sys.argv) < 3:
            print("Error: API URL required")
            sys.exit(1)
        set_api_url(sys.argv[2])

    elif command == "api-key":
        if len(sys.argv) < 3:
            print("Error: API key required")
            sys.exit(1)
        set_api_key(sys.argv[2])

    elif command == "ollama-url":
        if len(sys.argv) < 3:
            print("Error: Ollama URL required")
            sys.exit(1)
        set_ollama_url(sys.argv[2])

    elif command == "llm-config":
        show_llm_config()

    elif command == "auto-pull":
        if len(sys.argv) == 2:
            auto_pull = getattr(Config, 'LLM_AUTO_PULL', False)
            print(f"Auto-pull: {'enabled' if auto_pull else 'disabled'}")
        elif len(sys.argv) == 3:
            value = sys.argv[2].lower()
            if value in ["on", "true", "1", "yes", "enable"]:
                _update_env_setting("LLM_AUTO_PULL", "true")
                print("Auto-pull enabled")
            elif value in ["off", "false", "0", "no", "disable"]:
                _update_env_setting("LLM_AUTO_PULL", "false")
                print("Auto-pull disabled")
            else:
                print(f"Error: Invalid value '{value}'")
                sys.exit(1)
        else:
            print("Usage: jarvis auto-pull [on|off]")
            sys.exit(1)

    elif command in ["-h", "--help", "help"]:
        show_usage()

    # ==========================================================================
    # Quick Query (no command prefix)
    # ==========================================================================
    else:
        # Treat as a query message
        message = " ".join(sys.argv[1:])
        query_daemon(message)


if __name__ == "__main__":
    main()
