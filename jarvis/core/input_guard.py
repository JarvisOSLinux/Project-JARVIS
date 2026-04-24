"""Lightweight prompt-injection detection.

This is a first-pass heuristic filter, not a comprehensive sanitizer.
It logs a WARNING when input matches known injection patterns so that:

  1. Security researchers studying JARVIS can see injection attempts in
     the structured log without any extra instrumentation.
  2. A future policy layer can decide to block or flag the request.

The guard intentionally does NOT silently drop input — that would make
JARVIS unresponsive without telling the user why.  Logging + returning
the matched pattern name lets the caller decide on the policy.

Patterns covered
----------------
* ``instruction_override`` — “ignore previous instructions” variants
* ``jailbreak_preamble``   — DAN / developer-mode / unrestricted-mode preambles
* ``role_override``        — “you are now an unrestricted AI” constructs
* ``system_tag_injection`` — raw system-prompt tags (<system>, [INST], etc.)

See docs/SECURITY-ARCHITECTURE.md for the threat-model context.
"""

from __future__ import annotations

import re
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(
            r"\bignore\b.{0,40}\b(previous|all|above|prior)\b.{0,40}"
            r"\b(instruction|prompt|rule|context)s?\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "jailbreak_preamble",
        re.compile(
            r"\b(do anything now|dan mode|developer mode|jailbreak mode|"
            r"unrestricted mode|no restrictions?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_override",
        re.compile(
            r"\byou\s+are\s+now\b.{0,80}\b(unrestricted|no\s+limit|no\s+rule|"
            r"without\s+(rules?|restrictions?|limits?))\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "system_tag_injection",
        re.compile(
            r"(<\s*/?system\s*>|\[SYSTEM\]|\[INST\]|<<SYS>>|<\|system\|>)",
            re.IGNORECASE,
        ),
    ),
]


def scan(text: str) -> Optional[str]:
    """Scan *text* for prompt-injection patterns.

    Returns the matched pattern name if suspicious, ``None`` if clean.
    Always logs a WARNING on a match — never silently drops input.
    """
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            logger.warning(
                "SECURITY: possible prompt injection (pattern=%r) in input: %r",
                name,
                text[:160],
            )
            return name
    return None
