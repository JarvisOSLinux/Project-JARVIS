# `jarvis web` — phone access to a home JARVIS

Opt-in, off by default. Design and decisions: Project-JARVIS#266. The page is
the jarvis-app interface ported to a phone (#267).

```
phone browser ──(Tailscale)──▶ tailscale serve (HTTPS, adds Tailscale-User-Login)
                                      │
                                      ▼
                        jarvis web   127.0.0.1:8318   serves the page, checks account + role
                                      │
                                      ▼
                        jarvis.sock  (the socket jarvis-app and the TUI use)
```

## Host setup (the laptop that runs JARVIS)

Each step ends with a check.

1. `sudo pacman -S tailscale && sudo systemctl enable --now tailscaled`, then
   `sudo tailscale up`. *Check:* `tailscale status`.
2. In the Tailscale admin console turn on MagicDNS and HTTPS certificates.
   *Check:* `tailscale status --json` shows a non-empty `CertDomains`.
3. Let accounts in. The login is the Tailscale account name:
   `jarvis web allow you@example.com owner`, `jarvis web allow friend@example.com trusted`.
   *Check:* `jarvis web list`.
4. `JARVIS_WEB_ENABLED=true jarvis web` (needs `pip install 'project-jarvis[web]'`;
   `jarvis web` installs it when missing). Keep `CONFIRMATION_MODE=smart`.
5. `tailscale serve --bg --https=443 8318`. *Check:* `tailscale serve status`.
   Never `tailscale funnel`; the gateway refuses Funnel requests.
6. Open `https://<laptop>.<tailnet>.ts.net` on a phone with the Tailscale app on.

Sleep, keyring and service wiring (runbook sections C and E of #266) are still
host chores, not code.

## Headscale (self-hosted control server)

This machine is only a client of your Headscale server; nothing about the
gateway changes. `scripts/run-web.sh --tailscale --login-server https://hs.example.com`
does the client side: installs tailscale, joins (an auth key from `$TS_AUTHKEY`
is passed by file, not on the command line; without one it prompts), and sets
`JARVIS_WEB_HOST_SUFFIX` to the node's MagicDNS suffix instead of `.ts.net`.

Headscale does not issue HTTPS certificates, as far as we know, so the script
falls back to `tailscale serve --http=80`. The tunnel is still WireGuard, but
browsers treat the page as insecure, so the phone's microphone and speech
recognition do not work. Headscale logins are plain user names, not emails:
give `jarvis web allow` exactly what `tailscale whois <ip>` shows, or pass
`--allow name:role` to the script.

## Roles

| Role | May do |
|---|---|
| guest | chat, stop the reply, see sessions, confirmations and clients |
| trusted | guest + approve or deny requests, create and switch sessions |
| owner | trusted + rename and delete sessions |

No role may change settings, providers, the wake chime or setups, shut JARVIS
down, send a raw `confirmation_response`, or `approve_all_confirmations`.
Every request that changes something is appended to
`$JARVIS_DATA_DIR/web_audit.jsonl` with the account and role.

## Safety behaviour

- Refused while the host runs `allow_all`; a session ends if the host switches to it.
- Idle sessions close after `JARVIS_WEB_IDLE_TIMEOUT` seconds (default 900).
- Revoking an account (`jarvis web revoke`) cuts its open sessions at their next message.
- 20 requests per 10 seconds per connection; frames over 64 KB are refused.
- The daemon's provider, settings and setup broadcasts are never forwarded.

## Configuration

| Variable | Default |
|---|---|
| `JARVIS_WEB_ENABLED` | `false` |
| `JARVIS_WEB_PORT` | `8318` |
| `JARVIS_WEB_ALLOWLIST_FILE` | `<config dir>/web_allowlist.json` |
| `JARVIS_WEB_AUDIT_FILE` | `<data dir>/web_audit.jsonl` |
| `JARVIS_WEB_IDLE_TIMEOUT` | `900` |
| `JARVIS_WEB_HOST_SUFFIX` | `.ts.net` |

`scripts/run-web.sh` starts the daemon and the gateway for you: `--stub` uses a
fake daemon so no LLM is needed, `--tailscale` also publishes it on your tailnet
and adds your own account as owner when the allowlist is empty.

`jarvis web start --insecure-dev-login <login>` treats every request as that
account, for trying the page on `localhost` without Tailscale. It prints a
warning; never leave it running or publish it.

## Verify before relying on it (#266)

Not yet run, since they need a tailnet:

- V1: `tailscale serve` names the right account for Yakup's phone.
- V2: a `Tailscale-User-Login` header sent by a client is stripped.
- V3: after a reboot, Tailscale, JARVIS and `jarvis web` return and the keyring is unlocked.
- V4: lid closed, an hour idle, still reachable.
- V5: phone on cellular.
- V6: two phones: both see the stream; an approval on one clears the other.

## Protocol

JSON text frames, one message each, the `jarvis.sock` protocol narrowed by the
table above. The gateway adds `{"type": "gateway", "login": …, "role": …}` as
the first frame and closes with 4003 (not allowed or revoked), 4008 (idle),
4009 (host in `allow_all`) or 4010 (JARVIS not running). The activity, status
and running-task messages of #267 phase 2 do not exist yet.
