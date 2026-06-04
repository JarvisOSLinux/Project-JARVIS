# Server Config Params — Design Spec

## Problem

When JARVIS installs an MCP server that requires configuration (API keys,
passwords, endpoints), the current system:

1. Installs the server
2. Tries to list its tools — server fails to start (missing required env var)
3. Surfaces a misleading "no tools found" message
4. LLM loops on reinstall/uninstall, never resolving the root cause

Even with the improved error surfacing (stderr now passed through), the LLM
still can't act usefully — it doesn't know what value to supply, and it must
never see or invent sensitive values like API keys.

---

## Goals

- Surface *all* required and optional config fields to the user **before** install runs
- Pre-fill fields from a persistent local store (`jarvis_params.toml`) so users
  never re-enter the same key twice, even across server uninstall/reinstall cycles
- Keep sensitive values (API keys, passwords) entirely out of the LLM conversation
- Give the user complete visibility: what was auto-filled, what is required, what
  is optional — all at once, in one place
- Feed the LLM only a binary result: configured+installed, or cancelled+why

---

## New File: `jarvis_params.toml`

### Location

`~/.config/jarvis/jarvis_params.toml`

Outside the project directory — user-specific, never committed to git.
Add to `.gitignore` if the file ends up adjacent to the project.

### Format

```toml
[io.github.brave.brave-search-mcp-server]
BRAVE_API_KEY = "sk-proj-..."

[io.github.jarvis.shell-system-mcp]
SUDO_PASSWORD = "hunter2"

[com.example.some-server]
API_TOKEN = ""          # saved empty — user started but didn't finish
ENDPOINT = "https://api.example.com/v1"   # optional, provided
```

- Sections keyed by server ID (matches `configurableProperties.key` in the manifest)
- Values are plain strings — no type coercion
- Empty string = user acknowledged the field but left it blank
- Absent key = field has never been seen/filled for this server

### Access pattern

- **Read** on modal open: pre-fill fields that have a saved value
- **Write** on any field change (auto-save — no explicit save button needed)
- **Read** on install: inject present values as environment variables for dmcp
- **Never** passed to the LLM — only the system layer reads/writes this file

### Sensitive values

The TOML file stores values in plaintext for now. Future: encrypt at rest using
the Linux kernel keyring (`jarvis_keys.c` in `linux-jarvisos`). The `sensitive`
flag in the manifest controls masking in the UI, not storage format.

---

## Manifest Schema: `configurableProperties`

Already defined in the registry. Each entry:

```json
{
  "key": "BRAVE_API_KEY",
  "label": "Brave Search API Key",
  "description": "Get a free key at brave.com/search/api (2000 req/month)",
  "sensitive": true,
  "required": true,
  "default": null
}
```

| Field | Type | Purpose |
|-------|------|---------|
| `key` | string | Env var name injected at server startup |
| `label` | string | Human-readable field name shown in the UI |
| `description` | string | Shown below the input; explains how to get the value |
| `sensitive` | bool | If true, mask input and show toggle button |
| `required` | bool | If true, blocks install until non-empty |
| `default` | string\|null | Pre-filled if no saved value exists |

Servers with no `configurableProperties` (or an empty array) skip the modal
entirely and go straight to install.

---

## Install Flow (updated)

```
LLM: {"action": "install_server", "server_id": "io.github.brave.brave-search-mcp-server"}
  │
  ▼
1. Fetch manifest from dmcp/registry
   → extract configurableProperties
   → if empty → skip to step 5
  │
  ▼
2. Read jarvis_params.toml
   → find saved values for this server_id
  │
  ▼
3. Open ServerConfigModal (Textual ModalScreen)
   → pre-fill fields from saved values + manifest defaults
   → user fills gaps, edits pre-filled values if desired
   → auto-save every keystroke to jarvis_params.toml
  │
  ├─ User cancels ──────────────────────────────────────────────┐
  │                                                             ▼
  │                          LLM gets: INSTALL_CANCELLED        │
  │                          missing required: [BRAVE_API_KEY]  │
  │                          LLM responds to user               │
  │                          explaining what is needed          │
  │                                                             │
  ├─ User confirms (all required fields filled) ─────────────── ┤
  │                                                             │
  ▼                                                             │
4. Run dmcp install (bare) → dmcp config set for each value     │
  │                                                             │
  ├─ Install fails ─────────────────────────────────────────────┤
  │     → Show error to user directly in TUI (not via LLM)      │
  │     → LLM gets: INSTALL_ERROR: <reason>                     │
  │     → LLM tells user install failed, no retry loop          │
  │                                                             │
  └─ Install succeeds ──────────────────────────────────────────┘
  │
  ▼
5. auto_index_server → list_server_tools → format_server_docs
   → LLM dispatches normally
```

---

## Runtime Config Error Flow (no reinstall needed)

When a server is already installed but a config value is wrong or expired:

```
list_server_tools → stderr: "API key invalid" or "token expired"
  │
  ▼
format_server_docs detects config-related keywords in error
  → "SERVER_DOCS: brave-search — server requires configuration.
     Error: API key invalid.
     Use configure_server to update the value, then retry get_server_docs."
  │
  ▼
LLM: {"action": "configure_server", "server_id": "...", "config": {"BRAVE_API_KEY": "???"}}

  BUT — LLM does not know the new value.
  LLM must respond: "Your Brave API key appears to be invalid. Please provide a new one."
  │
  ▼
User provides new key in chat
  │
  ▼
LLM: configure_server with the new value
  → system updates jarvis_params.toml
  → dmcp config set injects new value
  → LLM retries get_server_docs → tools appear → dispatch
```

Note: for runtime errors the user types the value into the chat. The LLM passes
it through to configure_server. This is acceptable for re-configuration flows
(the key is already "known" to the user at this point). The modal is only used
for the initial install.

---

## UI: ServerConfigModal

### When it appears

Triggered by the `install_server` action handler in `root_actions.py`, before
dmcp runs. The LLM is paused — no further LLM calls until the modal resolves.

### Layout

```
┌─ Configure: Brave Search MCP ─────────────────────────────────┐
│  Search the web using the Brave Search API.                    │
│  brave.com/search/api — free tier: 2000 req/month             │
│                                                                │
│  ── Saved ──────────────────────────────────────────────────── │
│  Brave Search API Key                                          │
│  [••••••••••••••••••••••••••]  [show]              ✓ saved    │
│  Get a free key at brave.com/search/api                        │
│                                                                │
│  ── Required ───────────────────────────────────────────────── │
│  (all required fields are filled)                              │
│                                                                │
│  ── Optional ───────────────────────────────────────────────── │
│  Timeout (seconds)                                             │
│  [30                        ]                                  │
│  Request timeout in seconds                                    │
│                                                                │
│  ──────────────────────────────  1/1 required ✓  ──────────── │
│                    [Cancel]        [Install & Save]            │
└────────────────────────────────────────────────────────────────┘
```

### Sections (in order)

1. **Saved** — fields that have a value in `jarvis_params.toml`. Always shown
   at the top; user can review and edit before confirming.
2. **Required** — fields with `required: true` and no saved value. Red border
   until non-empty. If all required fields are already saved, this section
   shows "(all required fields are filled)".
3. **Optional** — fields with `required: false`. Shown with default pre-filled
   if `default` is set. User can leave them or change them.

### Field rendering

- Label: bold
- Description: dim, italic, shown below the input
- Sensitive field: `password=True` on the Textual `Input` widget + `[show]`
  toggle button that switches between masked and revealed
- Required + empty: red border on the Input widget
- Required + filled: green border
- Auto-filled from saved: neutral border + `✓ saved` badge on the right

### Footer

- Status: `N/M required ✓` or `N/M required — X missing`
- `[Cancel]` — always enabled; closes modal, returns cancellation result
- `[Install & Save]` — disabled until all required fields are non-empty

### Keyboard

| Key | Action |
|-----|--------|
| `Tab` / `Shift+Tab` | Move between fields |
| `Enter` | If `[Install & Save]` is enabled, submit |
| `Escape` | Cancel (same as `[Cancel]`) |
| `Ctrl+H` | Toggle show/hide on the focused sensitive field |

---

## File & Code Changes

| File | Change |
|------|--------|
| `jarvis/core/params_store.py` | New — read/write `jarvis_params.toml`; get/set per server |
| `jarvis/tui/server_config_modal.py` | New — Textual `ModalScreen` with grouped field form |
| `jarvis/runtime/root_actions.py` | `_handle_install_server`: fetch manifest config fields, open modal if any, inject values before dmcp call |
| `jarvis/dispatch/dmcp_registry.py` | `install_server`: accept optional `env` dict; inject as env vars for the dmcp subprocess |
| `jarvis/dispatch/adapter.py` | `install_server`: pass env through to dmcp_registry |
| `jarvis/dispatch/dmcp_registry.py` | New fn `get_server_manifest(server_id)`: read `configurableProperties` from registry manifest file (pre-install) or `dmcp info --json` (post-install) |
| `mcp-registry/servers/*/manifest.json` | Add `configurableProperties` to any server that requires config (Brave, etc.) |

### `params_store.py` interface

```python
class ParamsStore:
    def get(self, server_id: str) -> dict[str, str]: ...
    def set(self, server_id: str, key: str, value: str) -> None: ...
    def set_many(self, server_id: str, values: dict[str, str]) -> None: ...
```

Backed by `~/.config/jarvis/jarvis_params.toml`. Thread-safe writes via
atomic replace (write temp file → rename).

### Modal result type

```python
@dataclass
class ConfigModalResult:
    confirmed: bool
    values: dict[str, str]          # all field values at time of submit/cancel
    missing_required: list[str]     # keys that were required but empty on cancel
```

---

## Resolved Design Questions

### 1. dmcp env injection

`dmcp install` does NOT accept env vars on the command line. Config is set
*after* install via `dmcp config set <server_id> KEY value`, which writes
`"config": {"KEY": "value"}` into the installed manifest JSON. When the server
process is later spawned, dmcp's `config_to_env()` converts that map to actual
environment variables passed to the process via `.envs()`.

**Consequence for our flow**: The modal fires before the *first* `get_server_docs`
call, not before `install`. Correct sequence:

```
install (bare, no config) → configure (dmcp config set per key) → get_server_docs
```

The existing `configure_server` action already calls `dmcp config set` correctly.
The modal just needs to collect values and call `configure_server` before the
server is first started.

### 2. Manifest fetch for `configurableProperties`

`dmcp info <server_id> --json` exists and returns the full manifest JSON.
However, it only works for *installed* servers. For servers not yet installed
(the common case — user is about to install), we read `configurableProperties`
directly from the registry manifest file.

Two sources, in priority order:
1. Registry clone: `~/.local/share/mcp/registry/<server_id>/manifest.json`
   (available before install)
2. `dmcp info <server_id> --json` (available after install, for re-configuration)

`get_server_manifest()` in `dmcp_registry.py` should try the registry path first,
fall back to `dmcp info --json`.

### 3. `configurableProperties` is Python-owned, not dmcp-owned

dmcp's `Manifest` struct has no `configurableProperties` field — the field exists
in the JSON but dmcp silently ignores it. dmcp only knows about the flat `config`
map (the values, not the schema). A comment in `dmcp/src/run.rs` notes that key
names "must match `configurableProperties.key`" but dmcp does not enforce this.

**Consequence**: We read `configurableProperties` ourselves in Python (from the
manifest JSON), build the modal form from it, collect values, then call
`dmcp config set` with matching key names. The schema interpretation is entirely
our responsibility.

### 4. Encryption at rest

For v1, plaintext in `jarvis_params.toml` is acceptable — same as how most CLI
tools store API keys in config files (`.netrc`, `~/.config/gh/hosts.yml`, etc.).
Future: integrate with `jarvis_keys.c` kernel keyring already present in
`linux-jarvisos`.

4. **Re-entering values during reinstall**: If a server is uninstalled and
   reinstalled, the modal should pre-fill from `jarvis_params.toml` without
   asking the user to re-enter anything they already provided. This is the
   primary motivation for the persistent store.

---

## Out of Scope (for this iteration)

- Encrypting `jarvis_params.toml` at rest
- A standalone "manage saved params" screen (edit/delete saved values outside
  of the install flow)
- Multi-user / per-session param scoping
- Params validation beyond "non-empty required" (e.g. format checking an API key)
