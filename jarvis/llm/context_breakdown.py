"""What fills a ROOT prompt, part by part, for `/context` (#272).

A ROOT call is three things (see context_manager.py): the system prompt (the
instructions, plus a rolling summary of exchanges that left the window), the
last few exchanges in full, and this turn's message. This turn's message is a
stack of labelled blocks built by root_context.build_root_context and the
action handlers ("GOALS: …", "SERVER_DOCS: …", "NEW INPUT: …").

Only known labels start a block, so a tool's own output that happens to have
an upper-case line ("WARNING: …") stays inside the block it arrived in.
Sizes are characters; `/context` scales them to the provider's token count.
"""

from __future__ import annotations

import re
from typing import Dict, List

SUMMARY_MARKER = "\n--- CONVERSATION CONTEXT (summary of earlier exchanges) ---"

# Display order for /context.
PARTS = (
    "Instructions",
    "Conversation summary",
    "Recent conversation",
    "Tool docs",
    "Search results",
    "Memories",
    "Goals",
    "Task results",
    "Your message",
    "Other",
)

_PART_OF_LABEL = {
    "SERVER_DOCS": "Tool docs",
    "ACTIVE_SERVER_DOCS": "Tool docs",
    "TOOLS_AFTER_SIGN_IN": "Tool docs",
    "SKILL": "Tool docs",
    "SEARCH_RESULTS": "Search results",
    "RELEVANT MEMORIES": "Memories",
    "GOALS": "Goals",
    "GOAL_STATE": "Goals",
    "INTENT": "Goals",
    "SIGNAL": "Task results",
    "SIGNALS": "Task results",
    "DISPATCH_RESULT": "Task results",
    "DISPATCH_ERROR": "Task results",
    "REMIND_COMPLETED": "Task results",
    "EXIT_DATA": "Task results",
    "HELD_OUTPUT": "Task results",
    "JOB_OUTPUT": "Task results",
    "CONVERSATION_SUMMARY": "Conversation summary",
    "NEW INPUT": "Your message",
}
# Other blocks JARVIS writes. Listed so each starts a block of its own instead
# of swelling the block before it.
for _label in (
    "SESSION_TITLE",
    "STANDING CONSTRAINTS",
    "INSTALL_RESULT",
    "INSTALL_ERROR",
    "UNINSTALL_RESULT",
    "UNINSTALL_ERROR",
    "UPDATE_RESULT",
    "UPDATE_ERROR",
    "SETUP_PENDING",
    "SETUP_COMPLETED",
    "SETUP_CANCELLED",
    "CONFIGURE_PENDING",
    "CONFIGURE_RESULT",
    "CONFIGURE_CANCELLED",
    "CONFIGURE_ERROR",
    "CONFIG_HINT",
    "USER_DENIAL",
    "NOTE",
):
    _PART_OF_LABEL[_label] = "Other"

# A label at the start of a line, before " (…)", " —" or ":".
_LABEL = re.compile(r"^([A-Z][A-Z_]*(?: [A-Z][A-Z_]*)?)(?= \(| —|:)", re.MULTILINE)


def message_parts(text: str) -> Dict[str, int]:
    """Characters per part in one turn's message."""
    sizes = {p: 0 for p in PARTS}
    cuts = [
        (m.start(), _PART_OF_LABEL[m.group(1)])
        for m in _LABEL.finditer(text)
        if m.group(1) in _PART_OF_LABEL
    ]
    start, part = 0, "Other"
    for at, next_part in cuts:
        sizes[part] += len(text[start:at])
        start, part = at, next_part
    sizes[part] += len(text[start:])
    return sizes


def breakdown(messages: List[Dict[str, str]]) -> Dict[str, object]:
    """Characters per part of a ROOT messages list, plus how much history was kept."""
    sizes = {p: 0 for p in PARTS}
    if not messages:
        return {"chars": sizes, "exchanges_kept": 0}
    system = (
        messages[0].get("content", "") if messages[0].get("role") == "system" else ""
    )
    body = messages[1:] if system else messages
    head, marker, summary = system.partition(SUMMARY_MARKER)
    sizes["Instructions"] += len(head)
    sizes["Conversation summary"] += len(marker) + len(summary)
    *earlier, latest = body if body else [{}]
    for message in earlier:
        sizes["Recent conversation"] += len(message.get("content", ""))
    for part, size in message_parts(latest.get("content", "")).items():
        sizes[part] += size
    kept = sum(1 for m in earlier if m.get("role") == "assistant")
    return {"chars": sizes, "exchanges_kept": kept}


def token_rows(chars: Dict[str, int], prompt_tokens: int) -> List[tuple]:
    """(part, tokens) per part with any content, scaled so they sum to prompt_tokens.

    Without a reported count, about four characters make a token.
    """
    total_chars = sum(chars.values())
    if total_chars == 0:
        return []
    scale = prompt_tokens / total_chars if prompt_tokens else 0.25
    return [(p, round(chars[p] * scale)) for p in PARTS if chars.get(p)]
