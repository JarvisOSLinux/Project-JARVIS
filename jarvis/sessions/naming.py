"""A chat's name, taken from the first thing said in it.

A session starts as "New chat" and JARVIS names it after the first message, so
every screen's chat list (TUI, jarvis-app, the phone page) reads what each chat
was about. The lead-in people say to a voice assistant ("Hey Jarvis, can you")
is dropped: "Hey Jarvis, can you run the system update for me?" names its chat
"Run the system update for me".
"""

from __future__ import annotations

import re

_DEFAULT_TITLE = re.compile(r"^(?:New chat|Chat \d{4}-\d{2}-\d{2})", re.IGNORECASE)

# Greetings, the wake word and a polite opener, each optional, in that order.
_LEAD_IN = re.compile(
    r"^(?:(?:hey|hi|hello|ok(?:ay)?|yo|so|um+|uh+)\b[\s,!.]*)*"
    r"(?:jarvis\b[\s,!.:]*)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"|please\s+"
    r"|i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to\s+)?",
    re.IGNORECASE,
)

MAX_WORDS = 6
MAX_CHARS = 40


def is_default_title(title: str | None) -> bool:
    """Whether a session still has the name it started with."""
    return not (title or "").strip() or bool(_DEFAULT_TITLE.match(title.strip()))


def title_from(text: str) -> str:
    """A short title for a chat that begins with `text`, or "" when it says
    nothing to name it by (a greeting alone, or a slash command)."""
    text = " ".join((text or "").split())
    if not text or text.startswith("/"):
        return ""
    rest = _LEAD_IN.sub("", text, count=1)
    words = rest.split()[:MAX_WORDS]
    title = " ".join(words).rstrip("?.!,;:")
    if not title:
        return ""
    if len(title) > MAX_CHARS:
        cut = title[: MAX_CHARS - 1]
        title = (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"
    return title[0].upper() + title[1:]
