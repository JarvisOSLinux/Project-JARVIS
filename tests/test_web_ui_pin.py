"""The page `jarvis web` serves is jarvis-app's UI, pinned (2026-10-28).

jarvis/server/web/ was a hand-ported copy of jarvis-app's src/, and the two
drifted: the phone gained Stop, Stop all and per-task stop, the app never did.
It is now one UI (transport.js picks Tauri or the gateway's WebSocket), kept in
jarvis-app and copied here by scripts/sync-web-ui.sh, which records the commit
and each file's hash in jarvis/server/web_ui.json. A change made here instead
of in jarvis-app fails this test.
"""

import hashlib
import json
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent / "jarvis" / "server"
WEB = SERVER / "web"
FIX = (
    "jarvis/server/web/ is jarvis-app's src/; make the change in jarvis-app and "
    "run scripts/sync-web-ui.sh"
)


@pytest.mark.unit
def test_the_page_is_the_pinned_jarvis_app_ui():
    pin = json.loads((SERVER / "web_ui.json").read_text())
    assert pin["repo"] == "JarvisOSLinux/jarvis-app" and len(pin["commit"]) == 40
    here = {
        f.name: hashlib.sha256(f.read_bytes()).hexdigest()
        for f in WEB.iterdir()
        if f.is_file()
    }
    assert sorted(here) == sorted(pin["files"]), FIX
    changed = [name for name, digest in here.items() if pin["files"][name] != digest]
    assert not changed, f"{', '.join(changed)} changed. {FIX}"


@pytest.mark.unit
def test_the_page_runs_on_both_transports():
    page = (WEB / "index.html").read_text()
    assert page.index("transport.js") < page.index("main.js")
    transport = (WEB / "transport.js").read_text()
    assert "kind: 'tauri'" in transport and "kind: 'web'" in transport
