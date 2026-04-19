# Terminal UI (TUI) overview

This document explains what a **TUI** is in general, how JARVIS uses one today, and where we keep **work-in-progress** product notes. The architecture and roadmap sections will be tightened after the next design pass.

---

## What is a TUI?

A **TUI** (text user interface, often called a **terminal UI**) is an interactive program that runs **inside a terminal emulator** and draws its interface with **characters, colors, and simple box-drawing** instead of with a separate GUI window (buttons and widgets drawn by the OS windowing system).

Compared to plain **stdin/stdout chat** (`jarvis chat`), a TUI typically offers:

- **Layout**: multiple panes (e.g. session list + transcript + input).
- **Keyboard-first navigation**: focus moves between regions; shortcuts are documented in a footer or help screen.
- **Richer rendering**: scrollable regions, styled text, optional mouse support.

JARVIS’s TUI is built with **[Textual](https://textual.textualize.io/)** (Python). It is an **optional** install: `pip install "jarvis-ai[tui]"` or, from a clone, `pip install -e ".[tui]"`.

---

## JARVIS TUI today

- **Command**: `jarvis tui` (or `python -m jarvis.main tui` / `python -m jarvis.cli tui` with the same environment).
- **Role**: the TUI **owns the terminal** (no concurrent stdin chat on the same tty). User lines are injected through the same event path as other inputs (`EventMerger`), so behavior stays aligned with the rest of the runtime.
- **Voice**: disabled in TUI mode by design; the screen is the primary interface.

For implementation entry points, see `jarvis/tui/app.py` and `Jarvis(tui_mode=True)` in `jarvis/main.py`.

---

## WIP: design notes and open questions

*Status: brainstorming. Update this section once architecture choices are confirmed.*

### “Clear visible log” vs “show background work”

These are **different** ideas:

- **Clear visible log**: empty the **on-screen transcript widget** (e.g. the chat `RichLog`) for readability. It does **not** by itself erase stored conversation or contextor memory unless we explicitly add a destructive “reset session” action tied to it.
- **Background / dispatch visibility**: a **separate** concern—showing goals, tasks, tool runs, registry search, etc. That belongs in a **dispatch / activity** surface (pane, modal, or log stream), not in the phrase “clear log.”

### Export / “Share transcript”

Export means writing the **current session’s visible or full history** to a file (e.g. Markdown or plain text) so the user can attach it to a ticket, paste into another tool, or share with someone—similar in spirit to “Share” in web chat products. Format and whether to redact secrets are product decisions for the final spec.

### Operator “extras” vs system prompts

Core **system prompts** (ROOT/Dispatch behavior, JSON contracts, safety) should stay **versioned in code** or tightly controlled—not arbitrary user read/write from the TUI, for the reasons you raised.

A separate **`extras` / `JARVIS_OPERATOR_NOTES`-style** channel (env or small include file) can hold **optional tone** (“soul”), locale preferences, or other non-contract text that is **merged at the edge** of prompt assembly, with size limits and clear precedence (extras augment; they do not replace safety rules). That keeps “fun customization” away from breaking the orchestration layer.

### Model picker: local vs API vs custom

Reasonable layers:

1. **Local (e.g. Ollama)**: list tags from the local daemon (`ollama list` / API). Straightforward when `LLM_PROVIDER` is Ollama and the URL is reachable.
2. **First-class API providers**: curated list in docs or UI when we officially support them.
3. **Custom / “unsupported” OpenAI-compatible APIs**: usually enough to collect **base URL**, **model id**, **API key**, and optionally **extra headers** (some gateways need them). Validation is “does a minimal chat completion work?” rather than maintaining a giant allowlist.

`jarvis/config.py` already maps many knobs from **environment variables** (see `LLM_*` and related). The pattern is: **`.env` (or `jarvis.conf`) for values users change**; `Config` holds defaults and typed reads—not every constant needs to move to `.env`, only what operators should tune without editing code.

### Reconnect / health

Can be as simple as a **wake-up ping**: one cheap chat/completion or HTTP health check to the configured **LLM base URL** so the user knows the model daemon is alive again after sleep or network blip. Optionally extend to **subprocesses** (contextor/dispatch) if you expose a status RPC later. Still optional for v1 if “restart `jarvis tui`” is acceptable.

### Dispatch: goals, tasks, MCP search, installed tools

- **In-terminal “what is running”**: surfacing **active goals**, **archived goals**, and **tasks** (and tool noise) helps users trust long runs.

- **What is already on disk (Python side)**: completed/failed goals are appended to **`goal_archive.jsonl` under `JARVIS_DATA_DIR`** (same data root as Unix sockets by default). That is a sensible place to **co-locate** other operator logs (e.g. a future `dispatch_activity.jsonl`) without merging them into contextor’s DB—**same folder, different files**.

- **Dispatch binary (Rust)**: keeps an **in-memory rolling signal window** for the LLM; detailed tracing goes to **stderr** via `RUST_LOG` (see `dispatch/README.md`). There is no single “workflow JSON” required for the TUI; file-based workflow dumps would be a **deliberate add-on** if you want them next to `goal_archive.jsonl`.

- **JARVIS Python logs**: optional **`LOG_FILE`** in env (`Config.LOG_FILE`) — another knob for “everything under `JARVIS_DATA_DIR`” if you standardize paths in docs.

- **Live vs persisted**:
  - **Live state**: enough for “what is happening now.”
  - **Persisted audit trail** (optional): append-only log or structured store if we need replay or post-mortem—**not the same schema as contextor** (contextor is memory/RAG; dispatch is execution telemetry) unless we deliberately unify them.

- **MCP registry search in the TUI**: front-end for the same **keyword + embedding** discovery the backend already uses; manual search is a natural fit.

- **Installed tools list**: driven off **installed index** (`index.json` and related), listing only what is installed; **system / privileged** tools gated behind an explicit flag or permission so we do not surprise users.

### Search memory (cross-session)

UI to **query memories across sessions** (with clear scope: global vs session-scoped where applicable). After results are shown, the user can **reference** a hit in the next message or trigger a recall flow—exact UX to specify later.

### Focus cycle (Ctrl+L vs Ctrl+I)

Implemented in `jarvis/tui/app.py`: **Ctrl+L** focuses the **chat log** (scroll with arrows / PgUp), **Ctrl+I** focuses the **message input**. Shells often bind Ctrl+L to “clear line”; inside the TUI the app receives the key first, so behavior is predictable while JARVIS has focus. **F1** / **`/help`** remain planned for a full key table.

### Session search / filter

Treat as **sidebar filter** (fuzzy or prefix) when the session list grows—collapses into the same “sessions” area rather than a separate feature name.

### “Current” session

**Current** means the **session id** that receives the next user message and whose **conversation + scoped memory** the engine uses. The sidebar marker exists so users know **which thread they are extending** before they send.

### Help: F1, `/help`, footer

- **F1** or **`/help`**: show keybindings and slash commands (discoverability).
- **Footer**: short reminders; full table lives in help.

### Command palette (Ctrl+P)

Textual can expose a **command palette**: a searchable list of **actions** (new session, export transcript, model picker, search memory, etc.). **Use case**: discoverability and power-user speed without memorizing every shortcut. If we show “palette” in the footer, it should be **wired** to real commands or the hint removed.

### Theme and timestamps

- **Theme**: light/dark or follow terminal—nice for accessibility.
- **Timestamps**: optional, compact format (e.g. time-only on the right) to avoid visual bloat.

### Copy last message

Deferred for now (redundant with terminal selection for many users).

---

## Changelog

| Date       | Note                                                                 |
| ---------- | -------------------------------------------------------------------- |
| 2026-04-18 | Initial doc: TUI definition + WIP notes.                           |
| 2026-04-18 | WIP: operator extras vs prompts; reconnect; log locations; Ctrl+L/I. |
