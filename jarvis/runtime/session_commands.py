"""Session management slash-commands (/new, /sessions, /switch, /rename, /delete, /context)."""

from __future__ import annotations

from typing import Any

from ..config import Config


def session_reply(app: Any, message: str) -> None:
    """Emit a local reply for slash-commands (no LLM roundtrip)."""
    app.output_manager.handle_response({"output": message})


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    return text


def _rename_target(app: Any, raw: str, whole: str) -> tuple:
    """(session, title) for `/rename`: `<id> <title>` names a session, else the current.

    The first word is taken as an id only when it starts exactly one session's
    id and a title follows, the rule `/delete` uses for its prefix.
    """
    first, _, rest = raw.partition(" ")
    if rest.strip():
        matches = [s for s in app.sessions.list(limit=500) if s.id.startswith(first)]
        if len(matches) == 1:
            return matches[0], _unquote(rest.strip())
    return app.sessions.current, whole


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
            session_reply(app, "Usage: /rename [<session id>] <new title>")
            return True
        target, title = _rename_target(app, parts[1].strip(), arg)
        if target is None:
            session_reply(
                app,
                "You're not in a session. Name one by its id (/rename <id> <title>), "
                "or switch to it first (/switch <id>).",
            )
            return True
        if app.sessions.rename(title, session_id=target.id):
            session_reply(app, f"Renamed {target.short_id()} to '{title}'.")
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
        session_reply(app, context_report(app))
        return True

    # Unknown slash-command — let it through to the LLM so the user
    # can still type "/foo" as literal input if they insist.
    return False


def context_report(app: Any) -> str:
    """What filled the last prompt, part by part (#272)."""
    from ..llm.context_breakdown import token_rows

    jarvis = getattr(app, "jarvis", app)
    llm = getattr(jarvis, "llm", None)
    provider = getattr(llm, "provider", None)
    last = getattr(llm, "last_breakdown", None)
    if not last:
        return "Context: no model call yet."
    prompt = getattr(provider, "last_prompt_tokens", 0) or 0
    completion = getattr(provider, "last_completion_tokens", 0) or 0
    rows = token_rows(last["chars"], prompt)
    used = prompt or sum(tokens for _, tokens in rows)
    window = getattr(Config, "LLM_CONTEXT_WINDOW", 0)
    of = window if window > 0 else used
    lines = [
        (
            f"Context: {used:,} of {window:,} tokens ({used * 100 // window}%) in the last prompt"
            if window > 0
            else f"Context: {used:,} tokens in the last prompt"
        )
    ]
    width = max([len(part) for part, _ in rows] + [len("Free space")])
    for part, tokens in rows:
        share = tokens * 100 / of if of else 0
        bar = "█" * max(1, round(share / 5)) if tokens else ""
        note = ""
        if part == "Recent conversation":
            kept = last.get("exchanges_kept", 0)
            note = f"  {kept} exchange{'s' if kept != 1 else ''} in full"
        lines.append(f"  {part:<{width}}  {tokens:>7,}  {share:5.1f}%  {bar}{note}")
    if window > 0:
        free = max(0, window - used)
        lines.append(
            f"  {'Free space':<{width}}  {free:>7,}  {free * 100 / window:5.1f}%"
        )
    if completion:
        lines.append(f"Reply: {completion:,} tokens.")
    root_window = getattr(llm, "_root_window", None)
    if root_window:
        lines.append(
            f"History: the last {root_window} exchanges stay in full; older ones are "
            "folded into the conversation summary."
        )
    lines.append(
        "Sizes per part are scaled from their length to the provider's count."
        if prompt
        else "Sizes are estimated from length (about 4 characters a token)."
    )
    if window <= 0:
        lines.append(
            "Set LLM_CONTEXT_WINDOW to see the share of the window and the free space."
        )
    docs = getattr(jarvis, "mcp_dispatch_docs", {}) or {}
    if docs:
        lines.append(f"Tool docs loaded for: {', '.join(docs)}")
    return "\n".join(lines)
