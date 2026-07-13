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

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

from .config import Config
from .core.logger import JarvisLogger, get_logger
from .core.providers import (
    add_provider,
    edit_provider,
    list_providers,
    move_provider,
    parse_flags,
    remove_provider,
)

# Initialise logging from config before any get_logger() call so that the
# file handler (LOG_FILE) and level (LOG_LEVEL) are applied from the start.
# Without this, the first get_logger() call would call setup() with defaults
# (no log_file), set _initialized = True, and all subsequent setup() calls
# would return early — leaving LOG_FILE silently ignored.
JarvisLogger.setup(
    log_level=Config.LOG_LEVEL,
    log_file=Config.LOG_FILE or None,
    enable_colors=Config.LOG_COLORS,
)

logger = get_logger(__name__)

_jarvis_config_dir = os.environ.get("JARVIS_CONFIG_DIR")
if _jarvis_config_dir:
    ENV_FILE = Path(_jarvis_config_dir) / "jarvis.conf"
else:
    from .platform import current as _platform

    ENV_FILE = _platform.config_dir() / "jarvis.conf"


def _find_ipc_endpoint() -> "str | None":
    """Locate and ownership-verify a running JARVIS instance's input socket.

    Prints its own error and returns None on failure so callers can decide
    whether to exit.
    """
    from .platform import current as platform

    candidates = [Config.JARVIS_INPUT_SOCKET, "/run/jarvis/input.sock"]
    path = None
    for p in candidates:
        if p and _ipc_endpoint_exists(p):
            path = p
            break
    if not path:
        print("Error: JARVIS IPC endpoint not found.")
        print("  Tried:", ", ".join(p for p in candidates if p))
        print("  Is JARVIS running? Start with 'jarvis' or 'jarvis run'.")
        return None
    if not platform.ipc_verify_owner(path):
        print("Error: IPC endpoint ownership check failed.")
        return None
    return path


def _cmd_send() -> None:
    """Send a message to a running JARVIS instance via IPC."""
    from .platform import current as platform

    if len(sys.argv) < 3:
        print("Usage: jarvis send <message>")
        print("  Sends the message to a running JARVIS instance.")
        print("  Start JARVIS with 'jarvis' or 'jarvis run' first.")
        sys.exit(1)
    msg = " ".join(sys.argv[2:]).strip()
    if not msg:
        print("Error: Message cannot be empty")
        sys.exit(1)
    path = _find_ipc_endpoint()
    if not path:
        sys.exit(1)
    try:
        sock = platform.ipc_connect(path)
        try:
            sock.sendall((msg + "\n").encode("utf-8"))
        finally:
            sock.close()
        print("Sent.")
    except (socket.error, OSError) as e:
        print(f"Error: Could not send to JARVIS: {e}")
        sys.exit(1)


def _ipc_endpoint_exists(path: str) -> bool:
    """Check if an IPC endpoint exists (socket file or port file)."""
    return os.path.exists(path) or os.path.exists(path + ".port")


def _cmd_confirmations() -> None:
    """List or resolve pending tool confirmations on a running JARVIS instance."""
    from .platform import current as platform

    if len(sys.argv) == 2:
        request = {"type": "list_confirmations"}
    else:
        subcmd = sys.argv[2]
        if subcmd == "approve-all":
            request = {"type": "approve_all_confirmations"}
        elif subcmd in ("approve", "deny") and len(sys.argv) >= 4:
            request = {
                "type": (
                    "approve_confirmation"
                    if subcmd == "approve"
                    else "deny_confirmation"
                ),
                "id": sys.argv[3],
            }
        else:
            _show_confirmations_usage()
            sys.exit(1)

    path = _find_ipc_endpoint()
    if not path:
        sys.exit(1)

    try:
        sock = platform.ipc_connect(path)
        try:
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
            sock.settimeout(5.0)
            response_line = sock.makefile().readline()
        finally:
            sock.close()
    except (socket.error, OSError) as e:
        print(f"Error: Could not reach JARVIS: {e}")
        sys.exit(1)

    if not response_line:
        print("Error: No response from JARVIS.")
        sys.exit(1)

    try:
        response = json.loads(response_line)
    except json.JSONDecodeError:
        print("Error: Invalid response from JARVIS.")
        sys.exit(1)

    if response.get("type") == "confirmation_list":
        confirmations = response.get("confirmations", [])
        if not confirmations:
            print("No pending confirmations.")
            return
        print(f"Pending confirmations ({len(confirmations)}):")
        for c in confirmations:
            tools = ", ".join(c.get("tool_names", []))
            print(f"  [{c['id']}] {tools}")
    elif response.get("type") == "ack":
        print(response.get("message", "Done."))
    else:
        print(f"Error: {response.get('message', 'Unexpected response')}")
        sys.exit(1)


def _show_confirmations_usage() -> None:
    print("Usage:")
    print("  jarvis confirmations               # List pending confirmations")
    print("  jarvis confirmations approve <id>  # Approve one")
    print("  jarvis confirmations deny <id>      # Deny one")
    print("  jarvis confirmations approve-all    # Approve all pending")


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
    from .core.sudo_manager import disable_sudo, enable_sudo

    value = "true" if enabled else "false"
    _update_env_setting("JARVIS_SUDO_ENABLED", value)

    if enabled:
        if enable_sudo():
            print("Sudo access enabled for jarvis user")
        else:
            print(
                "Failed to enable sudo access. Please run with sudo: sudo jarvis sudo enable"
            )
            sys.exit(1)
    else:
        if disable_sudo():
            print("Sudo access disabled for jarvis user")
        else:
            print(
                "Failed to disable sudo access. Please run with sudo: sudo jarvis sudo disable"
            )
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


def _legacy_redirect(old_cmd: str) -> None:
    """Print a migration hint for removed legacy commands."""
    print(f"'{old_cmd}' has been removed. Use the provider pool instead:")
    print("  jarvis providers                                    # List providers")
    print("  jarvis providers add --type ollama --model <model>  # Add provider")
    print("  jarvis providers edit <name> --model <model>        # Update a field")
    sys.exit(1)


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

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(lines) + "\n")

    os.environ[key] = value
    setattr(Config, key, value)


def get_output_mode() -> str:
    """Get current output mode from config."""
    return Config.OUTPUT_MODE


def _check_capabilities() -> dict:
    """Check system capabilities for voice features."""
    capabilities = {
        "voice_input": False,
        "voice_output": False,
    }

    try:
        from .voice.audio import (
            check_audio_input_available,
            check_audio_output_available,
        )

        capabilities["voice_input"] = check_audio_input_available()
        capabilities["voice_output"] = check_audio_output_available()
    except Exception as e:
        logger.debug(f"Error checking capabilities: {e}")

    return capabilities


def _cmd_providers() -> None:
    """Manage the provider failover pool."""
    if len(sys.argv) == 2:
        providers = list_providers()
        if not providers:
            print("No providers configured.")
            print()
            print("Add one with:")
            print("  jarvis providers add --type ollama --model qwen3:4b")
            print(
                "  jarvis providers add --type api --model gpt-4 "
                "--url https://api.example.com --key sk-xxx"
            )
            return

        print(f"Provider pool ({len(providers)} providers):")
        print()
        for i, p in enumerate(providers):
            name = p.get("name", f"provider-{i}")
            ptype = p.get("type", "?")
            model = p.get("model", "?")
            url = p.get("url", "")
            priority = f"[{i + 1}]"
            print(f"  {priority} {name}")
            print(f"      type: {ptype}  model: {model}")
            if url:
                print(f"      url: {url}")
            if ptype == "api":
                print(f"      api_key: {'set' if p.get('api_key') else '(not set)'}")
            if p.get("temperature") is not None:
                print(f"      temperature: {p['temperature']}")
            print()
        print(f"  File: {Config.PROVIDERS_FILE}")
        return

    subcmd = sys.argv[2]

    if subcmd == "add":
        flags = parse_flags(sys.argv[3:])
        ptype = flags.get("type", "")
        model = flags.get("model", "")
        if not ptype or not model:
            print(
                "Usage: jarvis providers add --type <ollama|api|lmstudio> --model <model>"
            )
            print(
                "  Optional: --name <label> --url <url> --key <api_key> --temperature <0.0-2.0>"
            )
            sys.exit(1)
        temp = None
        if flags.get("temperature"):
            try:
                temp = float(flags["temperature"])
            except ValueError:
                print(
                    f"Error: Temperature must be a number, got '{flags['temperature']}'"
                )
                sys.exit(1)
        try:
            name, position = add_provider(
                ptype,
                model,
                name=flags.get("name"),
                url=flags.get("url"),
                api_key=flags.get("key"),
                temperature=temp,
            )
            print(f"Added provider '{name}' ({ptype}/{model}) at position {position}")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif subcmd == "remove":
        if len(sys.argv) < 4:
            print("Usage: jarvis providers remove <name>")
            sys.exit(1)
        try:
            remove_provider(sys.argv[3])
            print(f"Removed provider '{sys.argv[3]}'")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif subcmd == "move":
        if len(sys.argv) < 5:
            print("Usage: jarvis providers move <name> <position>")
            print("  Position is 1-based (1 = highest priority)")
            sys.exit(1)
        try:
            target_pos = int(sys.argv[4])
        except ValueError:
            print(f"Error: Position must be a number, got '{sys.argv[4]}'")
            sys.exit(1)
        try:
            move_provider(sys.argv[3], target_pos)
            print(f"Moved provider '{sys.argv[3]}' to position {target_pos}")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif subcmd == "edit":
        if len(sys.argv) < 4:
            print("Usage: jarvis providers edit <name> --field <value>")
            print("  Fields: --model, --url, --key, --type")
            sys.exit(1)
        flags = parse_flags(sys.argv[4:])
        if not flags:
            print("Error: No fields to update. Use --model, --url, --key, or --type")
            sys.exit(1)
        try:
            updated = edit_provider(sys.argv[3], **flags)
            print(f"Updated provider '{sys.argv[3]}': {', '.join(updated)}")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    else:
        print(f"Error: Unknown subcommand '{subcmd}'")
        _show_providers_usage()
        sys.exit(1)


def _show_providers_usage() -> None:
    print("Usage:")
    print("  jarvis providers                                  # List all")
    print("  jarvis providers add --type ollama --model <model> # Add provider")
    print("  jarvis providers remove <name>                    # Remove by name")
    print("  jarvis providers move <name> <position>           # Reorder priority")
    print("  jarvis providers edit <name> --model <model>      # Update a field")


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
    print("Pending Confirmations:")
    print("  jarvis confirmations               # List pending confirmations")
    print("  jarvis confirmations approve <id>  # Approve one")
    print("  jarvis confirmations deny <id>      # Deny one")
    print("  jarvis confirmations approve-all    # Approve all pending")
    print()
    print("Provider Pool:")
    print("  jarvis providers                                    # List providers")
    print("  jarvis providers add --type ollama --model <model>  # Add Ollama")
    print("  jarvis providers add --type api --model <m> --url <u> --key <k>")
    print(
        "  jarvis providers add --type lmstudio --model <m>    # Add LM Studio (no key needed)"
    )
    print("  jarvis providers remove <name>                      # Remove provider")
    print("  jarvis providers move <name> <position>             # Reorder priority")
    print("  jarvis providers edit <name> --model <model>        # Update a field")
    print(
        "  jarvis auto-pull on/off                             # Auto-pull missing models"
    )


def _has_llm_configured() -> bool:
    """True if at least one provider is configured."""
    return bool(list_providers())


def main() -> None:
    """Main CLI entry point."""
    from .main import Jarvis

    # No arguments or "run" — dual-input mode (voice + socket + optional stdin)
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] == "run"):
        if len(sys.argv) > 1:
            sys.argv.pop(1)
        if not _has_llm_configured():
            print("Error: No LLM configured.")
            print("  Quick start: jarvis providers add --type ollama --model qwen3:4b")
            print("  Or set a model: jarvis model qwen3:4b")
            sys.exit(1)
        print("Starting JARVIS (dual input: voice + socket)...")
        print(
            "  Say 'Hey Jarvis' for voice, or use 'jarvis send <msg>' from another terminal."
        )
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
        if not _has_llm_configured():
            print("Error: No LLM configured.")
            print("  Quick start: jarvis providers add --type ollama --model qwen3:4b")
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
        if not capabilities["voice_output"]:
            print("Warning: No audio output devices detected.")
            print("  Voice output will fallback to text mode.")
            response = input("Continue anyway? (y/n): ").strip().lower()
            if response not in ["y", "yes"]:
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

    elif command == "confirmations":
        _cmd_confirmations()

    elif command == "model":
        _legacy_redirect("jarvis model")

    elif command == "ask":
        if len(sys.argv) < 3:
            print("Error: Message required")
            print('Usage: jarvis ask "<message>"')
            sys.exit(1)

        message = " ".join(sys.argv[2:])
        jarvis = Jarvis(text_mode=True)
        jarvis.ask(message)

    elif command in ("provider", "llm-url", "api-key", "llm-config"):
        _legacy_redirect(f"jarvis {command}")

    elif command == "providers":
        _cmd_providers()

    elif command == "auto-pull":
        if len(sys.argv) == 2:
            auto_pull = getattr(Config, "LLM_AUTO_PULL", False)
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
