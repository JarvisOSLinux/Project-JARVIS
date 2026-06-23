# Terminal UI (TUI) Overview

JARVIS's TUI is built with **[Textual](https://textual.textualize.io/)** (Python). It owns the terminal, rendering an interactive layout with scrollable output, a sidebar, and keyboard-driven navigation.

**Install:** `pip install "jarvis-ai[tui]"`
**Launch:** `jarvis` (or `jarvis tui`)

---

## Layout

```
┌──────────────────────────────────────────────────────┬──────────────────┐
│  Chat output (scrollable, Markdown)                  │  Session sidebar │
│                                                      │  (session list)  │
│                                                      │                  │
├──────────────────────────────────────────────────────┴──────────────────┤
│  [Input line]                                                            │
├──────────────────────────────────────────────────────────────────────────┤
│  Status bar: model · ctx: tokens/window · mode · provider                │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Key Bindings

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `F1` / `/help` | Open keybinding + slash command help overlay |
| `F2` / `/settings` / `/providers` | Open tabbed config modal (Config + Providers tabs) |
| `Ctrl+L` | Focus chat log (scroll with arrows / PgUp/PgDn) |
| `Ctrl+I` | Focus message input |
| `Ctrl+Shift+C` | Clear on-screen transcript (does not clear contextor memory) |
| `Ctrl+Shift+E` / `/export` | Export transcript to `JARVIS_DATA_DIR/transcripts/` as Markdown |
| `Esc` | Close modal / return focus to input |

---

## Slash Commands (TUI only)

| Command | Effect |
|---------|--------|
| `/help` or `/?` | Open help overlay |
| `/settings` | Open config modal → Config tab |
| `/providers` | Open config modal → Providers tab |
| `/providers add` | Push provider add form |
| `/rename <name>` | Rename current session |
| `/sessions` | Show session list |
| `/voice` / `/text` | Switch output mode |
| `/export` | Export transcript |

Full list in `jarvis/tui/slash_commands_doc.py`; handler in `jarvis/runtime/session_commands.py`.

---

## Modals

**`config_modal.py`** — Tabbed settings sheet:
- **Config tab**: runtime config viewer (confirmation mode, context window, etc.)
- **Providers tab**: list, add, edit, remove LLM providers; changes persist to `~/.config/jarvis/providers.json`

**`provider_modal.py`** — Provider add/edit form; pushed from config_modal Providers tab.

**`server_config_modal.py`** — MCP server configuration; pushed when a server needs API keys / config.

**`confirm_modal.py`** — Tool-level action confirmation dialog (shows tool name, Y/N); integrates with `ConfirmationManager` via the TUI callback.

---

## Implementation Notes

- Voice is disabled in TUI mode (screen is the primary interface)
- User input goes through `EventMerger` same as CLI/socket — behavior is identical
- `lifecycle.py` registers TUI-specific callbacks (output rendering, confirmation modal, slash command intercept) during startup
- The status bar (`status_bar.py`) reads `last_prompt_tokens` + `last_completion_tokens` from the active provider for the `ctx:` display; `LLM_CONTEXT_WINDOW` sets the denominator (0 = show raw count only)
