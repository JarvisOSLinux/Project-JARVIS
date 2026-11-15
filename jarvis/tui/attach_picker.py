"""Pick a file to attach (`/attach` with no path): images and PDFs, from ``~``."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Label, Static

from .input_helpers import is_attachable


class AttachableTree(DirectoryTree):
    """Folders and the files an attachment can be; hidden ones left out."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [
            p
            for p in paths
            if not p.name.startswith(".") and (self._safe_is_dir(p) or is_attachable(p))
        ]


class AttachPicker(ModalScreen[Optional[Path]]):
    """Enter on a file attaches it; Esc closes with nothing."""

    BINDINGS = [Binding("escape", "close", "Close")]

    DEFAULT_CSS = """
    AttachPicker {
        align: center middle;
    }
    AttachPicker > #attach-dialog {
        width: 80;
        max-width: 95%;
        height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    AttachPicker #attach-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    AttachPicker AttachableTree {
        height: 1fr;
    }
    AttachPicker .attach-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, start: Optional[Path] = None) -> None:
        super().__init__()
        self._start = start or Path.home()

    def compose(self) -> ComposeResult:
        with Vertical(id="attach-dialog"):
            yield Label("Attach an image or PDF", id="attach-title")
            yield AttachableTree(self._start, id="attach-tree")
            yield Static(
                "Enter attaches the file or opens the folder · Esc closes",
                classes="attach-hint",
            )

    def on_mount(self) -> None:
        self.query_one(AttachableTree).focus()

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        event.stop()
        self.dismiss(event.path)

    def action_close(self) -> None:
        self.dismiss(None)
