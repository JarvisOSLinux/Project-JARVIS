"""Signing a server in to the user's account through dmcp (#229).

dmcp owns accounts: it runs the provider's sign-in, keeps the token in the OS
keyring, and hands it only to servers the user granted it to. JARVIS's part is
to notice that a server needs an account, start ``dmcp login`` when asked, and
show the user the code to type. The model never holds a token, and never sees
the code either — only whether the sign-in worked.

A tool call that dmcp refused for want of an account fails with a
``credential_required: {json}`` line in its error text. That text also carries
the server's own stderr, so a server can print a look-alike line. The line is
therefore only ever a trigger: which server failed comes from the dispatch PID
map, and which provider it may sign in to comes from that server's installed
manifest. A forged line can at most prompt a sign-in that server was already
entitled to ask for, and nothing happens unless the user completes it.
"""

from __future__ import annotations

import asyncio
import json
from logging import Logger
from typing import Any, Callable, Dict, Iterable, List, Optional

from ..config import Config
from ..core.secret_scrubber import redact_secrets

CREDENTIAL_REQUIRED_PREFIX = "credential_required: "

# The device code is valid for a quarter hour at most providers; past this the
# sign-in has certainly failed, and a wedged dmcp must not park the goal forever.
SIGN_IN_TIMEOUT_SECS = 20 * 60

_REASONS = {
    "no_account": "no {provider} account is signed in",
    "not_granted": "it has not been given the {provider} account",
    "insufficient_scope": "the {provider} account is missing a permission it needs",
    "expired": "the {provider} sign-in expired",
    "store_unavailable": "the keyring holding the {provider} account could not be read",
    "rejected": "{provider} rejected the credentials it was given",
}


def declared_credentials(manifest: Any) -> Dict[str, Dict[str, Any]]:
    """The manifest's ``credentials`` entries, by provider. Malformed ones are skipped."""
    out: Dict[str, Dict[str, Any]] = {}
    for decl in (manifest or {}).get("credentials") or []:
        if isinstance(decl, dict) and isinstance(decl.get("provider"), str):
            out.setdefault(decl["provider"], decl)
    return out


def credential_keys(manifest: Any) -> set:
    """Config keys a signed-in account fills, so no form should ask for them."""
    keys = set()
    for decl in declared_credentials(manifest).values():
        inject = decl.get("inject")
        if isinstance(inject, dict):
            keys.update(k for k in inject if isinstance(k, str))
    return keys


def login_tool(manifest: Any) -> Optional[str]:
    login = (manifest or {}).get("login")
    if isinstance(login, dict) and isinstance(login.get("tool"), str):
        return login["tool"]
    return None


def _strings(payload: Any) -> Iterable[str]:
    if isinstance(payload, str):
        yield payload
    elif isinstance(payload, dict):
        for value in payload.values():
            yield from _strings(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _strings(value)


def find_credential_required(payload: Any) -> List[Dict[str, Any]]:
    """Every ``credential_required`` object in a signal's text, parsed.

    Walks the payload's strings, so it reads the error wherever dispatch put it
    and never parses a JSON-escaped copy of it.
    """
    found = []
    decoder = json.JSONDecoder()
    for text in _strings(payload):
        start = 0
        while True:
            at = text.find(CREDENTIAL_REQUIRED_PREFIX, start)
            if at < 0:
                break
            start = at + len(CREDENTIAL_REQUIRED_PREFIX)
            try:
                obj, _ = decoder.raw_decode(text, start)
            except ValueError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("provider"), str):
                found.append(obj)
    return found


def sign_in_hint(server_id: str, manifest: Any, provider: str, reason: str) -> str:
    """What the model is told when a server needs an account, built from local data only."""
    why = _REASONS.get(reason, _REASONS["no_account"]).format(provider=provider)
    lines = [
        f"SIGN_IN_NEEDED: {server_id} cannot run because {why}.",
        f'  Call: {{"action": "sign_in", "server_id": "{server_id}", "provider": "{provider}"}}',
        "  JARVIS shows the user a code to enter on the provider's own page; you never see it "
        "or any token. Tell the user a sign-in is needed and why, then emit sign_in. Never "
        "ask for a token, password or code in chat, and do not use configure_server for "
        "the keys the account fills.",
    ]
    tool = login_tool(manifest)
    if tool:
        lines.append(
            f"  The server also has its own sign-in tool, '{tool}', if the user prefers it."
        )
    return "\n".join(lines)


def sign_in_note_for_docs(manifest: Any) -> str:
    """One SERVER_DOCS line naming the account a server works in."""
    decls = declared_credentials(manifest)
    if not decls:
        return ""
    providers = ", ".join(sorted(decls))
    keys = ", ".join(sorted(credential_keys(manifest)))
    return (
        f"  ACCOUNT: works in the user's {providers} account (fills {keys}). If a call fails "
        "with SIGN_IN_NEEDED, use sign_in — never configure_server for these keys."
    )


def device_code_message(event: Dict[str, Any], server_id: str) -> str:
    """The line the user sees. Only the user: it never enters the model's context."""
    name = event.get("provider_name") or event.get("provider") or "the provider"
    uri = event.get("verification_uri_complete") or event.get("verification_uri") or ""
    code = event.get("user_code") or ""
    minutes = max(1, int(event.get("expires_in") or 0) // 60)
    return (
        f"To let {server_id} use your {name} account, open {uri} and enter the code "
        f"{code}. The code expires in about {minutes} minute(s)."
    )


async def run_sign_in(
    logger: Logger,
    provider: str,
    server_id: str,
    on_code: Callable[[Dict[str, Any]], None],
    *,
    timeout: float = SIGN_IN_TIMEOUT_SECS,
) -> Dict[str, Any]:
    """Run ``dmcp login <provider> --for <server> --json`` to its end.

    ``on_code`` receives the device-code event the moment dmcp prints it. The
    return value is dmcp's result event (``status``: signed_in / denied /
    expired / error), or an error result when dmcp gave none.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            Config.DMCP_BINARY,
            "login",
            provider,
            "--for",
            server_id,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"type": "result", "status": "error", "message": "dmcp is not installed"}

    result: Optional[Dict[str, Any]] = None

    async def read() -> None:
        nonlocal result
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "device_code":
                on_code(event)
            elif event.get("type") == "result":
                result = event

    stderr = b""
    try:
        reader = asyncio.ensure_future(read())
        collect = asyncio.ensure_future(
            proc.stderr.read() if proc.stderr else asyncio.sleep(0)
        )
        await asyncio.wait_for(asyncio.gather(reader, proc.wait()), timeout=timeout)
        stderr = (await collect) or b""
    except (asyncio.TimeoutError, asyncio.CancelledError) as e:
        proc.kill()
        await proc.wait()
        if isinstance(e, asyncio.CancelledError):
            raise
        return {
            "type": "result",
            "status": "expired",
            "message": "the sign-in timed out",
        }

    if result is None:
        detail = stderr.decode(errors="replace").strip()[-500:]
        result = {
            "type": "result",
            "status": "error",
            "message": detail or f"dmcp login exited with status {proc.returncode}",
        }
    if isinstance(result.get("message"), str):
        result["message"], _ = redact_secrets(result["message"])
    logger.info(
        f"JARVIS: sign-in for {server_id} ({provider}) finished: {result.get('status')}"
    )
    return result
