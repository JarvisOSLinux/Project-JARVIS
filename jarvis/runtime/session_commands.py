"""Session management slash-commands (/new, /sessions, /switch, /rename, /delete, /context)."""

from __future__ import annotations

from typing import Any

from ..config import Config


def session_reply(app: Any, message: str) -> None:
    """Emit a local reply for slash-commands (no LLM roundtrip)."""
    app.output_manager.handle_response({"output": message})


def handle_slash_command(app: Any, text: str) -> bool:
    """Handle /new, /sessions, /switch, /rename, /delete.

    Returns True if the input was a slash-command (handled), else
    False so it falls through to normal LLM routing.
    """
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    # Strip surrounding quote pairs so `/rename 'Hello World'` works naturally.
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in ('"', "'"):
        arg = arg[1:-1]

    if cmd == "/new":
        if not app.sessions.available:
            session_reply(app, "Memory is disabled — sessions unavailable.")
            return True
        session = app.sessions.new_session(title=arg or None)
        if session:
            session_reply(
                app,
                f"Started new session {session.short_id()}"
                + (f" ('{session.title}')" if session.title else ""),
            )
        else:
            session_reply(app, "Could not create a new session.")
        return True

    if cmd == "/sessions":
        if not app.sessions.available:
            session_reply(app, "Memory is disabled — sessions unavailable.")
            return True
        sessions = app.sessions.list(limit=50)
        if not sessions:
            session_reply(app, "No sessions yet.")
            return True
        current_id = app.sessions.current_id
        lines = ["Sessions (most recent first):"]
        for s in sessions:
            marker = "* " if s.id == current_id else "  "
            lines.append(f"{marker}{s.short_id()}  {s.display_label()}")
        session_reply(app, "\n".join(lines))
        return True

    if cmd == "/switch":
        if not arg:
            session_reply(app, "Usage: /switch <session_id_prefix>")
            return True
        session = app.sessions.switch(arg)
        if session:
            session_reply(
                app,
                f"Switched to {session.short_id()} ('{session.title}')",
            )
        else:
            session_reply(app, f"No session matches '{arg}'.")
        return True

    if cmd == "/rename":
        if not arg:
            session_reply(app, "Usage: /rename <new title>")
            return True
        if not app.sessions.current:
            session_reply(app, "No active session to rename.")
            return True
        if app.sessions.rename(arg):
            session_reply(app, f"Renamed to '{arg}'.")
        else:
            session_reply(app, "Rename failed.")
        return True

    if cmd == "/delete":
        if not arg:
            session_reply(app, "Usage: /delete <session_id_prefix>")
            return True
        sessions = app.sessions.list(limit=500)
        matches = [s for s in sessions if s.id.startswith(arg)]
        if len(matches) != 1:
            session_reply(
                app,
                f"Need a unique id prefix; got {len(matches)} match(es).",
            )
            return True
        if app.sessions.delete(matches[0].id):
            session_reply(app, f"Deleted session {matches[0].short_id()}.")
        else:
            session_reply(app, "Delete failed.")
        return True

    if cmd == "/context":
        lines = ["Context usage (last LLM call):"]
        jarvis = getattr(app, "jarvis", app)
        raw_provider = None
        if hasattr(jarvis, "llm"):
            raw_provider = getattr(jarvis.llm, "provider", None)
        prompt_toks = getattr(raw_provider, "last_prompt_tokens", 0) or 0
        completion_toks = getattr(raw_provider, "last_completion_tokens", 0) or 0
        if prompt_toks == 0 and completion_toks == 0:
            lines.append("  No LLM calls made yet in this session.")
        else:
            window = getattr(Config, "LLM_CONTEXT_WINDOW", 0)
            lines.append(f"  Prompt tokens:     {prompt_toks}")
            lines.append(f"  Completion tokens: {completion_toks}")
            lines.append(f"  Total:             {prompt_toks + completion_toks}")
            if window > 0:
                pct = int(prompt_toks / window * 100)
                lines.append(f"  Context window:    {window} ({pct}% used)")
        buf = getattr(jarvis, "mcp_buffer", {})
        if buf:
            lines.append(f"  MCP buffer:        {len(buf)} server(s) cached")
            for sid, entry in buf.items():
                lines.append(f"    {sid}  (used {entry['count']}x)")
        session_reply(app, "\n".join(lines))
        return True

    # Unknown slash-command — let it through to the LLM so the user
    # can still type "/foo" as literal input if they insist.
    return False
