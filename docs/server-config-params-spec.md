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

- Surface *all* required and optional config fields to the user **before** setup
  runs, collected in a single form so the user has complete visibility
- Pre-fill fields from a persistent local store (`jarvis_params.toml`) so users
  never re-enter the same key twice, even across server uninstall/reinstall cycles
- Keep sensitive values (API keys, passwords) entirely out of the LLM conversation
- Feed the LLM only a binary result: configured+installed, or cancelled+why

---

## New File: `jarvis_params.toml`

### Location

`~/.config/jarvis/jarvis_params.toml`

Outside the project directory — user-specific, never committed to git.

### Format

```toml
[io.github.brave.brave-search-mcp-server]
BRAVE_API_KEY = "sk-proj-..."

[io.github.jarvis.shell-system-mcp]
SUDO_PASSWORD = "hunter2"

[com.example.some-server]
API_TOKEN = ""          # saved empty — user started but didn't finish
ENDPOINT = "https://api.example.com/v1"
```

- Sections keyed by server ID
- Values are plain strings — no type coercion
- Empty string = user acknowledged the field but left it blank
- Absent key = field has never been seen for this server

### Access pattern

- **Read** on modal open: pre-fill fields that have a saved value
- **Write** on any field change: auto-save — no explicit save button needed
- **Never** passed to the LLM — only the system layer reads/writes this file

### Sensitive values

Plaintext for v1 — same as `.netrc`, `~/.config/gh/hosts.yml`, etc.
Future: encrypt at rest via `jarvis_keys.c` kernel keyring in `linux-jarvisos`.
The `sensitive` flag controls UI masking only, not storage format.

---

## Manifest Schema: `configurableProperties`

Defined in the registry and now properly typed in dmcp (`ConfigurableProperty`
struct in `dmcp/src/models.rs`, serialized by `dmcp info --json`). Each entry:

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

## Install Flow

```
LLM: {"action": "install_server", "server_id": "io.github.brave.brave-search-mcp-server"}
  │
  ▼
1. dmcp install --no-setup <server_id>
   Copies files, writes manifest — does NOT run the setup script yet.
  │
  ▼
2. Read configurableProperties from installed manifest
   → if empty → jump to step 5
  │
  ▼
3. Read jarvis_params.toml → pre-fill any saved values for this server_id
  │
  ▼
4. Open ServerConfigModal
   → pre-fill fields from saved values + manifest defaults
   → user fills gaps, edits pre-filled values if desired
   → auto-save every keystroke to jarvis_params.toml
  │
  ├─ User cancels ──────────────────────────────────────────────┐
  │                                                             ▼
  │                          LLM gets: INSTALL_CANCELLED        │
  │                          missing required: [BRAVE_API_KEY]  │
  │                          LLM responds to user explaining    │
  │                          what is needed                     │
  │                                                             │
  └─ User confirms (all required fields filled) ────────────────┤
  │                                                             │
  ▼                                                             │
5. dmcp config <server_id> set KEY value  (one per field)
   → stores values in manifest.config before setup runs
  │
  ▼
6. dmcp setup <server_id>
   → dmcp injects manifest.config as MCP_CONFIG_* env vars
   → setup script handles them however it needs to
   → server developer owns all setup logic; JARVIS is just the conduit
  │
  ├─ Setup fails ───────────────────────────────────────────────┤
  │     → LLM gets: INSTALL_ERROR: setup failed — <reason>      │
  │       and explains it to the user                           │
  │                                                             │
  └─ Setup succeeds ────────────────────────────────────────────┘
  │
  ▼
7. auto_index_server → get_server_docs → LLM dispatches
```

### Separation of concerns

JARVIS owns: collecting values, persisting to `jarvis_params.toml`, calling
`dmcp config set`, triggering `dmcp setup`. It does not interpret config values.

MCP server developer owns: what the setup script does with the values it
receives, whether setup needs them at all or only at runtime, any validation.

---

## Runtime Config Error Flow (no reinstall needed)

When a server is already installed but a config value is wrong or expired:

```
list_server_tools → stderr: "API key invalid"
  │
  ▼
format_server_docs detects config-related keywords in error
  → "server requires configuration — use configure_server, then retry get_server_docs"
  │
  ▼
LLM does not know the new value → responds: "Your API key appears invalid.
  Please provide a new one."
  │
  ▼
User provides new key in chat
  │
  ▼
LLM: configure_server → system writes to jarvis_params.toml + dmcp config set
  → LLM retries get_server_docs → tools appear → dispatch
```

Two guardrails apply on the `configure_server` path: placeholder-looking values
are rejected (`CONFIGURE_BLOCKED` → the LLM must ask the user for the real
value), and config keys are normalized to env-var form (e.g. `--brave-api-key`
→ `BRAVE_API_KEY`) before `dmcp config set` / `jarvis_params.toml`.

---

## UI: ServerConfigModal

### Async bridge

`_handle_install_server` in `root_actions.py` is an async coroutine. The modal
is a Textual `ModalScreen`. The backend pauses via a plain `asyncio.Future`:

```python
future = asyncio.get_event_loop().create_future()
await app.config_modal_callback(server_id, server_name, server_desc, props, saved, future)
result = await future   # coroutine suspends; Textual event loop stays free
                        # modal calls future.set_result(ConfigModalResult(...)) on submit/cancel
```

(`tui/lifecycle.py` assigns `jarvis.config_modal_callback = app._open_config_modal`
— there is no generic `tui_callback` registry.)

`await future` does not block the event loop — Textual continues rendering and
handling input. No new manager class or event type needed; just a registered
TUI callback (same pattern already used in `lifecycle.py`).

### Layout

```
┌─ Configure: Brave Search MCP ─────────────────────────────────┐
│  Search the web using the Brave Search API.                    │
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

1. **Saved** — fields with a value in `jarvis_params.toml`. Shown at top; editable.
2. **Required** — `required: true` with no saved value. Red border until non-empty.
   If all required fields are already saved, shows "(all required fields are filled)".
3. **Optional** — `required: false`. Default pre-filled if `default` is set.

### Field rendering

- Label: bold. Description: dim italic, below the input.
- Sensitive: `password=True` on the Textual `Input` + `[show]` toggle.
- Required + empty: red border. Required + filled: green border.
- Auto-filled from saved: neutral border + `✓ saved` badge.

### Footer

- Status: `N/M required ✓` or `N/M required — X missing`
- `[Cancel]` always enabled. `[Install & Save]` disabled until all required fields filled.

### Keyboard

| Key | Action |
|-----|--------|
| `Tab` / `Shift+Tab` | Move between fields |
| `Enter` | Submit (if `[Install & Save]` enabled) |
| `Escape` | Cancel |
| `Ctrl+H` | Toggle show/hide on focused sensitive field |

---

## File & Code Changes

| File | Change |
|------|--------|
| `jarvis/core/params_store.py` | New — read/write `jarvis_params.toml` per server |
| `jarvis/tui/server_config_modal.py` | New — Textual `ModalScreen` with grouped field form |
| `jarvis/runtime/root_actions.py` | `_handle_install_server`: read manifest, open modal, dmcp config set, dmcp setup |
| `jarvis/dispatch/dmcp_registry.py` | New fn `get_server_manifest(server_id)`: read installed manifest; fall back to registry clone |
| `jarvis/dispatch/dmcp_registry.py` | New fn `run_server_setup(server_id)`: call `dmcp setup <id>`, capture stderr |
| `jarvis/tui/lifecycle.py` | Assign `jarvis.config_modal_callback = app._open_config_modal` |
| `mcp-registry/servers/*/manifest.json` | Add `configurableProperties` to servers that require config |
| `dmcp/src/models.rs` | **Done** — `ConfigurableProperty` struct + field on `Manifest` |

### `params_store.py` interface

```python
class ParamsStore:
    def __init__(self, server_id: str): ...
    def get(self) -> dict[str, str]: ...
    def set(self, key: str, value: str) -> None: ...
    def set_many(self, values: dict[str, str]) -> None: ...
```

Atomic writes: write to temp file → rename.

### Modal result type

```python
@dataclass
class ConfigModalResult:
    confirmed: bool
    values: dict[str, str]        # all field values at submit/cancel
    missing_required: list[str]   # required keys that were empty on cancel
```

---

## Out of Scope (this iteration)

- Encrypting `jarvis_params.toml` at rest
- Standalone "manage saved params" screen
- Multi-user / per-session param scoping
- Format validation beyond "non-empty required"

## Changelog — corrected claims

*2026-07-22:* CLI corrected to `dmcp install --no-setup` and `dmcp config <server_id> set` (the underscore spelling also fixed in `dmcp_registry.py`); ParamsStore snippet matches the constructor-bound implementation; async bridge uses `config_modal_callback` (no generic tui_callback registry); setup-failure branch reports via the LLM only; configure_server guardrails documented (CONFIGURE_BLOCKED on placeholders, env-var key normalization).
