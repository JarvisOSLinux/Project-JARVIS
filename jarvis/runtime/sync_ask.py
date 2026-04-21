"""Synchronous one-shot ask path and voice callback when the event loop is idle."""

from __future__ import annotations

from logging import Logger
from typing import Any, Dict

from ..config import Config


def sync_ask(app: Any, logger: Logger, prompt: str) -> Dict[str, Any]:
    """Synchronous single-prompt interface for one-shot CLI usage."""
    logger.info(f"JARVIS: Processing: '{prompt}'")

    app.sessions.ensure_session()
    if app.contextor:
        app.contextor.auto_store_prompt(
            prompt,
            session_id=app.sessions.current_id,
        )

    app.goals.add_goal(prompt)
    app.llm.switch_mode("root")
    context = app._build_root_context(new_input=prompt)

    response = app._ask_llm_sync(context, tag="ask")
    parsed = app.task_parser.parse(response)

    if "error" in parsed:
        result = {"output": "I had trouble processing that. Could you try again?"}
    elif parsed["action"] == "respond":
        result = {"output": parsed["output"]}
    else:
        result = {"output": f"Action: {parsed.get('action', 'unknown')}"}

    app.output_manager.handle_response(result)
    if "error" not in parsed and parsed.get("action") == "respond":
        app._persist_assistant_turn(result.get("output", ""))

    if Config.RESET_HISTORY_AFTER_RESPONSE:
        app.llm.reset_history()

    return result


def handle_voice_command(app: Any, logger: Logger, text: str) -> dict:
    """Voice callback: inject into event loop when running, else sync_ask."""
    if app._running and app.events._running:
        app.events.inject_user_input(text)
        return {}
    return sync_ask(app, logger, text)
