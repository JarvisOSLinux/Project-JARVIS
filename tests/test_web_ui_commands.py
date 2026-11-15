"""The page's slash commands are the TUI's (2026-11-15).

jarvis-app's src/commands.js (copied to jarvis/server/web/ by
scripts/sync-web-ui.sh) lists the same commands, in the same order, as
slash_command_catalog(), and completes them the way the TUI's
CommandCompleter does. A command added on one side only fails here; fix it in
the TUI catalog or in jarvis-app and sync again.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.tui.input_helpers import CommandCompleter
from jarvis.tui.slash_commands_doc import TUI_LOCAL_SLASH_HELP, slash_command_catalog

WEB = Path(__file__).resolve().parent.parent / "jarvis" / "server" / "web"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(
    NODE is None, reason="node is not installed, so commands.js cannot be loaded"
)

# Each list is typed into one completer, Tab after each line.
CASES = [
    ["/pro"],
    ["/e", "/export ", "/exit "],
    ["/PRO"],
    ["/new title", "hello", ""],
    ["/e", "/ex", "/export "],
]

_LOAD = """
const c = require(process.argv[1]);
const names = c.JARVIS_COMMANDS.map((x) => x.name);
const completed = JSON.parse(process.argv[2]).map((seq) => {
    const k = new c.CommandCompleter(names);
    return seq.map((text) => k.complete(text));
});
const app = c.JARVIS_COMMANDS.filter((x) => x.where === 'app').map((x) => x.name);
console.log(JSON.stringify({ names, app, completed }));
"""


@pytest.fixture(scope="module")
def page() -> dict:
    out = subprocess.run(
        [NODE, "-e", _LOAD, str(WEB / "commands.js"), json.dumps(CASES)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


@needs_node
@pytest.mark.unit
def test_the_page_lists_the_tuis_commands_in_order(page):
    assert page["names"] == [name for name, _ in slash_command_catalog()]


@needs_node
@pytest.mark.unit
def test_every_app_command_has_a_handler(page):
    main = (WEB / "main.js").read_text()
    block = re.search(r"^const COMMAND_HANDLERS = \{\n(.*?)^\};", main, re.S | re.M)
    assert block, "COMMAND_HANDLERS not found in main.js"
    handled = set(re.findall(r"^ {4}'(/[^']+)':", block.group(1), re.M))
    assert set(page["app"]) <= handled
    # The TUI-local commands are the app's: one marked 'daemon' would reach
    # the model as a prompt.
    local = {
        a.strip().split()[0] for c, _ in TUI_LOCAL_SLASH_HELP for a in c.split(",")
    }
    assert set(page["app"]) == local


@needs_node
@pytest.mark.unit
def test_the_page_completes_like_the_tui(page):
    names = [name for name, _ in slash_command_catalog()]
    expected = []
    for seq in CASES:
        completer = CommandCompleter(names)
        expected.append([completer.complete(text) for text in seq])
    assert page["completed"] == expected
    assert expected[:4] == [
        ["/providers "],
        ["/export ", "/exit ", "/export "],
        ["/providers "],
        [None, None, None],
    ]


@pytest.mark.unit
def test_commands_load_between_transport_and_main():
    html = (WEB / "index.html").read_text()
    spots = [html.index(f'src="{name}"') for name in ("transport.js", "commands.js")]
    assert spots[0] < spots[1] < html.index('src="main.js"')
