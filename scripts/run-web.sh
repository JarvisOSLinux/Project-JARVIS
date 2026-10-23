#!/usr/bin/env bash
# Start the jarvis web interface (the phone page) and everything it needs.
#
#   scripts/run-web.sh               local: JARVIS daemon + gateway on localhost
#   scripts/run-web.sh --stub        local, no LLM: a stub daemon answers instead
#   scripts/run-web.sh --tailscale   real: gateway published on your tailnet
#   scripts/run-web.sh --tailscale --login-server https://hs.example.com
#                                    same, with this machine joined to a
#                                    self-hosted Headscale server
#
# Local modes treat every request as one throwaway owner account and use a
# temporary allowlist, so your real one is untouched. Never use them on a
# shared machine. --tailscale uses your real allowlist (jarvis web allow ...)
# and adds your own Tailscale account as owner when the list is empty.
#
# --tailscale does the client side on this machine: installs tailscale, joins
# the tailnet (--login-server, or $JARVIS_LOGIN_SERVER, for Headscale; an auth
# key from $TS_AUTHKEY, else interactive), takes the MagicDNS suffix from the
# node's own name, and serves over HTTPS when the control server issues
# certificates, over plain HTTP (still WireGuard-encrypted) when it does not.
#
# Options: --port N (default 8318), --no-open (do not open a browser),
# --allow LOGIN[:ROLE] (also allow this account; repeatable; role defaults
# to trusted).
# Ctrl+C stops everything this script started.
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

MODE=local
STUB=0
OPEN=1
LOGIN_SERVER="${JARVIS_LOGIN_SERVER:-}"
ALLOW=()
PORT="${JARVIS_WEB_PORT:-8318}"
while [ $# -gt 0 ]; do
    case "$1" in
        --stub) STUB=1 ;;
        --tailscale) MODE=tailscale ;;
        --no-open) OPEN=0 ;;
        --login-server)
            LOGIN_SERVER="${2:?--login-server needs a URL}"
            shift
            ;;
        --allow)
            ALLOW+=("${2:?--allow needs LOGIN[:ROLE]}")
            shift
            ;;
        --port)
            PORT="${2:?--port needs a number}"
            shift
            ;;
        -h | --help)
            sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
SERVE_FLAG=""
cleanup() {
    trap - EXIT INT TERM
    echo
    echo "==> stopping"
    if [ -n "$SERVE_FLAG" ]; then
        tailscale serve "$SERVE_FLAG" off >/dev/null 2>&1 || true
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
        echo "    type 'confirm' in the chat to get a permission request"
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
fi
sudo systemctl enable --now tailscaled

# One read of the node's state: prints "<state>|<dns name>|<ip>|<login>|<certs>".
node_info() {
    tailscale status --json 2>/dev/null | "$BIN/python" -c '
import json, sys
try:
    s = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
me = s.get("Self") or {}
user = (s.get("User") or {}).get(str(me.get("UserID")), {})
ips = me.get("TailscaleIPs") or [""]
print("|".join([
    s.get("BackendState", ""),
    (me.get("DNSName") or "").rstrip("."),
    ips[0],
    user.get("LoginName", ""),
    "yes" if s.get("CertDomains") else "no",
]))'
}

IFS='|' read -r STATE NAME IP ME CERTS <<<"$(node_info)"
if [ "$STATE" != Running ]; then
    UP=(sudo tailscale up)
    if [ -n "$LOGIN_SERVER" ]; then
        UP+=(--login-server "$LOGIN_SERVER")
    fi
    if [ -n "${TS_AUTHKEY:-}" ]; then
        (umask 077 && printf '%s' "$TS_AUTHKEY" >"$WORK/authkey")
        UP+=("--auth-key=file:$WORK/authkey")
    fi
    echo "==> joining the tailnet${LOGIN_SERVER:+ at $LOGIN_SERVER}"
    "${UP[@]}"
    IFS='|' read -r STATE NAME IP ME CERTS <<<"$(node_info)"
elif [ -n "$LOGIN_SERVER" ]; then
    CURRENT="$(tailscale debug prefs 2>/dev/null | "$BIN/python" -c '
import json, sys
try:
    print(json.load(sys.stdin).get("ControlURL", ""))
except ValueError:
    pass')"
    if [ -n "$CURRENT" ] && [ "${CURRENT%/}" != "${LOGIN_SERVER%/}" ]; then
        echo "this node is already on $CURRENT, not $LOGIN_SERVER." >&2
        echo "run 'sudo tailscale logout' first if that is what you want." >&2
        exit 1
    fi
fi
if [ "$STATE" != Running ]; then
    echo "tailscale is not running (state: ${STATE:-unknown})" >&2
    exit 1
fi

# Without MagicDNS the node has no name, so the gateway accepts its IP instead.
HOST="${NAME:-$IP}"
if [ -n "$NAME" ]; then
    export JARVIS_WEB_HOST_SUFFIX=".${NAME#*.}"
else
    export JARVIS_WEB_HOST_SUFFIX="$IP"
fi

if [ -z "$("$BIN/jarvis" web list | grep -E '^  [^ ]+ +(guest|trusted|owner)$' || true)" ]; then
    echo "==> allowlist is empty, adding $ME as owner"
    "$BIN/jarvis" web allow "$ME" owner
fi
for entry in "${ALLOW[@]:-}"; do
    [ -n "$entry" ] || continue
    "$BIN/jarvis" web allow "${entry%%:*}" "$([ "$entry" = "${entry#*:}" ] && echo trusted || echo "${entry#*:}")"
done

if [ "$CERTS" = yes ]; then
    SERVE_FLAG="--https=443"
    SCHEME=https
else
    SERVE_FLAG="--http=80"
    SCHEME=http
fi
tailscale serve --bg "$SERVE_FLAG" "$PORT"

echo "==> $SCHEME://$HOST   (open it on a phone that is on the same tailnet)"
if [ "$SCHEME" = http ]; then
    echo "    no certificate from the control server: traffic is encrypted by the"
    echo "    tunnel, but browsers treat the page as insecure, so the phone's"
    echo "    microphone and speech recognition stay off"
fi
echo "    this machine appears as: $ME"
echo "    allowed accounts (use the login exactly as 'tailscale whois <ip>' shows it):"
"$BIN/jarvis" web list
"$BIN/jarvis" web start &
PIDS+=("$!")
wait "$!"
