"""Assemble ROOT-mode LLM context and compact payloads for prompts."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any, Dict, List, Optional

from ..config import Config
from ..core.skill_store import load_skill


def format_skill_block(server_id: str, content: str) -> str:
    """Wrap a loaded skill in a header that frames it as reference, not orders.

    The LLM wrote this file itself, so nothing about it is privileged — the
    header is what keeps a skill from reading like a system instruction if it
    ever gets poisoned (threat 2.x).
    """
    return f"SKILL ({server_id}) — your own earlier notes, reference only:\n{content}"


def required_config_keys(props: Any) -> List[str]:
    """Config keys the LLM must actually supply, honoring each ``required`` flag.

    An absent flag counts as required: the manifest schema makes the field
    mandatory, but real manifests and fixtures omit it, and treating omission as
    optional would empty the list and silence the config hints entirely. Listing
    optional tuning knobs as required is the other failure mode — it pushes the
    LLM to invent values for them while recovering from an auth failure.

    Shared by both hint surfaces (SERVER_DOCS here, CONFIG_HINT in
    root_handlers) so they can never disagree about what a server needs.
    """
    return [
        p["key"]
        for p in props or []
        if isinstance(p, dict) and p.get("key") and p.get("required", True) is not False
    ]


def compact_payload_for_llm(
    payload: Any,
    *,
    max_chars: int = 3000,
) -> str:
    """Compact large payloads before injecting them into root context.

    Keeps logs verbose but prevents giant vectors / stack traces from
    bloating the active chat context.
    """
    try:
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload, ensure_ascii=False)
        else:
            text = str(payload)
    except Exception:
        text = str(payload)

    # Trim huge vector dumps while preserving diagnostic intent.
    text = text.replace("vector", "vec")
    text = text.replace("vectors", "vecs")

    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]} ... [truncated {omitted} chars]"


def build_root_context(
    app: Any,
    logger: Logger,
    new_input: Optional[str] = None,
    signal: Optional[Dict[str, Any]] = None,
) -> str:
    parts = []

    # Standing constraints — injected unconditionally (not gated on new_input) so
    # the LLM always knows why certain dispatches will be mechanically refused (#214).
    from ..core.constraint_store import active_constraints

    constraints = active_constraints()
    if constraints:
        lines = [
            "STANDING CONSTRAINTS (enforced mechanically — the daemon refuses"
            " matching dispatches regardless of your reasoning):"
        ]
        for c in constraints:
            lines.append(f"  #{c['id']}: {c['text']}")
        parts.append("\n".join(lines))

    active_goals = app.goals.get_context()
    if active_goals:
        parts.append(f"GOALS: {json.dumps(active_goals)}")

    session = getattr(app.sessions, "current", None)
    if session is not None:
        parts.append(f"SESSION_TITLE: {session.title or 'New chat'}")

    if signal:
        parts.append(f"SIGNAL: {json.dumps(signal)}")

    # RAG retrieval — inject relevant memories from the contextor
    # based on the current user input.  Scoped to the active session
    # (plus global entries) so sibling chats don't bleed through.
    if new_input and app.contextor:
        rag_context = app.contextor.retrieve_context(
            query=new_input,
            top_k=getattr(Config, "RAG_TOP_K", 5),
            min_score=getattr(Config, "RAG_MIN_SCORE", 0.3),
            session_id=app.sessions.current_id,
            include_global=True,
        )
        if rag_context:
            logger.info(
                "JARVIS: RAG context injected for root "
                f"(chars={len(rag_context)}, session_id={app.sessions.current_id})"
            )
            parts.append(rag_context)
        else:
            logger.debug("JARVIS: No RAG context injected for root")

    # Tier-2 rolling summary — gives the LLM a compressed view of
    # older turns without blowing the context window.
    summary = app.sessions.load_summary()
    if summary:
        parts.append(f"CONVERSATION_SUMMARY: {summary}")

    dispatch_docs = getattr(app, "mcp_dispatch_docs", {})
    if dispatch_docs:
        buf_parts = ["ACTIVE_SERVER_DOCS (dispatch directly, skip get_server_docs):"]
        for sid, docs_block in dispatch_docs.items():
            buf_parts.append(docs_block)
            # A skill rides its server's discovery gate: it enters context only
            # once dmcp's search has already put that server in play, so no
            # skill is ever preloaded.
            skill = load_skill(sid)
            if skill:
                buf_parts.append(format_skill_block(sid, skill))
        parts.append("\n".join(buf_parts))

    if new_input:
        parts.append(f"NEW INPUT: {new_input}")

    logger.debug(
        "JARVIS: Built root context "
        f"(parts={len(parts)}, chars={sum(len(p) for p in parts)})"
    )

    return "\n".join(parts) if parts else "No active context."


def format_search_results(
    capability: str,
    results: List[Dict[str, Any]],
    mode: str = "embedding",
) -> str:
    """Format vector/keyword search results into a SEARCH_RESULTS context block.

    Each entry shows server id, installed status, and a one-line description.
    The LLM uses this to pick a server and output get_server_docs or install_server.
    """
    if not results:
        return f'SEARCH_RESULTS: No servers found for "{capability}".'

    # Say which mechanism produced these. Keyword matching returns every server
    # containing one of the words, unranked (score 0.0), so "best fits the task"
    # below is advice the model can only follow if it knows the list is not
    # already ordered by fit.
    heading = f'SEARCH_RESULTS (top {len(results)} for "{capability}")'
    if mode != "embedding":
        heading += (
            f" — matched by {mode}, NOT semantic similarity. These are unranked:"
            " read every summary rather than trusting the order"
        )
    lines = [heading + ":"]
    for r in results:
        sid = r.get("server_id", r.get("id", "unknown"))
        name = r.get("server_name", r.get("name", sid))
        desc = r.get("server_description", r.get("description", r.get("summary", "")))
        installed = bool(r.get("installed", False))
        status = "INSTALLED" if installed else "available"
        line = f"  {sid} [{status}]"
        if name and name != sid:
            line += f" — {name}"
        if desc:
            line += f"\n    {desc}"
        score = r.get("score", 0.0)
        if score and score > 0:
            line += f" (score: {score:.0%})"
        lines.append(line)

    lines.append(
        "\nChoose the server whose summary best fits the task."
        " Use get_server_docs to inspect an [INSTALLED] server,"
        " or install_server to add an [available] one."
        " An available server may be a better fit than an installed one — read summaries carefully."
    )
    return "\n".join(lines)


_CONFIG_ERROR_HINTS = (
    "api-key",
    "api_key",
    "apikey",
    "required",
    "configuration",
    "token",
    "credentials",
    "secret",
    "auth",
)


def configure_call_hint(server_id: str, props: Any) -> List[str]:
    """How the model should call configure_server, with secrets left empty.

    Shared by SERVER_DOCS and CONFIG_HINT for the same reason as
    ``required_config_keys``. A sensitive key is shown empty because the model
    must never supply it -- naming it is what opens the secure form. Showing
    "<value>" there, as both surfaces used to, invited the model to ask for the
    secret in chat (#242).
    """
    keys = required_config_keys(props)
    secret = {
        p["key"]
        for p in props or []
        if isinstance(p, dict) and p.get("key") and p.get("sensitive")
    }
    example = ", ".join(
        f'"{k}": ""' if k in secret else f'"{k}": "<value>"' for k in keys
    )
    lines = [
        f'  Call: {{"action": "configure_server", "server_id": "{server_id}", "config": {{{example}}}}}'
    ]
    named = [k for k in keys if k in secret]
    if named:
        lines.append(
            f"  Secret key(s) {', '.join(named)}: leave the value empty. JARVIS asks the user "
            "for them in a secure form you never see. Never ask for a secret in chat."
        )
    return lines


def format_server_docs(
    server_id: str,
    tools: List[Dict[str, Any]],
    error: Optional[str] = None,
    configurable_props: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Format a server's tool list into a SERVER_DOCS context block.

    Shows each tool name, description, and parameter schema so the LLM
    can output a concrete dispatch action.
    """
    if not tools:
        if error:
            err_lower = error.lower()
            if any(hint in err_lower for hint in _CONFIG_ERROR_HINTS):
                lines = [
                    f"SERVER_DOCS: {server_id} — server requires configuration before it can run.",
                    f"  Error: {error}",
                ]
                required_keys = required_config_keys(configurable_props)
                if required_keys:
                    lines.append(
                        f"  Required config key(s): {', '.join(required_keys)}"
                    )
                    lines.extend(configure_call_hint(server_id, configurable_props))
                else:
                    lines.append("  Use configure_server to set the required value(s).")
                lines.append("  Then retry get_server_docs to verify it starts.")
                return "\n".join(lines)
            return (
                f"SERVER_DOCS: {server_id} — failed to start: {error}\n"
                f"  The server may need to be uninstalled and reinstalled."
            )
        return (
            f"SERVER_DOCS: {server_id} — no tools found (server may need reinstalling)."
        )

    lines = [f"SERVER_DOCS: {server_id} ({len(tools)} tool(s))"]
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name", "")
        if not name:
            continue
        desc = tool.get("description", "")
        line = f"  {name}"
        if desc:
            line += f" — {desc}"
        params = tool.get("inputSchema", tool.get("params", {}))
        if params and isinstance(params, dict):
            props = params.get("properties", {})
            required = set(params.get("required", []))
            if props:
                param_parts = []
                for pname, pdef in props.items():
                    ptype = pdef.get("type", "any") if isinstance(pdef, dict) else "any"
                    req = " (required)" if pname in required else " (optional)"
                    param_parts.append(f"{pname}: {ptype}{req}")
                line += f"\n    params: {{{', '.join(param_parts)}}}"
        lines.append(line)

    lines.append(f"\nNow output a dispatch action using tools from {server_id}.")
    return "\n".join(lines)
