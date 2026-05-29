# Tool Discovery Redesign

## Problem

The current `run` action passes user intent keywords directly into a text-search
against the MCP registry. This is brittle:

- "check python version" → keywords `["python", "version"]`
- `dmcp browse -k python` → returns `calculator-py` (has `"python"` in keywords because it is *implemented* in Python)
- LLM is shown calculator tools and correctly refuses them
- System loops, eventually gives up

Root cause: keyword extraction from user intent is not the same as reasoning
about capability. The LLM knows that "check python version requires running a
shell command." That inference never happens — the system bypasses it.

Secondary cause: embedding search (which would semantically match "execute shell
command" to `super-shell-mcp`) never fires because `EMBEDDING_SEARCH_THRESHOLD`
defaults to 100 and the registry has ~24 servers.

---

## New Workflow

```
User: "Can you check my python version?"
  │
  ▼
ROOT (Turn 1) — reasons about capability
  Output: {"action": "search_tools", "capability": "execute shell commands"}
  │
  ▼
System — embedding search on full vector index (installed + registry)
  Injects: SEARCH_RESULTS with top_k server summaries + installed status
  │
  ▼
ROOT (Turn 2) — picks best match
  Output: {"action": "get_server_docs", "server_id": "super-shell-mcp"}
         OR: {"action": "install_server", "server_id": "super-shell-mcp"}
             if the chosen server is not yet installed
  │
  ▼
System — fetches full tool list from installed server (dmcp tools <id> --json)
  Injects: SERVER_DOCS with tool names, descriptions, parameter schemas
  │
  ▼
ROOT (Turn 3) — knows the server and its tools, dispatches
  Output: {"action": "dispatch", "tasks": [
    {"server": "super-shell-mcp", "tool": "execute", "params": {"command": "python --version"}}
  ]}
  │
  ▼
Execute → DISPATCH_RESULT → ROOT (Turn 4) → respond to user
```

If `search_tools` returns nothing: ROOT retries with a different capability
description. No hard cap on retries — the LLM decides when to give up, but the
system prompt makes clear it should make genuinely different attempts before
doing so.

If selected server needs configuration before it works: ROOT outputs
`configure_server` to set required values, then proceeds.

---

## New LLM Actions

### `search_tools`

```json
{
  "action": "search_tools",
  "capability": "execute shell commands",
  "goal_updates": []
}
```

- `capability`: A description of the *capability needed*, not the user's words.
  The LLM must reason about what kind of tool would satisfy the request.
- System performs embedding search on the full vector index (installed + uninstalled).
- Returns `SEARCH_RESULTS` block injected into next ROOT context.

**`SEARCH_RESULTS` format injected by system:**
```
SEARCH_RESULTS (top 5 for "execute shell commands"):
  super-shell-mcp [INSTALLED] — Execute shell commands, run scripts, manage processes
  filesystem-mcp  [INSTALLED] — Read, write, and list files and directories
  terminal-mcp    [available] — Full terminal emulation via MCP
  ...
```

### `get_server_docs`

```json
{
  "action": "get_server_docs",
  "server_id": "super-shell-mcp",
  "goal_updates": []
}
```

- System calls `dmcp tools <server_id> --json`, formats full tool list.
- Returns `SERVER_DOCS` block injected into next ROOT context.

**`SERVER_DOCS` format:**
```
SERVER_DOCS: super-shell-mcp
  execute — Run a shell command and return stdout/stderr
    params: {"command": "string (required)", "timeout": "integer (optional)"}
  read_file — Read a file from disk
    params: {"path": "string (required)"}
  ...
```

### `install_server`

```json
{
  "action": "install_server",
  "server_id": "super-shell-mcp",
  "goal_updates": []
}
```

- System calls `dmcp install <server_id>`.
- On success: auto-indexes the server, then transitions to `get_server_docs`
  automatically (no extra LLM turn needed for the docs fetch after install).
- Returns `INSTALL_RESULT: super-shell-mcp installed` or `INSTALL_ERROR: ...`

### `configure_server`

```json
{
  "action": "configure_server",
  "server_id": "some-api-server",
  "config": {
    "API_KEY": "...",
    "BASE_URL": "https://..."
  },
  "goal_updates": []
}
```

- System calls `dmcp config <server_id> set <key> <value>` for each entry.
- Used when `SERVER_DOCS` or install output indicates required configuration.
- Returns `CONFIGURE_RESULT: set 2 values on some-api-server`.

### `dispatch` (unchanged)

```json
{
  "action": "dispatch",
  "tasks": [
    {"server": "super-shell-mcp", "tool": "execute", "params": {"command": "python --version"}}
  ],
  "goal_updates": []
}
```

No change to dispatch semantics. ROOT only reaches this action after it has
seen `SERVER_DOCS` and knows the tool names and parameter schemas.

---

## Actions Removed / Replaced

| Old action | Replacement | Reason |
|------------|-------------|--------|
| `run` | `search_tools` → `get_server_docs` → `dispatch` | `run` hid all reasoning; keyword extraction was the failure point |

The `run` action and all supporting code (`_run_handle`, `keyword_fallback`,
`tool_discovery.py`) are deleted. The multi-step flow replaces it entirely.

---

## Embedding Search

The vector index is already built and populated via `dmcp sync-index`. The
change needed is in `config.py`:

```python
# Before (never fires for small registries):
EMBEDDING_SEARCH_THRESHOLD = int(os.getenv("EMBEDDING_SEARCH_THRESHOLD", "100"))

# After (embedding is the default; keyword only as emergency fallback):
EMBEDDING_SEARCH_THRESHOLD = int(os.getenv("EMBEDDING_SEARCH_THRESHOLD", "3"))
```

With `ENFORCE_EMBEDDING_SEARCH=true` in `.env`, embedding always fires regardless
of count.

The `ALLOW_EMBEDDING_SEARCH` flag and `select_discovery_mode` logic remain for
environments where an embedding model is not available (they fall back to keyword
in that case).

---

## System Prompt Changes

The `run` example block in `LLM_ROOT_PROMPT` is replaced with:

```
search_tools — Find an MCP server that can handle a task.
               Think about what CAPABILITY you need, not what the user said.
               WRONG:   {"action": "search_tools", "capability": "check python version"}
               CORRECT: {"action": "search_tools", "capability": "execute shell commands"}

get_server_docs — Fetch full tool list for a server from SEARCH_RESULTS.
                  {"action": "get_server_docs", "server_id": "<id from SEARCH_RESULTS>"}

install_server — Install a server that appears in SEARCH_RESULTS as [available].
                 {"action": "install_server", "server_id": "<id>"}

configure_server — Set required config on an installed server before using it.
                   {"action": "configure_server", "server_id": "<id>", "config": {"KEY": "value"}}
```

And the context section adds:
```
SEARCH_RESULTS means run get_server_docs on the best match.
If SEARCH_RESULTS is empty: retry search_tools with a different capability description.
Make at least 2 genuinely different attempts before telling the user you cannot help.
```

---

## Files to Change

| File | Change |
|------|--------|
| `jarvis/config.py` | Lower `EMBEDDING_SEARCH_THRESHOLD` to 3; update `LLM_ROOT_PROMPT` with new actions |
| `jarvis/core/command_parser.py` | Add `search_tools`, `get_server_docs`, `install_server`, `configure_server`; remove `run` and `find_tools` |
| `jarvis/runtime/root_actions.py` | Add handlers for 4 new actions; remove `_run_handle`, `_first_candidate_id`, `_continue_root` (or keep `_continue_root` as internal helper) |
| `jarvis/runtime/root_context.py` | Add `format_search_results()` and `format_server_docs()` context builders |
| `jarvis/dispatch/adapter.py` | Add `search_tools()` (calls vector search), `get_server_docs()` (calls `dmcp tools`), `install_server()` (already exists), `configure_server()` (calls `dmcp config set`) |
| `jarvis/dispatch/tool_discovery.py` | Delete (keyword fallback fully removed) |
| `jarvis/dispatch/dmcp_registry.py` | Keep `install_server`, `list_server_tools`, `run_dmcp`; delete `search_servers`, `_local_installed_servers`, `list_visible_servers` |

---

## What Stays the Same

- `dispatch` action and all dispatch execution logic
- Confirmation gate (DANGEROUS-tier tools still require confirmation)
- Memory actions (`store`, `recall`, `search_memory`, `list_memory`)
- Goal tracking
- Signal handling from the Rust dispatch binary
- Voice/TUI/socket input paths

---

## Documentation Caching (Future)

After the new workflow is stable, add server doc caching to avoid re-fetching
on every use:

- Store in contextor: theme `mcp_server_docs:<server_id>`, global scope
- On `get_server_docs`: check cache first, inject if hit, fetch+store if miss
- Invalidate on `install_server` (docs may change after reinstall)

Not in scope for the initial implementation.
