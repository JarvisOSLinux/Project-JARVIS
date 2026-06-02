# Memory Management — Update, Forget & Memento

## Problem

Stored memories become stale over time with no way to correct or remove them.
Stale active memories are worse than none — the LLM acts confidently on wrong
information. Three failure modes:

- **Supersede**: user moves a project folder; old path persists, LLM uses it
- **Forget**: user asks JARVIS to stop remembering something; no action exists
- **Contaminated search**: vector search surfaces old entries alongside current
  ones, LLM can't tell which is authoritative

## Design

### Two-tier memory model

| Tier | Description | Searched by RAG? |
|---|---|---|
| **Active** | One current entry per theme — the authoritative truth | Yes |
| **Memento** | Archived history of previous entries for a theme, with timestamps | No — explicit only |

`search_memory` and all RAG retrieval operate on **active entries only**. Mementos
are never surfaced automatically — the LLM must explicitly request them. This
keeps retrieval clean and current with no stale contamination.

### Two LLM actions

#### `update_memory`

```json
{"action": "update_memory", "theme": "<theme>", "content": "<new content>"}
```

- **Non-empty content** → current active entry moves to mementos table
  (archived with timestamp), new entry becomes active
- **Empty content** → current active entry moves to mementos, no new active
  entry created. The theme is effectively forgotten. `search_memory` returns
  nothing for it.

One action covers both update and forget. No separate `forget_memory` needed.

#### `peek_memento`

```json
{"action": "peek_memento", "theme": "<theme>", "limit": 5}
```

Returns the last N archived entries for a theme, newest first, each with a
timestamp. The LLM calls this explicitly when it wants to understand how
something evolved or recover previous context. It is never called automatically.

### Why not expose memento to RAG

If mementos were included in vector search, an old entry could score higher
than the current one for certain queries, causing the LLM to act on outdated
information without realising it. Keeping mementos out of the search index
means active memory is always authoritative and RAG results are always clean.

The LLM reaching for `peek_memento` is a deliberate signal that history is
relevant to the current task — not an accident of retrieval scoring.

## Changes required

### 1. Contextor — new `mementos` table

```sql
CREATE TABLE IF NOT EXISTS mementos (
    id          TEXT PRIMARY KEY,
    theme       TEXT NOT NULL,
    content     TEXT NOT NULL,
    vector      BLOB NOT NULL,
    stored_at   REAL NOT NULL,   -- when the entry was originally stored
    archived_at REAL NOT NULL,   -- when it was moved to mementos
    metadata    TEXT,
    session_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_memento_theme_archived
    ON mementos (theme, archived_at);
```

### 2. Contextor — `ReplaceActive` command

```rust
ReplaceActive {
    theme: String,
    content: String,        // empty = forget (no new active entry)
    vector: Vec<f32>,       // ignored when content is empty
    #[serde(default)]
    metadata: Option<Value>,
    #[serde(default)]
    session_id: Option<String>,
}
```

Behaviour (single SQLite transaction):
1. Move current active entry for `theme` to `mementos` (set `archived_at = now`)
2. Delete from `entries`
3. If `content` is non-empty: insert new row into `entries`
4. Update in-memory vector index accordingly

Returns `{ "ok": true, "archived": true|false, "forgotten": true|false }`.

### 3. Contextor — `PeekMemento` command

```rust
PeekMemento {
    theme: String,
    #[serde(default = "default_memento_limit")]
    limit: usize,           // default 5
    #[serde(default)]
    session_id: Option<String>,
}
```

Returns last N mementos for the theme, ordered `archived_at DESC`:

```json
{
  "ok": true,
  "theme": "github_username",
  "mementos": [
    { "content": "yakupatahanov", "stored_at": "...", "archived_at": "..." },
    { "content": "yakup",        "stored_at": "...", "archived_at": "..." }
  ]
}
```

### 4. Contextor — `Search` scoped to active entries

The existing `Search` command queries the in-memory vector index, which is
loaded from `entries`. Since `ReplaceActive` removes old entries from `entries`
before archiving them to `mementos`, the index never contains mementos.
No change to `Search` needed — the separation is structural.

### 5. JARVIS — `jarvis/core/contextor.py` (or equivalent adapter)

Add:
- `update_memory(theme, content, session_id)` — embeds content (if non-empty),
  calls `ReplaceActive`
- `peek_memento(theme, limit, session_id)` — calls `PeekMemento`, returns
  formatted entries

### 6. JARVIS — `jarvis/core/command_parser.py`

Add `update_memory` and `peek_memento` to `VALID_ACTIONS` with parsers.

### 7. JARVIS — `jarvis/runtime/root_actions.py`

Add handlers for both actions.

### 8. JARVIS — `jarvis/config.py` (root prompt)

Add to the memory action block:

```
update_memory — Correct or forget a memory. Old entry is archived as a memento.
  Pass empty string for content to forget the theme entirely.
  {{"action": "update_memory", "theme": "<theme>", "content": "<new content or empty>"}}

peek_memento — Look up the history of a theme (previous entries before current).
  Use when you need to understand how something changed over time.
  {{"action": "peek_memento", "theme": "<theme>", "limit": 5}}
```

## Example flows

### Correcting a stale location

```
User: I moved my projects to ~/Dev
LLM:  {"action": "update_memory", "theme": "projects_location",
        "content": "Projects are at /home/yakup/Dev"}
```

Old entry ("Projects are at /home/yakup/Projects") moves to mementos.
New active entry stored with fresh vector. RAG now returns the correct path.

### Forgetting a topic

```
User: Forget everything you know about the Game project
LLM:  {"action": "update_memory", "theme": "project:Game", "content": ""}
```

Active entry archived as memento. No new active entry. `search_memory` returns
nothing for this theme going forward.

### Peeking at history

```
User: What did you used to know about my GitHub username?
LLM:  {"action": "peek_memento", "theme": "github_username", "limit": 5}
```

Returns archived entries with timestamps. LLM can report the history without
any of it affecting active memory or future retrieval.

### Recovering from a mistake

```
User: Actually, keep the old project location — I moved it back
LLM:  {"action": "peek_memento", "theme": "projects_location", "limit": 1}
      → sees previous value was /home/yakup/Projects
      {"action": "update_memory", "theme": "projects_location",
        "content": "Projects are at /home/yakup/Projects"}
```

The memento acts as a natural undo — LLM reads history, restores the value.
