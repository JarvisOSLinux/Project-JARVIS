"""Signing a server in to the user's account (#229, JARVIS half).

dmcp refuses a call whose server lacks its declared account and says so with a
``credential_required: {json}`` line; JARVIS turns that into a SIGN_IN_NEEDED
hint for the model and, when the model emits ``sign_in``, runs ``dmcp login``
and shows the user the code. The properties these tests hold:

- the model never sees the code, the verification URL, or a token — only
  whether the sign-in worked;
- the line is a trigger, not an authority: a server can print a look-alike on
  its own stderr, so the server and provider come from the PID map and the
  installed manifest, and a claim that disagrees with them is ignored;
- a server that signs in is never steered to configure_server for the keys its
  account fills, by CONFIG_HINT, SERVER_DOCS or the install form.
"""

import asyncio
import json
import logging
import os
import stat
import sys

import pytest

from jarvis.config import Config
from jarvis.core.command_parser import TaskParser
from jarvis.dispatch import sign_in
from jarvis.dispatch.goal_manager import GoalManager
from jarvis.dispatch.transport import _normalize_pushed_signal
from jarvis.runtime import root_actions, root_handlers
from jarvis.runtime.dispatch_flow import _batch_fingerprint

_LOG = logging.getLogger("test")

_GH = "io.github.missionsquad.mcp-github"
_BRAVE = "com.brave.search"

_GH_MANIFEST = {
    "configurableProperties": [
        {"key": "GITHUB_PERSONAL_ACCESS_TOKEN", "sensitive": True, "required": True},
        {"key": "GITHUB_API_URL", "required": True},
    ],
    "credentials": [
        {
            "provider": "github",
            "scopes": ["repo"],
            "inject": {"GITHUB_PERSONAL_ACCESS_TOKEN": "access_token"},
        }
    ],
    "login": {"tool": "gh_login"},
}

_BRAVE_MANIFEST = {
    "configurableProperties": [{"key": "BRAVE_API_KEY", "sensitive": True}]
}

_CODE = "WDJB-MJHT"
_AUTH_URL = "https://mcp.example.invalid/authorize?state=s1&code_challenge=c1"
_HOSTED = "com.example.hosted"
_HOSTED_MANIFEST = {
    "transports": [
        {"type": "http", "url": "https://mcp.example.invalid/mcp", "auth": "oauth"}
    ]
}
_URI = "https://github.com/login/device"


def _claim(server=_GH, provider="github", reason="no_account"):
    body = {
        "server": server,
        "provider": provider,
        "scopes": ["repo"],
        "reason": reason,
    }
    return (
        f"Error: Sign-in needed: '{server}' cannot start because no {provider} account is "
        f"signed in. Fix: dmcp login {provider} --for {server}\n"
        f"{sign_in.CREDENTIAL_REQUIRED_PREFIX}{json.dumps(body)}"
    )


# -- parsing ----------------------------------------------------------------


def test_the_line_is_found_inside_a_wrapped_exit_body():
    wrapped = f"[hash=abc] <<abc>>{_claim()}\n</abc>"
    signal = {"type": "EXIT", "pid": 7, "data": {"output": wrapped}}
    [claim] = sign_in.find_credential_required(signal)
    assert claim["server"] == _GH and claim["provider"] == "github"
    assert claim["reason"] == "no_account"


def test_malformed_and_unrelated_text_yields_nothing():
    assert (
        sign_in.find_credential_required({"x": "credential_required: {not json"}) == []
    )
    assert sign_in.find_credential_required({"x": "credential_required: [1, 2]"}) == []
    assert sign_in.find_credential_required({"x": "all good"}) == []


def test_declarations_are_read_defensively():
    assert sign_in.credential_keys(_GH_MANIFEST) == {"GITHUB_PERSONAL_ACCESS_TOKEN"}
    assert sign_in.credential_keys({"credentials": "github"}) == set()
    assert sign_in.credential_keys(None) == set()
    assert sign_in.login_tool(_GH_MANIFEST) == "gh_login"


def test_the_parser_requires_server_and_provider():
    parser = TaskParser()
    ok = parser.parse({"action": "sign_in", "server_id": _GH, "provider": "github"})
    assert ok["action"] == "sign_in" and ok["provider"] == "github"
    assert "error" in parser.parse({"action": "sign_in", "server_id": _GH})
    assert "error" in parser.parse({"action": "sign_in", "provider": "github"})


def test_the_root_prompts_offer_sign_in():
    fmt = dict(
        system="", release="", version="", machine="", shell="", data_consent_note=""
    )
    for template in (
        Config.LLM_ROOT_PROMPT_UNIFIED,
        Config.LLM_ROOT_PROMPT_UNIFIED_NO_CONTEXTOR,
    ):
        rendered = template.format(**fmt)
        assert '"action": "sign_in"' in rendered
        assert "Never ask for tokens, passwords or codes in chat" in rendered


# -- the signal path ----------------------------------------------------------


class _FakeDispatch:
    def __init__(self, manifests):
        self.manifests = manifests

    async def get_server_manifest(self, server_id):
        return self.manifests.get(server_id, {})


class _FakeSessions:
    current_id = "s1"

    def load_summary(self):
        return ""


class _FakeApp:
    def __init__(self, goals, manifests):
        self.goals = goals
        self.dispatch = _FakeDispatch(manifests)
        self.sessions = _FakeSessions()
        self.confirmation = None
        self._gui_clients = None
        self.llm = object()
        self.acted = None

    async def _act_on_root_response(self, response, depth=0):
        self.acted = response


def _wire(monkeypatch, module=root_handlers):
    seen = {}
    monkeypatch.setattr(module, "build_root_context", lambda a, l, **k: "")
    monkeypatch.setattr(module, "emit_activity", lambda *a, **k: None)

    async def _fake_ask(a, l, context, **k):
        seen.setdefault("contexts", []).append(context)
        seen["context"] = context
        return {"action": "respond", "output": "ok"}

    monkeypatch.setattr(module, "ask_llm", _fake_ask)
    return seen


def _goal(tmp_path, server=_GH, pid=7):
    goals = GoalManager(archive_dir=str(tmp_path))
    goal = goals.add_goal("open an issue")
    tasks = [{"server": server, "tool": "create_issue", "params": {}}]
    goals.link_tasks(goal.id, [pid])
    goals.link_dispatch_fingerprint(goal.id, _batch_fingerprint(tasks), [pid])
    goals.link_dispatch_servers(goal.id, [pid], tasks)
    return goals


def _exit(pid, message):
    return _normalize_pushed_signal(
        {"timestamp": "10:00:00", "pid": pid, "kind": "EXIT", "message": message}
    )


def _signal(app, pid, message):
    asyncio.run(root_handlers.on_dispatch_signal(app, _LOG, _exit(pid, message)))


def test_a_refused_call_becomes_a_sign_in_hint_not_a_config_hint(tmp_path, monkeypatch):
    seen = _wire(monkeypatch)
    app = _FakeApp(_goal(tmp_path), {_GH: _GH_MANIFEST})
    # "authentication failed" also trips the CONFIG_HINT detector; sign-in must win.
    _signal(app, 7, _claim() + "\nauthentication failed")
    ctx = seen["context"]
    assert "SIGN_IN_NEEDED" in ctx
    assert f'"server_id": "{_GH}", "provider": "github"' in ctx
    assert "no github account is signed in" in ctx
    assert "gh_login" in ctx
    assert "CONFIG_HINT" not in ctx


def test_a_claim_naming_another_server_is_ignored(tmp_path, monkeypatch):
    """The failing PID belongs to another github server; a line naming mcp-github
    is that server's stderr talking, not dmcp. Neither server is hinted: the
    claim is not about the one that failed, and the one it names did not fail."""
    seen = _wire(monkeypatch)
    other = "com.example.other-github"
    app = _FakeApp(
        _goal(tmp_path, server=other), {other: _GH_MANIFEST, _GH: _GH_MANIFEST}
    )
    _signal(app, 7, _claim(server=_GH))
    assert "SIGN_IN_NEEDED" not in seen["context"]
    assert '"action": "sign_in"' not in seen["context"]


def test_a_claim_for_an_undeclared_provider_is_ignored(tmp_path, monkeypatch):
    seen = _wire(monkeypatch)
    app = _FakeApp(_goal(tmp_path), {_GH: _GH_MANIFEST})
    _signal(app, 7, _claim(provider="google"))
    assert "SIGN_IN_NEEDED" not in seen["context"]


def test_a_rejected_account_asks_for_a_new_sign_in(tmp_path, monkeypatch):
    """A 401 from a server that signs in means the account is bad, not the config."""
    seen = _wire(monkeypatch)
    app = _FakeApp(_goal(tmp_path), {_GH: _GH_MANIFEST})
    _signal(app, 7, "HTTP 401 Unauthorized: Bad credentials")
    ctx = seen["context"]
    assert "SIGN_IN_NEEDED" in ctx and "rejected the credentials" in ctx
    assert "CONFIG_HINT" not in ctx


def test_a_server_without_an_account_still_gets_config_hint(tmp_path, monkeypatch):
    seen = _wire(monkeypatch)
    app = _FakeApp(_goal(tmp_path, server=_BRAVE), {_BRAVE: _BRAVE_MANIFEST})
    _signal(app, 7, "HTTP 401 Unauthorized")
    assert "CONFIG_HINT" in seen["context"]
    assert "SIGN_IN_NEEDED" not in seen["context"]


def test_the_batch_path_hints_too(tmp_path, monkeypatch):
    seen = _wire(monkeypatch)
    app = _FakeApp(_goal(tmp_path), {_GH: _GH_MANIFEST})
    asyncio.run(root_handlers.on_dispatch_signals(app, _LOG, [_exit(7, _claim())]))
    assert "SIGN_IN_NEEDED" in seen["context"]


# -- the sign_in action -------------------------------------------------------

_FAKE_DMCP = r"""#!{python}
import json, os, sys
with open(os.environ["FAKE_DMCP_ARGV"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
mode = os.environ.get("FAKE_DMCP_MODE", "ok")
if mode == "crash":
    sys.stderr.write("Error: no configured registry declares a sign-in provider 'github'\n")
    sys.exit(1)
hosted = sys.argv[2] == "--for"
server = sys.argv[sys.argv.index("--for") + 1]
if hosted:
    print(json.dumps({{"type": "authorize", "server": server,
                      "url": "{auth_url}", "expires_in": 300}}), flush=True)
else:
    print(json.dumps({{"type": "device_code", "provider": "github", "provider_name": "GitHub",
                      "verification_uri": "{uri}", "user_code": "{code}", "expires_in": 900}}), flush=True)
if mode == "denied":
    print(json.dumps({{"type": "result", "status": "denied", "message": "sign-in was declined"}}))
    sys.exit(1)
print(json.dumps({{"type": "result", "status": "signed_in",
                  "provider": server if hosted else "github",
                  "account": "default" if hosted else "octocat", "scopes": ["repo"],
                  "store": "keyring", "granted_to": server}}))
"""


class _Output:
    def __init__(self):
        self.shown = []

    def handle_response(self, response):
        self.shown.append(response["output"])


class _ActionApp(_FakeApp):
    def __init__(self, manifests):
        super().__init__(None, manifests)
        self.output_manager = _Output()


@pytest.fixture
def fake_dmcp(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("the fake dmcp is a shebang script")
    path = tmp_path / "dmcp"
    path.write_text(
        _FAKE_DMCP.format(
            python=sys.executable, uri=_URI, code=_CODE, auth_url=_AUTH_URL
        )
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setattr(Config, "DMCP_BINARY", str(path))
    monkeypatch.setenv("FAKE_DMCP_ARGV", str(argv_log))
    return argv_log


def _act(app, **action):
    parsed = TaskParser().parse({"action": "sign_in", **action})
    asyncio.run(root_actions._handle_sign_in(app, _LOG, parsed, 0, 10))


def test_signing_in_shows_the_code_to_the_user_and_only_the_outcome_to_the_model(
    fake_dmcp, monkeypatch
):
    seen = _wire(monkeypatch, root_actions)
    app = _ActionApp({_GH: _GH_MANIFEST})
    _act(app, server_id=_GH, provider="github")

    assert json.loads(fake_dmcp.read_text()) == [
        "login",
        "github",
        "--for",
        _GH,
        "--json",
    ]
    assert len(app.output_manager.shown) == 1
    shown = app.output_manager.shown[0]
    assert _CODE in shown and _URI in shown and _GH in shown

    ctx = seen["context"]
    assert "SIGN_IN_RESULT" in ctx and "octocat" in ctx
    assert _CODE not in ctx and _URI not in ctx


def test_a_declined_sign_in_is_reported_as_declined(fake_dmcp, monkeypatch):
    seen = _wire(monkeypatch, root_actions)
    monkeypatch.setenv("FAKE_DMCP_MODE", "denied")
    _act(_ActionApp({_GH: _GH_MANIFEST}), server_id=_GH, provider="github")
    assert "declined" in seen["context"]


def test_a_dmcp_failure_reports_its_reason(fake_dmcp, monkeypatch):
    seen = _wire(monkeypatch, root_actions)
    monkeypatch.setenv("FAKE_DMCP_MODE", "crash")
    app = _ActionApp({_GH: _GH_MANIFEST})
    _act(app, server_id=_GH, provider="github")
    assert "SIGN_IN_ERROR" in seen["context"]
    assert "no configured registry declares" in seen["context"]
    assert app.output_manager.shown == []


def test_an_undeclared_provider_never_starts_dmcp(fake_dmcp, monkeypatch):
    seen = _wire(monkeypatch, root_actions)
    _act(_ActionApp({_GH: _GH_MANIFEST}), server_id=_GH, provider="google")
    assert "SIGN_IN_ERROR" in seen["context"]
    assert (
        not fake_dmcp.exists()
    ), "dmcp login was run for a provider the server never declared"


def test_a_second_sign_in_for_the_same_server_waits(fake_dmcp, monkeypatch):
    seen = _wire(monkeypatch, root_actions)
    app = _ActionApp({_GH: _GH_MANIFEST})
    app._sign_ins_in_flight = {(_GH, "github")}
    _act(app, server_id=_GH, provider="github")
    assert "SIGN_IN_PENDING" in seen["context"]
    assert not fake_dmcp.exists()


# -- install and docs -----------------------------------------------------------


class _InstallDispatch(_FakeDispatch):
    async def install_server(self, server_id):
        return {"ok": True}

    async def run_server_setup(self, server_id):
        return {"ok": True}

    async def auto_index_server(self, **kwargs):
        return None

    async def list_server_tools(self, server_id):
        return {"tools": [{"name": "create_issue", "description": "Open an issue"}]}

    async def set_server_config(self, server_id, values):
        self.config = values


def test_install_does_not_ask_for_the_keys_the_account_fills(monkeypatch, tmp_path):
    seen = _wire(monkeypatch, root_actions)
    monkeypatch.setattr(root_actions, "get_embeddings", lambda app: None)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    asked = {}

    async def _collector(app, logger, server_id, manifest, props, saved):
        asked["keys"] = [p["key"] for p in props]
        return "collected", {"GITHUB_API_URL": "https://api.github.com"}, []

    monkeypatch.setattr(root_actions, "_collect_config_securely", _collector)
    app = _ActionApp({_GH: _GH_MANIFEST})
    app.dispatch = _InstallDispatch({_GH: _GH_MANIFEST})
    asyncio.run(
        root_actions._handle_install_server(app, _LOG, {"server_id": _GH}, 0, 10)
    )
    assert asked["keys"] == ["GITHUB_API_URL"]
    ctx = seen["context"]
    assert "INSTALL_RESULT" in ctx and "SIGN_IN_NEEDED" in ctx


def test_server_docs_name_the_account(monkeypatch):
    seen = _wire(monkeypatch, root_actions)
    monkeypatch.setattr(root_actions, "_registry_state_note", _no_state)
    app = _ActionApp({_GH: _GH_MANIFEST})
    app.dispatch = _InstallDispatch({_GH: _GH_MANIFEST})
    asyncio.run(
        root_actions._handle_get_server_docs(app, _LOG, {"server_id": _GH}, 0, 10)
    )
    assert "ACCOUNT: works in the user's github account" in seen["context"]


async def _no_state(app, logger, server_id):
    return None


def test_prompt_messages_are_for_the_user():
    msg = sign_in.prompt_message(
        {
            "type": "device_code",
            "provider_name": "GitHub",
            "verification_uri": _URI,
            "user_code": _CODE,
            "expires_in": 900,
        },
        _GH,
    )
    assert _CODE in msg and _URI in msg and "15 minute" in msg
    msg = sign_in.prompt_message(
        {"type": "authorize", "url": _AUTH_URL, "expires_in": 300}, _HOSTED
    )
    assert _AUTH_URL in msg and "browser" in msg and "5 minute" in msg


# -- hosted servers: their own sign-in (MCP OAuth, dmcp#70) --------------------


def _hosted_claim(reason="no_account", server=_HOSTED):
    body = {
        "server": server,
        "provider": server,
        "scopes": [],
        "reason": reason,
        "hosted": True,
    }
    return (
        f"Error: Sign-in needed\n{sign_in.CREDENTIAL_REQUIRED_PREFIX}{json.dumps(body)}"
    )


def test_a_hosted_refusal_becomes_a_sign_in_hint(tmp_path, monkeypatch):
    seen = _wire(monkeypatch)
    app = _FakeApp(_goal(tmp_path, server=_HOSTED), {_HOSTED: _HOSTED_MANIFEST})
    _signal(app, 7, _hosted_claim())
    ctx = seen["context"]
    assert "SIGN_IN_NEEDED" in ctx and "nobody has signed in to it yet" in ctx
    assert f'"server_id": "{_HOSTED}", "provider": "{_HOSTED}"' in ctx


def test_a_hosted_claim_from_a_server_without_its_own_sign_in_is_ignored(
    tmp_path, monkeypatch
):
    """Only a manifest with an oauth transport makes the server its own provider."""
    seen = _wire(monkeypatch)
    app = _FakeApp(_goal(tmp_path, server=_BRAVE), {_BRAVE: _BRAVE_MANIFEST})
    _signal(app, 7, _hosted_claim(server=_BRAVE))
    assert "SIGN_IN_NEEDED" not in seen["context"]


def test_a_hosted_sign_in_runs_dmcp_without_a_provider_and_shows_the_link(
    fake_dmcp, monkeypatch
):
    seen = _wire(monkeypatch, root_actions)
    app = _ActionApp({_HOSTED: _HOSTED_MANIFEST})
    _act(app, server_id=_HOSTED, provider=_HOSTED)
    assert json.loads(fake_dmcp.read_text()) == ["login", "--for", _HOSTED, "--json"]
    assert len(app.output_manager.shown) == 1
    assert _AUTH_URL in app.output_manager.shown[0]
    ctx = seen["context"]
    assert "SIGN_IN_RESULT" in ctx
    assert _AUTH_URL not in ctx


def test_a_hosted_install_ends_with_a_sign_in_hint(monkeypatch, tmp_path):
    seen = _wire(monkeypatch, root_actions)
    monkeypatch.setattr(root_actions, "get_embeddings", lambda app: None)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    app = _ActionApp({_HOSTED: _HOSTED_MANIFEST})
    app.dispatch = _InstallDispatch({_HOSTED: _HOSTED_MANIFEST})
    asyncio.run(
        root_actions._handle_install_server(app, _LOG, {"server_id": _HOSTED}, 0, 10)
    )
    assert "SIGN_IN_NEEDED" in seen["context"]
    assert f'"provider": "{_HOSTED}"' in seen["context"]


def test_hosted_server_docs_name_the_sign_in():
    note = sign_in.sign_in_note_for_docs(_HOSTED_MANIFEST)
    assert "signs the user in itself, at mcp.example.invalid" in note
