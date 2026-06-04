"""ROOT-mode response action handling helpers."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any

from ..config import Config
from .goal_updates import apply_goal_updates
from .llm_bridge import ask_llm
from .output_hooks import emit_activity, get_embeddings, persist_assistant_turn
from .root_context import (
    build_root_context,
    compact_payload_for_llm,
    format_search_results,
    format_server_docs,
)


async def feed_root_summary(
    app: Any,
    logger: Logger,
    label: str,
    summary: str,
    depth: int,
) -> None:
    """Feed a subsystem summary back into ROOT for the next decision."""
    app.llm.switch_mode("root")
    context = build_root_context(app, logger)
    context += f"\n{label}: {summary}"

    response = await ask_llm(app, logger, context, tag="root-chain")
    await app._act_on_root_response(response, depth + 1)


async def _handle_search_tools(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    capability = parsed["capability"]
    top_k = parsed.get("top_k", 5)
    min_score = parsed.get("min_score", 0.25)
    emit_activity(app, f"Searching for: {capability[:60]}…", kind="dispatch")

    result = await app.dispatch.search_by_capability(
        capability=capability,
        embeddings=get_embeddings(app),
        top_k=top_k,
        min_score=min_score,
    )
    entries = result.get("results", [])
    mode = result.get("mode", "unknown")
    logger.info(
        f"JARVIS: search_tools '{capability}' → {len(entries)} result(s) via {mode}"
    )

    context = build_root_context(app, logger)
    context += "\n" + format_search_results(capability, entries)
    response = await ask_llm(app, logger, context, tag="root-search-tools")
    await app._act_on_root_response(response, depth + 1)


async def _handle_get_server_docs(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    server_id = parsed["server_id"]
    emit_activity(app, f"Fetching docs for {server_id}…", kind="dispatch")

    tools_result = await app.dispatch.list_server_tools(server_id)
    tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
    tools_error = tools_result.get("error") if isinstance(tools_result, dict) else None
    logger.info(f"JARVIS: get_server_docs '{server_id}' → {len(tools)} tool(s)")

    context = build_root_context(app, logger)
    context += "\n" + format_server_docs(server_id, tools, error=tools_error)
    response = await ask_llm(app, logger, context, tag="root-get-server-docs")
    await app._act_on_root_response(response, depth + 1)


async def _handle_install_server(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    import asyncio

    from ..core.params_store import ParamsStore

    server_id = parsed["server_id"]
    emit_activity(app, f"Installing {server_id}…", kind="dispatch")

    # Step 1: bare install (no setup script yet)
    install_result = await app.dispatch.install_server(server_id)
    if "error" in install_result:
        logger.warning(
            f"JARVIS: install_server '{server_id}' failed: {install_result['error']}"
        )
        context = build_root_context(app, logger)
        context += f"\nINSTALL_ERROR: {install_result['error']}"
        response = await ask_llm(app, logger, context, tag="root-install-error")
        await app._act_on_root_response(response, depth + 1)
        return

    # Step 2: check for configurable properties
    manifest = await app.dispatch.get_server_manifest(server_id)
    props = manifest.get("configurableProperties", [])

    if props and app.config_modal_callback:
        # Step 3: pre-fill from saved params
        store = ParamsStore(server_id)
        saved = store.get()
        server_name = manifest.get("name") or server_id
        server_desc = manifest.get("description") or manifest.get("summary") or ""

        emit_activity(app, "Waiting for configuration…", kind="dispatch")

        # Step 4: open modal and await result
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await app.config_modal_callback(
            server_id, server_name, server_desc, props, saved, future
        )
        result = await future

        if not result.confirmed:
            missing = result.missing_required
            logger.info(
                f"JARVIS: install_server '{server_id}' cancelled by user"
                + (f"; missing: {missing}" if missing else "")
            )
            context = build_root_context(app, logger)
            cancelled_msg = f"INSTALL_CANCELLED: {server_id}"
            if missing:
                cancelled_msg += f" — user did not provide: {', '.join(missing)}"
            context += f"\n{cancelled_msg}"
            response = await ask_llm(app, logger, context, tag="root-install-cancelled")
            await app._act_on_root_response(response, depth + 1)
            return

        # Step 5: persist config to manifest via dmcp config set
        if result.values:
            await app.dispatch.set_server_config(server_id, result.values)
            store.set_many(result.values)

    # Step 6: run setup script (receives MCP_CONFIG_* env vars from manifest.config)
    emit_activity(app, f"Running setup for {server_id}…", kind="dispatch")
    setup_result = await app.dispatch.run_server_setup(server_id)
    if "error" in setup_result:
        logger.warning(
            f"JARVIS: setup '{server_id}' failed: {setup_result['error']}"
        )
        context = build_root_context(app, logger)
        context += f"\nINSTALL_ERROR: setup failed — {setup_result['error']}"
        response = await ask_llm(app, logger, context, tag="root-setup-error")
        await app._act_on_root_response(response, depth + 1)
        return

    logger.info(f"JARVIS: install_server '{server_id}' complete")
    await app.dispatch.auto_index_server(
        server_id=server_id, embeddings=get_embeddings(app)
    )

    # Step 7: fetch docs so LLM can dispatch immediately
    tools_result = await app.dispatch.list_server_tools(server_id)
    tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
    tools_error = tools_result.get("error") if isinstance(tools_result, dict) else None

    context = build_root_context(app, logger)
    context += f"\nINSTALL_RESULT: {server_id} installed successfully."
    context += "\n" + format_server_docs(server_id, tools, error=tools_error)
    response = await ask_llm(app, logger, context, tag="root-install-result")
    await app._act_on_root_response(response, depth + 1)


async def _handle_uninstall_server(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    server_id = parsed["server_id"]
    emit_activity(app, f"Uninstalling {server_id}…", kind="dispatch")

    result = await app.dispatch.uninstall_server(server_id)
    context = build_root_context(app, logger)
    if "error" in result:
        logger.warning(
            f"JARVIS: uninstall_server '{server_id}' failed: {result['error']}"
        )
        context += f"\nUNINSTALL_ERROR: {result['error']}"
    else:
        logger.info(f"JARVIS: uninstall_server '{server_id}' succeeded")
        context += f"\nUNINSTALL_RESULT: {server_id} removed successfully."

    response = await ask_llm(app, logger, context, tag="root-uninstall-result")
    await app._act_on_root_response(response, depth + 1)


async def _handle_configure_server(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    server_id = parsed["server_id"]
    config = parsed["config"]
    emit_activity(app, f"Configuring {server_id}…", kind="dispatch")

    try:
        await app.dispatch.set_server_config(server_id, config)
        logger.info(f"JARVIS: configure_server '{server_id}' set {list(config.keys())}")
        label = f"CONFIGURE_RESULT: set {len(config)} value(s) on {server_id}"
    except Exception as e:
        logger.warning(f"JARVIS: configure_server '{server_id}' failed: {e}")
        label = f"CONFIGURE_ERROR: {e}"

    context = build_root_context(app, logger)
    context += f"\n{label}"
    response = await ask_llm(app, logger, context, tag="root-configure-server")
    await app._act_on_root_response(response, depth + 1)


async def act_on_root_response(
    app: Any,
    logger: Logger,
    response: dict[str, Any],
    depth: int,
    max_chain_depth: int,
) -> None:
    """Handle a ROOT-mode LLM response."""
    if depth >= max_chain_depth:
        logger.error("JARVIS: Max chain depth reached, forcing respond")
        app.output_manager.handle_response(
            {
                "output": "I got stuck in a loop. Could you try again?",
            }
        )
        return

    parsed = app.task_parser.parse(response)

    if "error" in parsed:
        logger.warning(f"JARVIS: Root parse error: {parsed['error']}")
        app.output_manager.handle_response(
            {
                "output": "I had trouble processing that. Could you try again?",
            }
        )
        return

    action = parsed["action"]
    logger.info(f"JARVIS: Root action='{action}'")
    if action == "respond":
        emit_activity(app, "Composing response…", kind="llm")
    elif action == "dispatch":
        emit_activity(app, "Planning tool execution…", kind="dispatch")
    elif action in ("store", "recall", "search_memory", "list_memory"):
        emit_activity(app, f"Running memory action: {action}", kind="memory")

    apply_goal_updates(app, parsed.get("goal_updates", []))

    if action == "search_tools":
        await _handle_search_tools(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "get_server_docs":
        await _handle_get_server_docs(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "install_server":
        await _handle_install_server(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "uninstall_server":
        await _handle_uninstall_server(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "configure_server":
        await _handle_configure_server(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "respond":
        output = parsed["output"]
        if not output.strip():
            logger.warning("JARVIS: LLM returned empty respond — retrying")
            context = build_root_context(app, logger)
            context += "\nYour previous response had an empty output. Please respond to the user."
            retry_response = await ask_llm(app, logger, context, tag="root-retry-empty")
            await app._act_on_root_response(retry_response, depth + 1)
            return
        app.output_manager.handle_response({"output": output})
        persist_assistant_turn(app, output)
        dismissed = app.goals.dismiss_completed()
        if dismissed:
            logger.info(f"JARVIS: Dismissed {len(dismissed)} completed goal(s)")
        if Config.RESET_HISTORY_AFTER_RESPONSE:
            app.llm.reset_history()

    elif action == "dispatch":
        if "tasks" in parsed:
            await app._dispatch_execute_tasks(parsed["tasks"], depth)
        else:
            logger.warning(
                "JARVIS: dispatch action without tasks — ignored (use search_tools first)"
            )

    # -- Memory actions (direct, no sub-chain) --
    elif action == "store":
        if not app.contextor:
            await feed_root_summary(
                app,
                logger,
                "STORE_RESULT",
                json.dumps({"error": "Memory is disabled"}),
                depth,
            )
            return
        # Global scope when LLM explicitly sets scope="global";
        # otherwise file under the active session.
        scope = parsed.get("scope", "session")
        sid = None if scope == "global" else app.sessions.current_id
        result = app.contextor.store(
            parsed["theme"],
            parsed["content"],
            session_id=sid,
        )
        await feed_root_summary(
            app,
            logger,
            "STORE_RESULT",
            compact_payload_for_llm(result),
            depth,
        )

    elif action == "recall":
        if not app.contextor:
            await feed_root_summary(
                app,
                logger,
                "RECALL_RESULT",
                json.dumps({"error": "Memory is disabled"}),
                depth,
            )
            return
        result = app.contextor.recall(
            parsed["theme"],
            session_id=app.sessions.current_id,
        )
        await feed_root_summary(
            app,
            logger,
            "RECALL_RESULT",
            compact_payload_for_llm(result),
            depth,
        )

    elif action == "search_memory":
        if not app.contextor:
            await feed_root_summary(
                app,
                logger,
                "SEARCH_MEMORY_RESULT",
                json.dumps(
                    {
                        "results": [],
                        "available": False,
                        "reason": "Memory is disabled",
                    }
                ),
                depth,
            )
            return
        result = app.contextor.semantic_search(
            query=parsed["query"],
            top_k=parsed.get("top_k", 5),
            offset=parsed.get("offset", 0),
            min_score=parsed.get("min_score", 0.3),
            session_id=app.sessions.current_id,
            include_global=True,
        )
        await feed_root_summary(
            app,
            logger,
            "SEARCH_MEMORY_RESULT",
            compact_payload_for_llm(result),
            depth,
        )

    elif action == "list_memory":
        if not app.contextor:
            await feed_root_summary(
                app,
                logger,
                "LIST_MEMORY_RESULT",
                json.dumps({"themes": []}),
                depth,
            )
            return
        result = app.contextor.list_themes(
            session_id=app.sessions.current_id,
        )
        await feed_root_summary(
            app,
            logger,
            "LIST_MEMORY_RESULT",
            compact_payload_for_llm(result),
            depth,
        )
