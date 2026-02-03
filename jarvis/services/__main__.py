"""
Entry point for running JARVIS voice service as a module.

Usage:
    python -m jarvis.services.voice_service
    python -m jarvis.services.voice_service --host 127.0.0.1 --port 18789
"""

import argparse
from .voice_service import run_voice_service, DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT


def main():
    parser = argparse.ArgumentParser(description="JARVIS Voice Service")
    parser.add_argument("--host", default=DEFAULT_DAEMON_HOST, help="Daemon host")
    parser.add_argument("--port", type=int, default=DEFAULT_DAEMON_PORT, help="Daemon port")
    args = parser.parse_args()

    run_voice_service(args.host, args.port)


if __name__ == "__main__":
    main()
