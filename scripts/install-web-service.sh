#!/usr/bin/env bash
# Run JARVIS and its web page (jarvis-app in a browser) as user services that
# start at boot, the page listening on 0.0.0.0 for your tailnet.
#
#   scripts/install-web-service.sh                       install, enable, start
#   scripts/install-web-service.sh --allow LOGIN[:ROLE]  also allow a tailnet
#                                                        account (repeatable;
#                                                        role defaults to trusted)
#   scripts/install-web-service.sh --port N              (default 8318)
#   scripts/install-web-service.sh --domain NAME         also served on NAME by
#                                                        your reverse proxy
#                                                        (repeatable; kept on
#                                                        later runs)
#   scripts/install-web-service.sh --no-domain           drop those domains
#   scripts/install-web-service.sh --uninstall
#
# Phones and laptops on the tailnet open http://<this host's tailnet IP>:<port>.
# Who can reach the host is the tailnet's business (Headscale ACLs); every
# request counts as your tailnet account (JARVIS_WEB_LOGIN), added to the
# allowlist as owner when missing (jarvis web allow / list / revoke).
#
# A domain: your reverse proxy terminates HTTPS for it and forwards to
# http://<this host's tailnet IP>:<port>, WebSockets included (/ws). Keep it
# inside the tailnet: every request counts as your account.
#
# The services run as you, so the sudo password popup asks for your password.
# Logs: journalctl --user -u jarvis -u jarvis-web -f
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

PORT="${JARVIS_WEB_PORT:-8318}"
ALLOW=()
DOMAINS=()
NO_DOMAIN=0
UNINSTALL=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift ;;
        --allow) ALLOW+=("$2"); shift ;;  # for `tailscale serve` use
        --domain) DOMAINS+=("$2"); shift ;;
        --no-domain) NO_DOMAIN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [ "$UNINSTALL" = 1 ]; then
    systemctl --user disable --now jarvis-web.service jarvis.service 2>/dev/null || true
    rm -f "$UNITS/jarvis-web.service" "$UNITS/jarvis.service"
    systemctl --user daemon-reload
    echo "==> removed the jarvis and jarvis-web user services"
    exit 0
fi

ensure_env

if ! command -v tailscale >/dev/null || ! tailscale status >/dev/null 2>&1; then
    echo "Tailscale is not running here; the page identifies phones through it." >&2
    exit 1
fi

# This node's tailnet name, address and account, and its MagicDNS suffix
# (".hs.th.net" on a Headscale named so; ".ts.net" on Tailscale's own).
IFS='|' read -r NAME IP ME <<<"$(tailscale status --json | "$BIN/python" -c '
import json, sys
s = json.load(sys.stdin)
me = s.get("Self") or {}
user = (s.get("User") or {}).get(str(me.get("UserID")), {})
print("|".join([
    (me.get("DNSName") or "").rstrip("."),
    (me.get("TailscaleIPs") or [""])[0],
    user.get("LoginName", ""),
]))')"
SUFFIX=".${NAME#*.}"

if [ -z "$ME" ]; then
    echo "Could not read this node's tailnet account from tailscale status." >&2
    exit 1
fi
if ! "$BIN/jarvis" web list | grep -qi "^ *$ME "; then
    echo "==> adding $ME to the allowlist as owner"
    "$BIN/jarvis" web allow "$ME" owner
fi
for entry in "${ALLOW[@]}"; do
    login="${entry%%:*}"
    role="trusted"
    [ "$entry" != "$login" ] && role="${entry#*:}"
    "$BIN/jarvis" web allow "$login" "$role"
done

# Domains: those given now, else the ones the installed unit already has.
WEB_DOMAINS=""
if [ "${#DOMAINS[@]}" -gt 0 ]; then
    WEB_DOMAINS="$(IFS=,; echo "${DOMAINS[*]}")"
elif [ "$NO_DOMAIN" = 0 ] && [ -f "$UNITS/jarvis-web.service" ]; then
    WEB_DOMAINS="$(sed -n 's/^Environment=JARVIS_WEB_DOMAINS=//p' "$UNITS/jarvis-web.service")"
fi

# A user service gets a bare PATH; dispatch, dmcp and contextor live in these.
SVC_PATH="$BIN:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "$UNITS"
cat >"$UNITS/jarvis.service" <<EOF
[Unit]
Description=JARVIS daemon
After=network-online.target

[Service]
WorkingDirectory=$ROOT
Environment=PATH=$SVC_PATH
ExecStart=$BIN/jarvis run
StandardInput=null
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

cat >"$UNITS/jarvis-web.service" <<EOF
[Unit]
Description=JARVIS web page (jarvis-app in a browser) for the tailnet
After=jarvis.service
Wants=jarvis.service

[Service]
WorkingDirectory=$ROOT
Environment=PATH=$SVC_PATH
Environment=JARVIS_WEB_ENABLED=true
Environment=JARVIS_WEB_HOST=0.0.0.0
Environment=JARVIS_WEB_LOGIN=$ME
Environment=JARVIS_WEB_PORT=$PORT
Environment=JARVIS_WEB_HOST_SUFFIX=$SUFFIX
${WEB_DOMAINS:+Environment=JARVIS_WEB_DOMAINS=$WEB_DOMAINS
}ExecStart=$BIN/jarvis web start
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable jarvis.service jarvis-web.service
systemctl --user restart jarvis.service jarvis-web.service
# Start at boot without a login, and keep running after logging out.
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    loginctl enable-linger "$USER" 2>/dev/null \
        || sudo loginctl enable-linger "$USER" \
        || echo "Could not turn on lingering; the services start at your next login." >&2
fi

echo "==> running. On a device in your tailnet open:"
echo "    http://$IP:$PORT"
[ -n "$NAME" ] && echo "    http://$NAME:$PORT"
IFS=, read -r -a SHOWN <<<"$WEB_DOMAINS"
for domain in "${SHOWN[@]}"; do
    # The gateway takes names or URLs; show each as its https address.
    domain="${domain#*://}"
    echo "    https://${domain%%/*}  (through your reverse proxy)"
done
echo "    Allowed: $("$BIN/jarvis" web list | tr -s ' ' | paste -sd ';')"
echo "    Logs: journalctl --user -u jarvis -u jarvis-web -f"
