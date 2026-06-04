"""Assemble ROOT-mode LLM context and compact payloads for prompts."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any, Dict, List, Optional

from ..config import Config


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

    if new_input:
        parts.append(f"NEW INPUT: {new_input}")

    logger.debug(
        "JARVIS: Built root context "
        f"(parts={len(parts)}, chars={sum(len(p) for p in parts)})"
    )

    return "\n".join(parts) if parts else "No active context."


def format_search_results(capability: str, results: List[Dict[str, Any]]) -> str:
    """Format vector/keyword search results into a SEARCH_RESULTS context block.

    Each entry shows server id, installed status, and a one-line description.
    The LLM uses this to pick a server and output get_server_docs or install_server.
    """
    if not results:
        return f'SEARCH_RESULTS: No servers found for "{capability}".'

    lines = [f'SEARCH_RESULTS (top {len(results)} for "{capability}"):']
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
                if configurable_props:
                    required_keys = [
                        p["key"]
                        for p in configurable_props
                        if isinstance(p, dict) and p.get("key")
                    ]
                    if required_keys:
                        key_list = ", ".join(required_keys)
                        example = ", ".join(f'"{k}": "<value>"' for k in required_keys)
                        lines.append(f"  Required config key(s): {key_list}")
                        lines.append(
                            f'  Call: {{"action": "configure_server", "server_id": "{server_id}", "config": {{{example}}}}}'
                        )
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
