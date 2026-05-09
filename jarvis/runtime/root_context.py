"""Assemble ROOT-mode LLM context and compact payloads for prompts."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any, Dict, Optional

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
