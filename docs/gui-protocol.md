# The `jarvis.sock` protocol

How an outside application talks to a running JARVIS: jarvis-app on the
desktop, and, through `jarvis web` (`docs/web-gateway.md`), the page on a
phone. It is written from `jarvis/runtime/io.py`, which is the reference; a
test pins each message added by #267.

## Connecting

- **Where:** `$JARVIS_DATA_DIR/jarvis.sock` (Linux `~/.local/share/jarvis`),
  overridable with `JARVIS_GUI_SOCKET`. On Windows there is no Unix socket:
  the daemon writes a loopback TCP port to `<socket>.port` and a per-start
  token to `<socket>.token`, which the client sends as its first line.
- **Who:** only the user who runs JARVIS. The socket is `0600` and the daemon
  checks the peer's credentials when it accepts the connection.
- **Framing:** one JSON object per line, UTF-8, both ways. Every message has a
  `type`. A line that is not JSON is ignored.
- **On connect** the daemon sends the current `state`. Send `hello` with a
  label so `list_clients` can name you, and with `subscribe` for the pushed
  events you can show: `{"type": "hello", "label": "jarvis-app", "subscribe":
  ["activity", "action", "user_message"]}`. Without it you get none of them,
  so a client written before them never sees an unknown type.
- **Keepalive:** after 60 s without a line from you the daemon sends `ping`;
  you may send `ping` and get `pong`.

Replies go to the client that asked. Changes to shared state (sessions,
providers, settings, confirmations) are broadcast to every client, so a
client keeps its view in step by listening, not by polling.

## From the client

### Chat

| `type` | Fields | What it does |
|---|---|---|
| `message` | `content` | Says something to JARVIS, as if typed. Slash commands (`/new`, `/switch 110`, `/context`) work too; their replies carry `command: true`. The state becomes `processing`. |
| `stop` | | Stops JARVIS's work in the current session (#270): the model's turn is cancelled, confirmations waiting are denied, reminders and timers cancelled, the requests closed. Tasks already running keep running and reach only the screen from then on, never the model. → `ack` with what was stopped and what still runs; the state becomes `idle`. |
| `stop_all` | | `stop`, and the session's running tasks are killed too. When one runs a package manager the first `stop_all` only warns (`ack`); a second within 15 s kills it. |
| `stop_task` | `pid` | Kills one running task (the `pid` from `work`); the model hears nothing from it. Its request is closed when nothing else of it is under way; otherwise the request's next turn is told the user stopped it. A package manager is only warned about at first (`ack`); a second `stop_task` for it within 15 s kills it. → `ack` with what was stopped. |
| `stop_stream` | | Ends the reply being shown (`response` with `done: true`) and sets the state to `idle`; JARVIS's work goes on. |
| `hello` | `label`, `subscribe` (optional list: `activity`, `action`, `user_message`, `password_request`, `password_request_done`) | Names this client in `list_clients`, and asks for the pushed events it can show. Subscribing to `password_request` also sends the requests already waiting. |
| `list_clients` | | → `client_list` |
| `ping` | | → `pong` |

### What JARVIS is doing (#267)

| `type` | Fields | Reply |
|---|---|---|
| `get_status` | `full` (bool, optional) | `status`: the `/status` health check. `full` also asks the registry for server updates, the one check that goes online. |
| `list_work` | | `work`: what the TUI's F4 view shows. |

Activity, action and user-message events are pushed to the clients that subscribed to them in `hello` (see below).

### Confirmations

| `type` | Fields | Reply |
|---|---|---|
| `list_confirmations` | | `confirmation_list` |
| `approve_confirmation` | `id` | `ack` "Approved <id>" |
| `deny_confirmation` | `id` | `ack` "Denied <id>" |
| `partial_approve_confirmation` | `id`, `approved_indices` (indices into the request's `details`) | `ack` "Resolved <id> (n approved)" |
| `approve_all_confirmations` | `ids` (optional: the ones the client shows) | `ack` "Approved n confirmation(s)": with `ids`, those still pending; without, every pending one |
| `confirmation_response` | `id`, `approved` (bool) or `approved_indices` | none; the older form of the above |

Whichever way a confirmation is resolved (this socket, the TUI, a desktop
notification, `jarvis confirm`), every client gets a fresh
`confirmation_list`.

### Sessions

| `type` | Fields | Reply |
|---|---|---|
| `list_sessions` | | `session_list` (to you) |
| `create_session` | `title` (optional) | `session_switched` with no messages (to everyone) |
| `switch_session` | `id` (a unique prefix is enough) | `session_switched` with the session's messages (to everyone) |
| `rename_session` | `id`, `title` | `session_list` (to everyone) |
| `delete_session` | `id` | `session_list` (to everyone) |

A failure answers `session_error` to the sender only.

### Server setups (#254)

| `type` | Fields | Reply |
|---|---|---|
| `list_setups` | | `setup_list` |
| `setup_completed` | `id` | `ack`; JARVIS runs the server's setup and resumes the request that needed it. Send it after writing the values to dmcp yourself (`dmcp config set --stdin`), as `jarvis setups fill` does: values never cross this socket. |
| `cancel_setup` | `id` | `ack`; the request is told it was cancelled. |

An unknown id answers `error`.

### Settings and providers

| `type` | Fields | Reply |
|---|---|---|
| `get_settings` | | `settings` |
| `set_setting` | `key`, `value` | `config_updated` (to everyone) or `config_error` |
| `list_providers` | | `provider_list` |
| `add_provider` | `ptype`, `model`, and optional `name`, `url`, `api_key`, `temperature` | `provider_list` (to everyone) or `provider_error` |
| `edit_provider` | `name`, `fields` (a dict of the fields to change) | as above. Moving a provider to another address (scheme, host or port) removes its stored key and `headers` unless `fields` has a new `key` (headers go regardless); the editor then also gets a `provider_error` saying so. A `url` must start with `http://` or `https://` and name a host. |
| `remove_provider` | `name` | as above |
| `move_provider` | `name`, `position` (1 = tried first) | as above (#267) |
| `list_models` | optional `url`, `api_key` | `model_list` (to the sender) |
| `pull_model` | `model`, optional `url`, `api_key` | `model_pull` messages, the last with `done: true` |

`list_models` asks the Ollama server at `url` (default `http://localhost:11434`)
what it offers; for Ollama Cloud use `https://ollama.com` with an API key. The
reply is `{"type": "model_list", "url", "models": [{"name", "size",
"modified"}]}`, or the same with `models: []` and an `error` message when the
server cannot be reached or refuses the key. `pull_model` downloads a model
and streams `{"type": "model_pull", "model", "status", "completed", "total",
"done": false}`, ending with `done: true` and, on failure, an `error`. Models on
Ollama Cloud are not downloaded: `pull_model` against it ends with an `error`.
Both carry the API key, so like `provider_list` they stay on this machine and
`jarvis web` does not relay them.

`settings` lists what JARVIS can change while it runs, the same list the
TUI's settings window draws (`jarvis/core/settings.py`), with each one's
current value:

```json
{"type": "settings", "settings": [
  {"key": "CONFIRMATION_MODE", "label": "Confirmation mode", "section": "Safety",
   "kind": "select", "value": "smart",
   "options": [{"value": "smart", "label": "Smart: ask when a tool can change something"}, ...]},
  {"key": "CONFIRMATION_TIMEOUT", "label": "Deny unanswered requests after (seconds)",
   "section": "Safety", "kind": "int", "value": 0, "min": 0,
   "hint": "0 keeps them waiting until you answer"},
  ...
]}
```

`kind` is `select` (one of `options`), `bool`, `int` or `float` (within `min`
and `max` when given), or `sound` (a WAV file's path; `""` sets the bundled
one). A setting in the `Advanced` section tunes JARVIS rather than uses it; a
client can keep that section folded. `set_setting` takes the value as JSON
(`true`, `30`) or as text (`"on"`, `"30"`); JARVIS checks it, saves it to its
config file and applies it at once. `config_updated` carries the value as
applied, typed, and goes to every client, also when the change was made in the
TUI. A key that is not in the list, or a value that does not fit, gets
`config_error` with a message naming the setting (`"Memories per search: at
least 1"`), to the sender only, and nothing is written.

`provider_list` carries API keys as stored in `providers.json`. It stays on
this machine: `jarvis web` never relays it.

### Voice and the daemon

| `type` | What it does |
|---|---|
| `start_listening`, `stop_listening` | Turn the wake-word listener on or off; the state becomes `listening` or `idle`. |
| `shutdown_request` | Saves and stops JARVIS. Every client gets `DAEMON_SHUTDOWN` first. |

## From the daemon

### Replies and state

```json
{"type": "response", "content": "Let me find a tool for that.", "done": true, "comment": true}
{"type": "response", "content": "Python 3.14.8 is installed.", "done": true}
{"type": "response", "content": "Switched to 110d5048.", "done": true, "command": true}
{"type": "state", "state": "processing"}
```

- `comment: true` (#273): one sentence of what JARVIS is about to do. It is
  not the answer, and the state stays as it was.
- `command: true` (#271): the reply to a slash command.
- After an answer the state becomes `idle`, or `speaking` then `idle` when it
  is read aloud.
- States: `idle`, `woken`, `capturing`, `listening`, `processing`,
  `speaking`. A state may carry `meta` (a discard reason).
- `wake_word_detected` has no fields.

### What the user said

Only to clients that subscribed to `user_message`. A message the user sent
from another client, typed into the TUI or `jarvis send`, or spoken, so every
screen shows the whole conversation. The client a message came from does not
get it back: it drew the message when it sent it. Secrets are redacted as the
session stores them. `at` is seconds since the epoch.

```json
{"type": "user_message", "content": "Update my system", "at": 1762200000.5}
```

### Activity (#267)

Only to clients that subscribed to `activity`. What JARVIS is doing at this
moment, for one line of a client's screen. It replaces the previous one; it is
not part of the conversation.

```json
{"type": "activity", "text": "Searching for: execute shell commands…", "kind": "dispatch"}
```

`kind` is `llm`, `dispatch`, `memory` or `activity`.

### The sudo password (#310)

With `SUDO_PASSWORD_POPUP` on (Linux), a root command elevates through `sudo -A`
and JARVIS asks the clients for the password (`runtime/askpass.py`). Only to
clients that subscribed to them:

```json
{"type": "password_request", "id": "9f2c1a07b3d4e5f6", "attempt": 1, "retry": false,
 "prompt": "[sudo] password for toufic:", "at": 1792732504.1,
 "command": "/usr/bin/dmcp call shell execute_command --args '{\"command\": \"pacman -Syu\"}'"}
{"type": "password_request_done", "id": "9f2c1a07b3d4e5f6", "outcome": "answered"}
```

`command` is sudo's own command line and `prompt` what it asks, both read from
`/proc`. A wrong password makes sudo ask again: a new request with `attempt` 2
and `retry: true`; a different prompt (a code, a new password) has `retry:
false`. Only an elevation JARVIS started gets one: a task it dispatched or a
dmcp command it ran. `outcome` is `answered`, `cancelled` (that sudo is killed,
so a cancel is not a failed try), or `gone` (sudo, or the dmcp that ran it,
stopped). Answer with:

| `type` | Fields | Reply |
|---|---|---|
| `password_response` | `id`, `password` | nothing; `password_request_done` to everyone. `error` if the request has ended or the password cannot be one (empty, a line break, over 4096 characters). Never logged. |
| `password_cancel` | `id` | as above |
| `list_password_requests` | | a `password_request` for each one still waiting (for a client that subscribed after one went out) |

The first answer wins. The password goes to that sudo only: it is not logged,
stored, put in the session, or sent to any other client.

### Actions (#271)

Only to clients that subscribed to `action`. What JARVIS did, as blocks: the
TUI folds them in the chat. Each action has a `start`, may have `update`s and
has one `end`, all with the same `id`.

```json
{"type": "action", "phase": "start", "id": "tool:7", "kind": "tool",
 "title": "execute_command (jarvis-shell-mcp)", "detail": "python3 --version", "at": 1792732504.1}
{"type": "action", "phase": "update", "id": "tool:7", "output": ":: Synchronizing package databases...", "at": 1792732534.2}
{"type": "action", "phase": "end", "id": "tool:7", "kind": "tool", "status": "ok",
 "output": "Python 3.14.8", "at": 1792732504.2, "started_at": 1792732504.1}
```

| `kind` | Covers | `title` |
|---|---|---|
| `search` | `search_tools` | `Search: <capability>`; the output lists the servers found |
| `docs` | `get_server_docs` | `Read the tools of <server>` |
| `install` | install, update, uninstall, configure, setup | `Install <server>`, `Update …`, … |
| `confirm` | a confirmation asked | `Confirm <tool> (<server>)`; `detail` is the command |
| `tool` | each dispatched task; `id` is `tool:<pid>` | `<tool> (<server>)`; `detail` is the parameters, a shell command as typed |

- `status` at the end: `ok`, `failed`, `stopped` (killed), `waiting` (a
  setup form is pending for the user), `approved`, `denied`.
- `output` is what a person reads: tool output without its provenance
  wrapper, and a shell result's JSON as what it printed. Output that failed
  the provenance check keeps its `[⚠ UNVERIFIED …]` mark. Long output keeps
  its last 20,000 characters.
- `at` and `started_at` are Unix times in seconds.
- A client that connects mid-way can get an `update` or `end` for an action
  it never saw start. Ignore it, or ask `list_work`.

### Status (#267)

The `/status` health check, as data.

```json
{"type": "status", "full": false, "sections": [
  {"title": "Engines", "checks": [
    {"name": "dispatch", "state": "ok", "detail": "connected"},
    {"name": "Search", "state": "warn", "detail": "keyword only: no embedding model was set up"}]}]}
```

`state` is `ok`, `warn`, `fail` or `off` (not in use).

### Work (#267)

```json
{"type": "work", "work": {
  "requests": [{"id": "a1b2c3d4", "text": "update my system", "since": 1792732400.0}],
  "tasks": [
    {"pid": 7, "kind": "tool", "title": "execute_command (jarvis-shell-system-mcp)",
     "detail": "pacman -Syu --noconfirm", "running_for": 65.2,
     "output": ":: Synchronizing package databases...", "awaiting_password": false,
     "request": "update my system"},
    {"pid": 9, "kind": "timer", "title": "goal_reminder:a1b2c3d4", "detail": "",
     "fires_in": 120, "request": ""}],
  "thinking": 1, "confirmations": 0, "setups": 0}}
```

- `running_for` is `null` for a task JARVIS did not start this run.
- `output` is the task's latest output, up to 2,000 characters.
- `awaiting_password` is `true` while a root call waits for the administrator
  password on the host's own screen (dmcp's stderr says so; #310). Show it as
  waiting, not running.
- `thinking` counts requests waiting on the model.

### Confirmations

```json
{"type": "confirmation_request", "id": "b7c3fdb5",
 "tools": ["com.github.x.jarvis-shell-mcp.execute_command"],
 "details": [{"index": 0, "tool": "com.github.x.jarvis-shell-mcp.execute_command",
              "params": {"command": "rm", "args": ["-r", "/tmp/x"]}, "command": "rm -r /tmp/x"}],
 "timeout": 0}
{"type": "confirmation_list", "confirmations": [
  {"id": "b7c3fdb5", "tool_names": ["…"], "tool_lines": ["…"], "created_at": 1792732504.0,
   "session_id": "a1b2c3d4", "goal_description": "clean up /tmp"}]}
```

`timeout: 0` means the request waits until someone answers it.

### Sessions

```json
{"type": "session_list", "sessions": [{"id": "110d5048-…", "title": "Pacman test",
  "created_at": "…", "updated_at": "…", "summary": "…", "metadata": {}, "entry_count": 12}]}
{"type": "session_switched", "session": {"id": "110d5048-…", "title": "Pacman test"},
 "messages": [{"role": "user", "content": "run pacman -Syu", "timestamp": 1761850000.1},
  {"role": "assistant", "content": "Let me run the update.", "comment": true, "timestamp": 1761850003.4},
  {"role": "action", "id": "tool:4", "kind": "tool", "title": "run_job (shell)",
   "detail": "pacman -Syu  (job update)", "status": "ok", "output": "…",
   "started_at": 1761850003.6, "at": 1761850190.2, "timestamp": 1761850003.6}]}
{"type": "session_error", "message": "No session matches 'zz'"}
```

A session's messages are what was said and, with `role: "action"`, the blocks
of what JARVIS did, each where it started, so a client can draw the chat as it
looked live. A block carries what an action's `start` and `end` carry
(`output` is kept to its last 8,000 characters). A block still running has
`status: "running"`, its output so far and no `at`; its next `update` or `end`
comes live, with the same `id`. A comment has `comment: true`.

### Everything else

| `type` | Fields |
|---|---|
| `settings` | `settings`: the list above |
| `config_updated` | `key`, `value` |
| `config_error` | `key`, `message` |
| `provider_list` | `providers` |
| `provider_error` | `message` |
| `setup_list` | `setups` |
| `client_list` | `clients` (labels) |
| `ack` | `message`, and `for`: the `type` of the request it answers (`stop`, `stop_task`, `approve_confirmation`, …), so a client can show a stop's reply without echoing every decision |
| `error` | `message` |
| `DAEMON_SHUTDOWN` | `state`, `goals`, `session_id`, `timestamp` |

## Through `jarvis web`

The gateway relays this protocol over a WebSocket, narrowed by role
(`docs/web-gateway.md`). It subscribes to `activity`, `action`,
`user_message`, `password_request` and `password_request_done` for the phone:

- **Every role** sees `state`, `response`, `user_message`, `activity`, confirmations, sessions
  (a guest's history without its action blocks), `client_list`, `ack`, `error`,
  and a `DAEMON_SHUTDOWN` cut down to its state.
- **Trusted and owner** also see `action`, `status` and `work`, and may send
  `get_status` and `list_work`. Action blocks carry tool parameters and raw
  output, so they go to the roles trusted to approve what JARVIS runs. A guest
  gets a `confirmation_request` cut to its id and tools, `confirmation_list`
  items without their rendered commands (`tool_lines`) and goal, and sessions
  without their `summary` and `metadata`. Trusted and owner also get the sudo
  password popup and may answer it.
- **The owner** also gets settings and providers (each provider without its
  `api_key` or `headers`, with `api_key_set`), and may change them, approve
  all (naming the `ids` it shows) and turn the wake word on or off.
- **Never relayed:** setups. No role may shut JARVIS down or send a raw
  `confirmation_response`. A chat line the daemon would read as a slash
  command (`/delete`, `/stop all`, ...) needs the role its socket message
  needs (`web_gateway.acts_as`); a refused one is answered with `error` and
  the last `state`, since the page shows "working" once it sends a line.

## Not in the protocol yet

- Stopping a request or a task (#270).
- Filling in a setup from an outside app: plain settings could travel here;
  secrets would not leave the desktop (#267 phase 2, open).
