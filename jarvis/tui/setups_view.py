"""The TUI's list of pending server setups (#254): F3 or /setups.

The same list `jarvis setups` prints, oldest first so the numbers match.
Picking one opens the setup form; C cancels it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

# ("fill" | "cancel", setup id), or None when closed.
Choice = Optional[Tuple[str, str]]


def ordered(setups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Oldest first, as `jarvis setups` numbers them."""
    return sorted(setups, key=lambda s: s.get("created_at") or 0)


def describe(number: int, setup: Dict[str, Any]) -> str:
    if setup.get("kind") == "configure":
        keys = [f["key"] for f in setup.get("fields") or []]
        needs = f"change: {', '.join(keys)} (asked for by JARVIS)"
    else:
        needs = f"needs: {', '.join(setup.get('missing') or [])}"
    lines = [
        f"{number}. {setup.get('server_name') or setup.get('server_id')}",
        f"   {needs}",
    ]
    if setup.get("goal_description"):
        lines.append(f"   for: {setup['goal_description']}")
    return "\n".join(lines)


class SetupsScreen(ModalScreen[Choice]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("c", "cancel_setup", "Cancel setup"),
    ]

    DEFAULT_CSS = """
    SetupsScreen {
        align: center middle;
    }
    SetupsScreen > #setups-dialog {
        width: 80;
        max-width: 95%;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    SetupsScreen #setups-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    SetupsScreen OptionList {
        height: auto;
        max-height: 20;
    }
    SetupsScreen .setups-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, setups: List[Dict[str, Any]]) -> None:
        super().__init__()
        self._setups = ordered(setups)

    def compose(self) -> ComposeResult:
        with Vertical(id="setups-dialog"):
            yield Label("Servers waiting for setup", id="setups-title")
            if not self._setups:
                yield Static("No server is waiting for setup.")
                yield Static("Esc closes", classes="setups-hint")
                return
            yield OptionList(
                *(
                    Option(describe(n, s), id=s["setup_id"])
                    for n, s in enumerate(self._setups, 1)
                ),
                id="setups-list",
            )
            yield Static(
                "Enter fills in the settings · C cancels the setup · Esc closes",
                classes="setups-hint",
            )

    def on_mount(self) -> None:
        if self._setups:
            self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id:
            self.dismiss(("fill", event.option.id))

    def action_cancel_setup(self) -> None:
        if not self._setups:
            return
        option_list = self.query_one(OptionList)
        index = option_list.highlighted
        if index is not None:
            self.dismiss(("cancel", self._setups[index]["setup_id"]))

    def action_close(self) -> None:
        self.dismiss(None)
