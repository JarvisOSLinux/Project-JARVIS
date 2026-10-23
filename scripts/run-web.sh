#!/usr/bin/env bash
# Start the jarvis web interface (the phone page) and everything it needs.
#
#   scripts/run-web.sh               local: JARVIS daemon + gateway on localhost
#   scripts/run-web.sh --stub        local, no LLM: a stub daemon answers instead
#   scripts/run-web.sh --tailscale   real: gateway published on your tailnet
#
# Local modes treat every request as one throwaway owner account and use a
# temporary allowlist, so your real one is untouched. Never use them on a
# shared machine. --tailscale uses your real allowlist (jarvis web allow ...)
# and adds your own Tailscale account as owner when the list is empty.
#
# Options: --port N (default 8318), --no-open (do not open a browser).
# Ctrl+C stops everything this script started.
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

MODE=local
STUB=0
OPEN=1
PORT="${JARVIS_WEB_PORT:-8318}"
while [ $# -gt 0 ]; do
    case "$1" in
        --stub) STUB=1 ;;
        --tailscale) MODE=tailscale ;;
        --no-open) OPEN=0 ;;
        --port)
            PORT="${2:?--port needs a number}"
            shift
            ;;
        -h | --help)
            sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            exit 2
            ;;
    esac
    shift
done

if [ "$MODE" = tailscale ] && [ "$STUB" = 1 ]; then
    echo "--stub and --tailscale do not mix: a stub must never be published" >&2
    exit 2
fi

ensure_env

WORK="$(mktemp -d)"
PIDS=()
SERVED=0
cleanup() {
    trap - EXIT INT TERM
    echo
    echo "==> stopping"
    if [ "$SERVED" = 1 ]; then
        tailscale serve --https=443 off >/dev/null 2>&1 || true
    fi
    for pid in "${PIDS[@]:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

pm_install() {
    if command -v pacman >/dev/null; then
        sudo pacman -S --needed --noconfirm "$@"
    elif command -v apt-get >/dev/null; then
        sudo apt-get install -y "$@"
    elif command -v dnf >/dev/null; then
        sudo dnf install -y "$@"
    elif command -v zypper >/dev/null; then
        sudo zypper install -y "$@"
    else
        echo "no supported package manager to install: $*" >&2
        exit 1
    fi
}

wait_for_socket() {
    local sock="$1" pid="$2" tries=60
    while [ ! -S "$sock" ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "daemon exited before opening $sock; its log:" >&2
            tail -n 15 "$WORK/daemon.log" >&2
            exit 1
        fi
        tries=$((tries - 1))
        if [ "$tries" -le 0 ]; then
            echo "timed out waiting for $sock" >&2
            exit 1
        fi
        sleep 0.5
    done
}

export JARVIS_WEB_ENABLED=true
export JARVIS_WEB_PORT="$PORT"

# -- daemon ---------------------------------------------------------------
if [ "$STUB" = 1 ]; then
    export JARVIS_GUI_SOCKET="$WORK/jarvis.sock"
    echo "==> stub daemon (no LLM)"
    "$BIN/python" "$ROOT/scripts/stub_daemon.py" "$JARVIS_GUI_SOCKET" >"$WORK/daemon.log" 2>&1 &
    PIDS+=("$!")
    wait_for_socket "$JARVIS_GUI_SOCKET" "$!"
else
    SOCK="$("$BIN/python" -c 'from jarvis.config import Config; print(Config.JARVIS_GUI_SOCKET)')"
    if [ -S "$SOCK" ]; then
        echo "==> using the JARVIS daemon already running on $SOCK"
    else
        echo "==> starting jarvis run"
        "$BIN/jarvis" run </dev/null >"$WORK/daemon.log" 2>&1 &
        PIDS+=("$!")
        wait_for_socket "$SOCK" "$!"
    fi
fi

# -- local mode -----------------------------------------------------------
if [ "$MODE" = local ]; then
    LOGIN="dev@localhost"
    export JARVIS_WEB_ALLOWLIST_FILE="$WORK/allowlist.json"
    "$BIN/jarvis" web allow "$LOGIN" owner >/dev/null
    URL="http://localhost:$PORT"
    echo "==> $URL  (every request is $LOGIN, owner)"
    if [ "$STUB" = 1 ]; then
        echo "    type 'confirm' in the chat to get a permission request,"
        echo "    and 'run' to watch a turn with action blocks"
    fi
    if [ "$OPEN" = 1 ]; then
        (sleep 1.5 && { xdg-open "$URL" || open "$URL"; } >/dev/null 2>&1) &
        PIDS+=("$!")
    fi
    "$BIN/jarvis" web start --insecure-dev-login "$LOGIN" &
    PIDS+=("$!")
    wait "$!"
    exit $?
fi

# -- tailscale mode -------------------------------------------------------
if ! command -v tailscale >/dev/null; then
    echo "==> installing tailscale"
    pm_install tailscale
    sudo systemctl enable --now tailscaled
fi
if ! tailscale status >/dev/null 2>&1; then
    echo "==> signing in to Tailscale"
    sudo tailscale up
fi

if [ -z "$("$BIN/jarvis" web list | grep -E ' (guest|trusted|owner)$' || true)" ]; then
    ME="$(tailscale status --json | "$BIN/python" -c '
import json, sys
s = json.load(sys.stdin)
print(s["User"][str(s["Self"]["UserID"])]["LoginName"])')"
    echo "==> allowlist is empty, adding $ME as owner"
    "$BIN/jarvis" web allow "$ME" owner
fi

NAME="$(tailscale status --json | "$BIN/python" -c '
import json, sys
print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
tailscale serve --bg --https=443 "$PORT"
SERVED=1
echo "==> https://$NAME   (open it on a phone with the Tailscale app on)"
echo "    allowed accounts:"
"$BIN/jarvis" web list
"$BIN/jarvis" web start &
PIDS+=("$!")
wait "$!"
