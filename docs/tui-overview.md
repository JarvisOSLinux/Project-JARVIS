# Terminal UI (TUI) Overview

JARVIS's TUI is built with **[Textual](https://textual.textualize.io/)** (Python). It owns the terminal, rendering an interactive layout with scrollable output, a sidebar, and keyboard-driven navigation.

**Install:** `pip install "project-jarvis[tui]"`
**Launch:** `jarvis tui` (bare `jarvis` starts voice+socket mode, not the TUI)

---

## Layout

```
┌──────────────────┬──────────────────────────────────────────────────────┐
│  Session sidebar │  Chat output (scrollable, Rich markup)               │
│  (session list)  │                                                      │
│                  │                                                      │
├──────────────────┴──────────────────────────────────────────────────────┤
│  [Input line]                                                            │
├──────────────────────────────────────────────────────────────────────────┤
│  Status bar: session · model · provider · ctx: tokens/window · key hints │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Key Bindings

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `F1` / `/help` | Open keybinding + slash command help overlay |
| `F2` / `/settings` / `/providers` | Open tabbed config modal (Settings + Providers tabs) |
| `Ctrl+L` | Focus chat log (scroll with arrows / PgUp/PgDn) |
| `Ctrl+I` | Focus message input |
| `Ctrl+Shift+C` | Clear on-screen transcript (does not clear contextor memory) |
| `Ctrl+Shift+E` / `/export` | Export transcript to `JARVIS_DATA_DIR/transcripts/` as Markdown |
| `Ctrl+N` | New session |
| `Ctrl+D` | Delete selected session (press twice to confirm) |
| `Ctrl+Q` | Quit |
| `Esc` | Close modal / return focus to input |

---

## Slash Commands

TUI-only:

| Command | Effect |
|---------|--------|
| `/help` or `/?` | Open help overlay |
| `/settings` | Open config modal → Settings tab |
| `/providers` | Open config modal → Providers tab |
| `/providers add` | Push provider add form |
| `/model` | Show/set active model |
| `/status` | Show daemon status |
| `/export` | Export transcript |
| `/clear` | Clear on-screen transcript |
| `/quit` | Quit |

Session commands (work in all modes — CLI/voice/socket/TUI, handled by
`jarvis/runtime/session_commands.py`): `/new`, `/sessions`, `/switch`,
`/rename <name>`, `/delete`. (Output mode is switched with the `jarvis text` /
`jarvis voice` CLI subcommands or the Output mode setting in F2 — there are no
`/voice`/`/text` slash commands.)

Full list in `jarvis/tui/slash_commands_doc.py`; handler in `jarvis/runtime/session_commands.py`.

---

## Modals

**`config_modal.py`** — Tabbed settings sheet:
- **Settings tab**: editable runtime settings (confirmation mode/timeout, RAG top-k & min score, max goals, history reset, wake chime path, output mode, log level) — Save persists them
- **Providers tab**: list, add, edit, remove LLM providers; changes persist to `~/.config/jarvis/providers.json`

**`provider_modal.py`** — Provider add/edit form; pushed from config_modal Providers tab.

**`server_config_modal.py`** — MCP server configuration; pushed when a server needs API keys / config.

**`confirm_modal.py`** — Tool-level action confirmation dialog: shows the full command each tool will run (not just the tool name, #186); Allow/Deny via Y/N or buttons, Esc = Deny. Integrates with `ConfirmationManager` via the TUI callback.

---

## Implementation Notes

- Voice is disabled in TUI mode (screen is the primary interface)
- User input goes through `EventMerger` same as CLI/socket — behavior is identical
- `lifecycle.py` registers TUI-specific callbacks (output rendering, confirmation modal, slash command intercept) during startup
- The status bar (`status_bar.py`) reads `last_prompt_tokens` from the active provider for the `ctx:` display (`/context` additionally shows completion tokens); `LLM_CONTEXT_WINDOW` sets the denominator (0 = show raw count only)

---

## Changelog — corrected claims

*2026-07-22:* install name corrected to `project-jarvis[tui]`; launch is `jarvis tui` only; sidebar drawn on the left, output uses Rich markup; status bar order corrected (no "mode" element; only prompt tokens read); Settings tab is an editor, not a viewer; `/voice`/`/text` removed (CLI subcommands, not slash commands); slash commands split into TUI-only vs all-mode session commands; Ctrl+N/Ctrl+D/Ctrl+Q bindings added; confirm modal shows full command lines (#186).
