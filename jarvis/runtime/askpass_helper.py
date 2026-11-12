"""sudo's askpass program while JARVIS asks for the password (#310).

dmcp elevates a root call with ``sudo -A`` when this program says it is ready
(``--check``); sudo then runs it as the user, with its prompt as the argument,
and reads the password from its standard output. The password comes from the
JARVIS daemon over ``$JARVIS_ASKPASS_SOCKET``, which shows the prompt in
jarvis-app and on the phones allowed to answer (`runtime/askpass.py`), and it
goes nowhere but to sudo.

Standard library only: the daemon copies this file into its data directory
and sudo runs it with ``python -I``, so it imports nothing from JARVIS, reads
no config and writes no log.
"""

import json
import os
import socket
import sys

# How long `--check` may take: dmcp asks before every elevation.
CHECK_TIMEOUT_S = 5.0


def _connect():
    path = os.environ.get("JARVIS_ASKPASS_SOCKET", "")
    if not path:
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(path)
    except OSError:
        sock.close()
        return None
    return sock


def _send(sock, message):
    sock.sendall((json.dumps(message) + "\n").encode("utf-8"))


def _reply(sock, still_wanted):
    """The daemon's one-line answer, or None if it never comes.

    ``still_wanted`` is asked between reads: sudo gives up on a password
    after a while, and then nobody is left to hand one to.
    """
    data = b""
    while b"\n" not in data:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            if still_wanted():
                continue
            return None
        except OSError:
            return None
        if not chunk:
            return None
        data += chunk
    try:
        reply = json.loads(data.split(b"\n", 1)[0])
    except ValueError:
        return None
    return reply if isinstance(reply, dict) else None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    sock = _connect()
    if sock is None:
        return 1
    with sock:
        if argv[:1] == ["--check"]:
            sock.settimeout(CHECK_TIMEOUT_S)
            _send(sock, {"type": "check"})
            reply = _reply(sock, lambda: False)
            return 0 if reply and reply.get("ready") is True else 1

        sudo = os.getppid()
        sock.settimeout(1.0)
        _send(sock, {"type": "ask", "prompt": argv[0] if argv else ""})
        reply = _reply(sock, lambda: os.getppid() == sudo)
    password = reply.get("password") if reply else None
    if not isinstance(password, str):
        return 1
    sys.stdout.write(password + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
