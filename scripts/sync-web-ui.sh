#!/usr/bin/env bash
# Copy jarvis-app's UI into jarvis/server/web/, the page `jarvis web` serves.
#
#   scripts/sync-web-ui.sh [jarvis-app checkout]    (default: ../jarvis-app)
#
# The page is jarvis-app's src/ folder: one UI for the desktop app and the
# browser, with transport.js choosing Tauri or the gateway's WebSocket. Edit
# it in jarvis-app, commit there, then run this. It records the commit and
# each file's SHA-256 in jarvis/server/web_ui.json; tests/test_web_ui_pin.py
# fails when a file here no longer matches, so the copy cannot drift.
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

APP="$(cd "${1:-$ROOT/../jarvis-app}" && pwd)"
if [ ! -f "$APP/src/transport.js" ]; then
    echo "$APP is not a jarvis-app checkout (no src/transport.js)" >&2
    exit 1
fi
if [ -n "$(git -C "$APP" status --porcelain -- src)" ]; then
    echo "$APP/src has uncommitted changes; commit them in jarvis-app first" >&2
    exit 1
fi

WEB="$ROOT/jarvis/server/web"
find "$WEB" -maxdepth 1 -type f -delete
find "$APP/src" -maxdepth 1 -type f -exec cp {} "$WEB/" \;

"$PYTHON" - "$WEB" "$(git -C "$APP" rev-parse HEAD)" <<'EOF'
import hashlib
import json
import sys
from pathlib import Path

web, commit = Path(sys.argv[1]), sys.argv[2]
pin = {
    "repo": "JarvisOSLinux/jarvis-app",
    "path": "src",
    "commit": commit,
    "files": {
        f.name: hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(web.iterdir())
        if f.is_file()
    },
}
(web.parent / "web_ui.json").write_text(json.dumps(pin, indent=2) + "\n")
print(f"jarvis/server/web/ is jarvis-app {commit[:12]} ({len(pin['files'])} files)")
EOF
