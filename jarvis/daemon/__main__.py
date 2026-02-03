"""
Entry point for running JARVIS daemon as a module.

Usage:
    python -m jarvis.daemon
    python -m jarvis.daemon --host 127.0.0.1 --port 18789
"""

import argparse
from .gateway import run_gateway, DEFAULT_HOST, DEFAULT_PORT


def main():
    parser = argparse.ArgumentParser(description="JARVIS Daemon")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind to")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    args = parser.parse_args()

    run_gateway(args.host, args.port)


if __name__ == "__main__":
    main()
