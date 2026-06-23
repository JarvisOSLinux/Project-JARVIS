"""Cross-platform abstraction layer.

Auto-detects the host OS and exports a concrete ``Platform`` instance with
the right implementations for IPC, paths, notifications, and service control.
"""

from __future__ import annotations

import platform as _platform

_system = _platform.system()

if _system == "Darwin":
    from .macos import MacOSPlatform as _Cls
elif _system == "Windows":
    from .windows import WindowsPlatform as _Cls
else:
    from .linux import LinuxPlatform as _Cls

current: _Cls = _Cls()  # type: ignore[assignment]
