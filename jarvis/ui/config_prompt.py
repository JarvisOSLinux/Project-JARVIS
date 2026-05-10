"""Collect configurableProperties values from the user via a Tkinter dialog.

Shown automatically after a server is installed if the manifest declares
configurableProperties. Runs the blocking Tk dialog in a thread pool so
the asyncio event loop is not blocked.

Falls back gracefully if Tkinter is unavailable (headless / no display).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


def _show_dialog(props: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Synchronous Tkinter form. Returns filled values or None if cancelled."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return None

    result: Dict[str, Optional[Dict[str, str]]] = {"values": None}

    root = tk.Tk()
    root.title("Server Configuration Required")
    root.resizable(False, False)
    root.lift()
    root.attributes("-topmost", True)

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")
    frame.columnconfigure(1, weight=1)

    ttk.Label(
        frame,
        text="Fill in the required configuration values:",
        font=("TkDefaultFont", 11, "bold"),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

    entries: Dict[str, tk.StringVar] = {}
    row = 1
    for prop in props:
        key = prop.get("key", "")
        label_text = prop.get("label", key)
        description = prop.get("description", "")
        sensitive = bool(prop.get("sensitive", False))
        required = bool(prop.get("required", False))

        if required:
            label_text += " *"

        ttk.Label(frame, text=label_text).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=(4, 0)
        )
        var = tk.StringVar()
        entries[key] = var
        ttk.Entry(frame, textvariable=var, width=45, show="•" if sensitive else "").grid(
            row=row, column=1, sticky="ew", pady=(4, 0)
        )
        row += 1

        if description:
            ttk.Label(frame, text=description, foreground="gray").grid(
                row=row, column=1, sticky="w", pady=(0, 4)
            )
            row += 1

    ttk.Label(frame, text="* required", foreground="gray").grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )
    row += 1

    def on_ok() -> None:
        result["values"] = {k: v.get() for k, v in entries.items()}
        root.destroy()

    def on_skip() -> None:
        root.destroy()

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=row, column=0, columnspan=2, pady=(12, 0), sticky="e")
    ttk.Button(btn_frame, text="Save", command=on_ok).pack(side="left", padx=(0, 4))
    ttk.Button(btn_frame, text="Skip", command=on_skip).pack(side="left")

    root.mainloop()
    return result["values"]


async def prompt_configurable_properties(
    props: List[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    """Show the config dialog and return collected values, or None if skipped.

    Runs the blocking Tkinter dialog in the default executor so the asyncio
    event loop remains responsive.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _show_dialog, props)
