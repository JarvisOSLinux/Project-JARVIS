"""Redact credentials before JARVIS logs, stores or sends text anywhere (#242).

A user asked for an API key answers by pasting it, and a message typed into
JARVIS is written in several places before the model even reads it: the log,
the goal tree (archived to disk), and contextor's memory, which is searched on
every later prompt. A secret that reaches any of them persists, and the memory
copy can be retrieved into an unrelated conversation and sent to the model
provider again. Deleting the session does not remove the archived goal.

So text is scrubbed at the two boundaries it crosses: the input funnel, before
anything is stored or sent, and the log handlers, for every line any module
writes -- including lines that echo input before the funnel sees it.

Only well-known credential formats are matched. There is deliberately no
entropy heuristic: commit SHAs, UUIDs, content hashes and base64 payloads are
all high-entropy, all legitimate in a conversation about code, and redacting
them would break the conversation in order to protect nothing.
"""

from __future__ import annotations

import logging
import re
from typing import List, Tuple

# Order matters where formats overlap: Anthropic keys also start with "sk-", so
# they are matched first and read back as the more specific label.
_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple(
    (label, re.compile(pattern))
    for label, pattern in (
        (
            "GitHub token",
            r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}",
        ),
        ("GitLab token", r"\bglpat-[A-Za-z0-9_-]{20,}"),
        ("Anthropic API key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
        # Legacy keys are unbroken alphanumerics; newer ones carry a known
        # prefix. Matching "sk-" plus any hyphenated run would redact slugs
        # like a branch named sk-feature-add-login-page.
        (
            "OpenAI API key",
            r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{40,}|\bsk-[A-Za-z0-9]{40,}",
        ),
        ("Stripe secret key", r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}"),
        ("Brave API key", r"\bBSA[A-Za-z0-9_-]{20,}"),
        ("Slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
        ("Google API key", r"\bAIza[0-9A-Za-z_-]{35}"),
        ("AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        ("Hugging Face token", r"\bhf_[A-Za-z0-9]{30,}"),
        ("Notion token", r"\b(?:secret|ntn)_[A-Za-z0-9]{40,}"),
        ("Telegram bot token", r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
        # Home Assistant long-lived tokens are JWTs, as are most bearer tokens.
        (
            "JSON web token",
            r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
        ),
        (
            "private key",
            r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z]+ )?PRIVATE KEY-----|\Z)",
        ),
    )
)


def redact_secrets(text: str) -> Tuple[str, List[str]]:
    """Return ``text`` with credentials replaced, and the labels of what was found.

    Labels are unique and in the order first found, for telling the user what
    was removed without repeating any part of it.
    """
    if not text:
        return text, []
    found: List[str] = []
    for label, pattern in _PATTERNS:
        text, count = pattern.subn(f"[redacted {label}]", text)
        if count and label not in found:
            found.append(label)
    return text, found


def redaction_notice(found: List[str]) -> str:
    """What to tell the user when their message contained a credential."""
    what = found[0] if len(found) == 1 else ", ".join(found[:-1]) + " and " + found[-1]
    article = "an" if what[0].lower() in "aeiou" else "a"
    return (
        f"I removed what looks like {article} {what} from your message before saving it "
        "or sending it anywhere. Secrets don't belong in the chat: when a server needs "
        "one, JARVIS asks for it in a separate form that never goes to the model."
    )


class SecretRedactingFilter(logging.Filter):
    """Scrub credentials from every record before a handler writes it.

    Attached to each JARVIS logger and to the shared file handler, so a line
    that echoes raw input -- the socket reader logs the first 80 characters of
    every message, which fits a whole GitHub token -- is cleaned wherever it is
    emitted, including lines added after this was written.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True  # a malformed record is not ours to drop
        clean, found = redact_secrets(message)
        if found:
            record.msg = clean
            record.args = ()
        return True


REDACTING_FILTER = SecretRedactingFilter()
