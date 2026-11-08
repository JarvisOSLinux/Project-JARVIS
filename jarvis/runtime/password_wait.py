"""Spot a root call parked on polkit's password prompt (#310).

dmcp writes a stderr line before it raises the prompt and another once the
password is accepted; dispatch streams a task's stderr into its live tail. A
tail whose last such line is the waiting one means nobody has answered yet.
The wording is dmcp's (`AUTH_WAITING_NOTICE` / `AUTH_GRANTED_NOTICE` in
dmcp/src/elevation.rs).
"""

from __future__ import annotations

WAITING = "waiting for the administrator password"
GRANTED = "administrator password accepted"

WAITING_NOTE = (
    "The command is parked on the administrator password prompt on the user's "
    "computer; nobody has answered it yet. It is not stuck and not asking for "
    "input: tell the user to approve the password prompt on their computer. "
    "Do not dispatch it again and do not send input."
)


def awaiting_password(tail: object) -> bool:
    """True when the tail's latest password line is the waiting one."""
    if not isinstance(tail, str):
        return False
    return tail.rfind(WAITING) > tail.rfind(GRANTED)
