# Memory Management — Update & Forget

## Problem

Contextor accumulates memories over time with no mechanism to correct or remove
individual entries. Stale memories are worse than no memories — the LLM acts
confidently on wrong information. Three failure modes:

- **Supersede**: user moves a project folder; old path memory persists alongside
  new one, both retrieved, LLM can't tell which is current
- **Forget**: user asks JARVIS to stop remembering something; no action exists
- **Conflict**: two memories contradict each other; no way to resolve without
  deleting the whole theme

## Design

### Single action: `update_memory`

Rather than separate `update` and `delete` actions, one action covers both.

```json
{"action": "update_memory", "theme": "<theme>", "content": "<new content>"}
```

- **Non-empty content** → embed new content, replace all entries under the theme
  with a single new entry carrying the new vector. Old entries gone.
- **Empty content** → theme is deleted entirely. Effectively a forget.

Why empty = forget works: an embedding of an empty string has near-zero cosine
similarity to any meaningful query. Even if the entry persists in storage, it
will never surface in retrieval. Deleting is cleaner but not strictly necessary.

### Why not separate update + delete actions

The LLM's action surface should stay small. One action that handles both cases
reduces prompt complexity and the chance the LLM picks the wrong one. The
caller doesn't need to decide — empty content means forget.

### Granularity: theme-level, not entry-level

Contextor entries are identified internally by auto-generated IDs that the LLM
never sees. The only identifier the LLM has is the `theme` name (visible via
`list_memory`). Theme-level replacement is therefore the right granularity —
it matches what the LLM can actually reason about.

If fine-grained entry-level updates are needed in future, contextor can expose
an `UpdateEntry { entry_id, content, vector }` command. That is out of scope
here.

## Changes required

### 1. Contextor — new `ReplaceTheme` command

Current contextor has `Store` (add entry) and `Delete` (remove all entries for
theme) but no atomic replace. Add:

```rust
ReplaceTheme {
    theme: String,
    content: String,   // empty string = delete only, no new entry stored
    vector: Vec<f32>,  // ignored when content is empty
    #[serde(default)]
    metadata: Option<Value>,
    #[serde(default)]
    session_id: Option<String>,
}
```

Behaviour:
1. Delete all existing entries for `theme` (+ optional `session_id` scope)
2. If `content` is non-empty: store new entry with supplied vector
3. Return `{ "ok": true, "replaced": true }` or `{ "ok": true, "deleted": true }`

This is atomic within a single SQLite transaction — no window where the theme
is briefly empty while the new entry is being written.

### 2. JARVIS — `jarvis/core/contextor.py` (or equivalent adapter)

Add `update_memory(theme, content, session_id)` function:
- If content is empty: send `Delete { theme }` to contextor
- If content is non-empty: embed content via the embedding model, send
  `ReplaceTheme { theme, content, vector }` to contextor

### 3. JARVIS — `jarvis/core/command_parser.py`

Add `update_memory` to `VALID_ACTIONS`. Parser:

```python
def _parse_update_memory(data: dict) -> dict:
    return {
        "action": "update_memory",
        "theme": data.get("theme", ""),
        "content": data.get("content", ""),
    }
```

### 4. JARVIS — `jarvis/runtime/root_actions.py`

Add handler:

```python
async def _handle_update_memory(parsed: dict, adapter, ...) -> str:
    theme = parsed.get("theme", "").strip()
    content = parsed.get("content", "").strip()
    if not theme:
        return "update_memory requires a theme"
    await adapter.update_memory(theme, content)
    if content:
        return f"Memory updated: {theme}"
    return f"Memory forgotten: {theme}"
```

### 5. JARVIS — `jarvis/config.py` (root prompt)

Add to the memory action block:

```
update_memory — Rewrite an existing memory or forget it entirely.
  Use when the user corrects outdated information or asks you to forget something.
  Pass empty string for content to forget.
  {{"action": "update_memory", "theme": "<theme>", "content": "<new content or empty>"}}
```

## Example flows

### Updating a stale location

```
User: I moved my projects to ~/Dev
LLM:  {"action": "update_memory", "theme": "projects_location",
        "content": "Projects are stored at /home/yakup/Dev"}
```

Old memory gone. New memory embedded with fresh vector. Next time the user asks
JARVIS to clone a repo, retrieval returns the correct path.

### Forgetting a project

```
User: Forget about the Game project
LLM:  {"action": "update_memory", "theme": "project:Game", "content": ""}
```

All entries under `project:Game` deleted. Subsequent searches return nothing
for that theme.

### Resolving a conflict

```
User: My GitHub username is now yakupdev, not yakupatahanov
LLM:  {"action": "update_memory", "theme": "github_username",
        "content": "GitHub username is yakupdev"}
```

Single replace removes both old entries, stores one authoritative entry.

## What this does not cover

- **Automatic staleness detection**: JARVIS won't proactively notice that a
  stored path no longer exists on disk. That's a future improvement (e.g. a
  periodic `Prune`-style validation pass).
- **Partial updates within a theme**: theme-level replace is all-or-nothing.
  If a theme has 10 entries and you want to correct one, replace the whole
  theme with the corrected content. Fine-grained entry updates require
  exposing entry IDs to the LLM, which adds complexity with little benefit
  for the current use cases.
- **Undo**: replaced entries are gone. No soft-delete or history is kept.
