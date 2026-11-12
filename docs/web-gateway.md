# `jarvis web` — phone access to a home JARVIS

Opt-in, off by default. Design and decisions: Project-JARVIS#266. The page is
jarvis-app's own UI (#267): one folder, `src/` in jarvis-app, used by the
desktop app through Tauri and served here through `transport.js`, which picks
the gateway's WebSocket when it runs in a browser. `jarvis/server/web/` is a
pinned copy: change the UI in jarvis-app, then run `scripts/sync-web-ui.sh`,
which records the jarvis-app commit and each file's hash in
`jarvis/server/web_ui.json` (`tests/test_web_ui_pin.py` fails on a hand edit).

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
| guest | chat, stop JARVIS (its running tasks keep running), see what JARVIS is doing (the activity line), sessions (their history without action blocks), confirmations and clients |
| trusted | guest + approve or deny requests, create and switch sessions, see action blocks (tool parameters and output), status and running work, stop all (ends the running tasks too), stop one running task |
| owner | trusted + rename and delete sessions, and the rest of jarvis-app: every setting, providers (add, edit with their keys, remove, reorder), approve all, the laptop's wake word on or off |

The owner's phone is jarvis-app (Yakup, 2026-11-10), less what belongs to the
laptop itself. No role may shut JARVIS down (nothing could start it again from
a phone), fill in a server's setup, send a raw `confirmation_response`, or
choose a wake-sound file; a phone may restore the default sound. Approvals
cannot be turned off from a phone: `set_setting` of `CONFIRMATION_MODE` to
`allow_all` is refused before it reaches JARVIS, read as JARVIS reads it
(`"allow_all "` included).

The role table covers the chat too: JARVIS runs a chat line that starts with
`/delete`, `/rename`, `/switch`, `/new`, `/sessions` or `/stop` as that
command, so the gateway treats the line as the command's own message
(`acts_as`). A guest typing `/delete 5bd5` or `/stop all` gets the same
refusal as sending `delete_session` or `stop_all`.

Approve All names the requests it approves (`ids`, the ones the page shows);
without them the gateway refuses it, and JARVIS approves only those of them
still pending. A request that came in after the page drew its list waits.

What JARVIS ran (tool parameters and output) is for trusted and owner: a
guest's `confirmation_request` is cut to its id and tools, its
`confirmation_list` items to their tool names, and its sessions come without
the LLM-written `summary` of their older turns.

The sudo password (#310): with `SUDO_PASSWORD_POPUP` on (Linux, off by
default), a root command asks for the host account's sudo password in a popup
on jarvis-app and on the trusted and owner phones, instead of polkit's dialog
on the host. The password goes from the phone to JARVIS, which hands it to
that one sudo (`runtime/askpass.py`); it is not logged, stored or audited (the
audit file gets the request's id). JARVIS shows a request only when it comes
from its own askpass helper under a real sudo started by dmcp below the
daemon, with sudo's command line as read from `/proc`. Only a task JARVIS
dispatched or a dmcp command it ran gets a popup. The owner's phone may turn
the popup on or off; it changes where sudo asks, not whether a password is
needed. Anyone allowed to answer can make the host
run that one command as root, so allow `trusted` only to people you would
give the password to.

API keys: the daemon's provider lists reach the owner with each `api_key`
and `headers` removed and `api_key_set` in its place. A key typed on the phone
goes to JARVIS over Tailscale's encrypted link and is written only to
`providers.json`. A key belongs to the address it was saved for: moving a
provider to another host (scheme, host or port) without typing a key removes
its stored key and headers, and `OLLAMA_API_KEY` from the environment is sent
only to ollama.com. So an edit from a phone cannot send a key to a server of
its choosing.

Every request that changes something is appended to
`$JARVIS_DATA_DIR/web_audit.jsonl` with the account and role: a setting with
its value, a provider change with its name, type, model and URL and whether a
key was given, never the key. A change made from a phone also shows on every
screen as a finished block ("Replies set to Shown and spoken (from
you@example.com's phone)"), kept with the session.

## Safety behaviour

- Refused while the host runs `allow_all`; a session ends if the host switches to it.
- Idle sessions close after `JARVIS_WEB_IDLE_TIMEOUT` seconds (default 900).
- Revoking an account (`jarvis web revoke`) cuts its open sessions at their next message.
- 20 requests per 10 seconds per connection; frames over 64 KB are refused.
- The daemon's provider and settings broadcasts reach only the owner, provider
  lists without their keys; setup broadcasts reach no one.

## Configuration

| Variable | Default |
|---|---|
| `JARVIS_WEB_ENABLED` | `false` |
| `JARVIS_WEB_PORT` | `8318` |
| `JARVIS_WEB_HOST` | `127.0.0.1` (`0.0.0.0` for direct tailnet access) |
| `JARVIS_WEB_LOGIN` | unset; on `0.0.0.0`, the account every request counts as |
| `JARVIS_WEB_ALLOWLIST_FILE` | `<config dir>/web_allowlist.json` |
| `JARVIS_WEB_AUDIT_FILE` | `<data dir>/web_audit.jsonl` |
| `JARVIS_WEB_IDLE_TIMEOUT` | `900` |
| `JARVIS_WEB_HOST_SUFFIX` | `.ts.net` |
| `JARVIS_WEB_DOMAINS` | unset; domains your reverse proxy serves the page on (comma-separated names or URLs) |

### As a service, on 0.0.0.0

`scripts/install-web-service.sh` installs two user services (`jarvis`, the
daemon, and `jarvis-web`, the page) that start at boot (lingering on) and run
as you, so the sudo password popup asks for your password. The page listens
on `0.0.0.0` (`JARVIS_WEB_HOST`) and devices on the tailnet open
`http://<host's tailnet IP>:8318`. Who can reach the host is the tailnet's
business (Headscale or Tailscale ACLs); no proxy adds an identity header, so
every request counts as `JARVIS_WEB_LOGIN` (your tailnet account, owner). Without
`JARVIS_WEB_LOGIN`, a request from another machine is refused, since its
identity header could be forged. The Host and Origin checks still hold: the
Host must be the host's IP, its MagicDNS name (`JARVIS_WEB_HOST_SUFFIX`, set
from the node's own name) or a one-label name, so a web page a phone has open
cannot reach JARVIS through that phone's browser. Only use it where nothing
but the tailnet reaches the host. `--uninstall` removes both services; logs are
in `journalctl --user -u jarvis -u jarvis-web`.

### On your own domain, behind your reverse proxy

Your proxy terminates HTTPS for the domain and forwards everything, the
WebSocket at `/ws` included, to `http://<host's tailnet IP>:8318`. Tell the
gateway the domain:

    scripts/install-web-service.sh --domain jarvis.example.org

`--domain` is repeatable and kept when the script runs again; `--no-domain`
drops it. It sets `JARVIS_WEB_DOMAINS`, which the gateway takes as a Host (a
proxy that passes the Host through, as Caddy and Traefik do) and as the
WebSocket's Origin (one that rewrites the Host to the upstream address, as
nginx does by default). Pages on any other origin are still refused. Every
request still counts as `JARVIS_WEB_LOGIN`, so the domain must only resolve
and route inside your tailnet, never to the public internet.

`scripts/run-web.sh` starts the daemon and the gateway for you: `--stub` uses a
fake daemon so no LLM is needed (type `confirm` for a permission request, `run`
for a turn with the activity line and action blocks), `--tailscale` also
publishes it on your tailnet and adds your own account as owner when the
allowlist is empty.

`jarvis web start --insecure-dev-login <login>` treats every request as that
account, for trying the page on `localhost` without Tailscale. It answers only
a `localhost`/`127.0.0.1` `Host` and refuses any request that came through a proxy
(`Tailscale-*`, `X-Forwarded-*` or `Forwarded` headers; `tailscale serve`
sends no identity for a tagged node but always `X-Forwarded-For`), so a
`tailscale serve` left pointing at the port does not make every tailnet peer
that account. It prints
a warning; never leave it running or publish it.

## Verify before relying on it (#266)

Not yet run, since they need a tailnet:

- V1: `tailscale serve` names the right account for Yakup's phone.
- V2: a `Tailscale-User-Login` header sent by a client is stripped.
- V3: after a reboot, Tailscale, JARVIS and `jarvis web` return and the keyring is unlocked.
- V4: lid closed, an hour idle, still reachable.
- V5: phone on cellular.
- V6: two phones: both see the stream; an approval on one clears the other.

## Protocol

JSON text frames, one message each: the `jarvis.sock` protocol
(`docs/gui-protocol.md`), narrowed by the table above. The gateway adds
`{"type": "gateway", "login": …, "role": …}` as the first frame and closes with
4003 (not allowed or revoked), 4008 (idle), 4009 (host in `allow_all`) or 4010
(JARVIS not running). A role taken away while a phone is connected stops what
it relays at that phone's next message.
