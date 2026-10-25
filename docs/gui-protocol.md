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
  ["activity", "action"]}`. Without it you get neither, so a client written
  before them never sees an unknown type.
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
| `hello` | `label`, `subscribe` (optional list: `activity`, `action`) | Names this client in `list_clients`, and asks for the pushed events it can show. |
| `list_clients` | | → `client_list` |
| `ping` | | → `pong` |

### What JARVIS is doing (#267)

| `type` | Fields | Reply |
|---|---|---|
| `get_status` | `full` (bool, optional) | `status`: the `/status` health check. `full` also asks the registry for server updates, the one check that goes online. |
| `list_work` | | `work`: what the TUI's F4 view shows. |

Activity and action events are pushed to the clients that subscribed to them in `hello` (see below).

### Confirmations

| `type` | Fields | Reply |
|---|---|---|
| `list_confirmations` | | `confirmation_list` |
| `approve_confirmation` | `id` | `ack` "Approved <id>" |
| `deny_confirmation` | `id` | `ack` "Denied <id>" |
| `partial_approve_confirmation` | `id`, `approved_indices` (indices into the request's `details`) | `ack` "Resolved <id> (n approved)" |
| `approve_all_confirmations` | | `ack` "Approved n confirmation(s)" |
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
| `set_confirmation_mode` | `mode`: `smart`, `ask_all` or `allow_all` | `config_updated` (to everyone) or `config_error` |
| `set_wake_chime_path` | `path` | `config_updated` or `config_error` |
| `reset_wake_chime_path` | | `config_updated` |
| `list_providers` | | `provider_list` |
| `add_provider` | `ptype`, `model`, and optional `name`, `url`, `api_key`, `temperature` | `provider_list` (to everyone) or `provider_error` |
| `edit_provider` | `name`, `fields` (a dict of the fields to change) | as above |
| `remove_provider` | `name` | as above |
| `move_provider` | `name`, `position` (1 = tried first) | as above (#267) |

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

### Activity (#267)

Only to clients that subscribed to `activity`. What JARVIS is doing at this
moment, for one line of a client's screen. It replaces the previous one; it is
not part of the conversation.

```json
{"type": "activity", "text": "Searching for: execute shell commands…", "kind": "dispatch"}
```

`kind` is `llm`, `dispatch`, `memory` or `activity`.

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
     "output": ":: Synchronizing package databases...", "request": "update my system"},
    {"pid": 9, "kind": "timer", "title": "goal_reminder:a1b2c3d4", "detail": "",
     "fires_in": 120, "request": ""}],
  "thinking": 1, "confirmations": 0, "setups": 0}}
```

- `running_for` is `null` for a task JARVIS did not start this run.
- `output` is the task's latest output, up to 2,000 characters.
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
 "messages": [{"role": "user", "content": "run pacman -Syu", "timestamp": "…"}]}
{"type": "session_error", "message": "No session matches 'zz'"}
```

### Everything else

| `type` | Fields |
|---|---|
| `settings` | `confirmation_mode`, `wake_chime_path` |
| `config_updated` | `key`, `value` |
| `config_error` | `key`, `message` |
| `provider_list` | `providers` |
| `provider_error` | `message` |
| `setup_list` | `setups` |
| `client_list` | `clients` (labels) |
| `ack` | `message` |
| `error` | `message` |
| `DAEMON_SHUTDOWN` | `state`, `goals`, `session_id`, `timestamp` |

## Through `jarvis web`

The gateway relays this protocol over a WebSocket, narrowed by role
(`docs/web-gateway.md`). It subscribes to `activity` and `action` for the
phone:

- **Every role** sees `state`, `response`, `activity`, confirmations, sessions,
  `client_list`, `ack`, `error`, and a `DAEMON_SHUTDOWN` cut down to its state.
- **Trusted and owner** also see `action`, `status` and `work`, and may send
  `get_status` and `list_work`. Action blocks carry tool parameters and raw
  output, so they go to the roles trusted to approve what JARVIS runs.
- **Never relayed:** settings, providers, setups. No role may change them,
  shut JARVIS down, or `approve_all_confirmations`.

## Not in the protocol yet

- Stopping a request or a task (#270).
- Filling in a setup from an outside app: plain settings could travel here;
  secrets would not leave the desktop (#267 phase 2, open).
- The remaining runtime settings that the TUI's settings window offers.
