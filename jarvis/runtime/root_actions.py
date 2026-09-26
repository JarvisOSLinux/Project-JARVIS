"""ROOT-mode response action handling helpers."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any

from ..config import Config
from ..core.skill_store import delete_skill, save_skill
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
    context = build_root_context(app, logger)
    context += f"\n{label}: {summary}"

    response = await ask_llm(app, logger, context, tag="root-chain", mode="root")
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
    context += "\n" + format_search_results(capability, entries, mode)
    response = await ask_llm(app, logger, context, tag="root-search-tools", mode="root")
    await app._act_on_root_response(response, depth + 1)


def _drift_row(rows: Any, server_id: str) -> dict | None:
    """The `dmcp update --check` row describing one server, if present."""
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("id", server_id) == server_id:
            return row
    return None


async def _registry_state_note(app: Any, logger: Logger, server_id: str) -> str:
    """Registry drift/revocation marker for a server's SERVER_DOCS block (#39).

    Best-effort by construction: get_server_docs is the step the whole tool flow
    hangs off, so a dmcp that is missing, slow, or babbling must cost the LLM its
    docs marker, never its docs.
    """
    try:
        row = _drift_row(await app.dispatch.check_updates(server_id), server_id)
    except Exception as e:
        logger.debug(f"JARVIS: update check skipped for '{server_id}': {e}")
        return ""
    if not row:
        return ""
    if row.get("trust_status") == "removed":
        return "revoked"
    return "update" if row.get("update_available") else ""


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

    # When the server fails to start due to missing config, surface the exact
    # env-var key names from configurableProperties so the LLM doesn't guess.
    configurable_props = None
    if not tools and tools_error:
        manifest = await app.dispatch.get_server_manifest(server_id)
        configurable_props = manifest.get("configurableProperties") or None

    docs_block = format_server_docs(
        server_id, tools, error=tools_error, configurable_props=configurable_props
    )

    state = await _registry_state_note(app, logger, server_id)
    if state == "revoked":
        logger.warning(f"JARVIS: get_server_docs '{server_id}' — server is REVOKED")
        docs_block = (
            "WARNING: this server was REVOKED by the registry — do not use; "
            "inform the user\n" + docs_block
        )
    elif state == "update":
        docs_block += (
            "\nNOTE: update available for this server — if calls fail, "
            "update_server and retry"
        )

    if tools and hasattr(app, "mcp_dispatch_docs"):
        app.mcp_dispatch_docs[server_id] = docs_block

    context = build_root_context(app, logger)
    context += "\n" + docs_block
    response = await ask_llm(
        app, logger, context, tag="root-get-server-docs", mode="root"
    )
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
        response = await ask_llm(
            app, logger, context, tag="root-install-error", mode="root"
        )
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
            response = await ask_llm(
                app, logger, context, tag="root-install-cancelled", mode="root"
            )
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
        logger.warning(f"JARVIS: setup '{server_id}' failed: {setup_result['error']}")
        context = build_root_context(app, logger)
        context += f"\nINSTALL_ERROR: setup failed — {setup_result['error']}"
        response = await ask_llm(
            app, logger, context, tag="root-setup-error", mode="root"
        )
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

    install_configurable_props = manifest.get("configurableProperties") or None
    context = build_root_context(app, logger)
    context += f"\nINSTALL_RESULT: {server_id} installed successfully."
    context += "\n" + format_server_docs(
        server_id,
        tools,
        error=tools_error,
        configurable_props=install_configurable_props,
    )
    response = await ask_llm(
        app, logger, context, tag="root-install-result", mode="root"
    )
    await app._act_on_root_response(response, depth + 1)


async def _handle_update_server(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    """Re-install a drifted server so the agent can heal it inside one goal (#39)."""
    server_id = parsed["server_id"]
    emit_activity(app, f"Updating {server_id}…", kind="dispatch")

    result = await app.dispatch.update_server(server_id)
    if "error" in result:
        logger.warning(f"JARVIS: update_server '{server_id}' failed: {result['error']}")
        await feed_root_summary(app, logger, "UPDATE_ERROR", result["error"], depth)
        return

    logger.info(f"JARVIS: update_server '{server_id}' succeeded")
    await feed_root_summary(
        app,
        logger,
        "UPDATE_RESULT",
        f"{server_id} updated; call get_server_docs to verify",
        depth,
    )


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

    response = await ask_llm(
        app, logger, context, tag="root-uninstall-result", mode="root"
    )
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

    # Reject placeholder values — the LLM must ask the user for real secrets.
    _PLACEHOLDERS = (
        "your_",
        "your-",
        "<your",
        "placeholder",
        "api_key_here",
        "insert_",
    )
    placeholder_keys = [
        k
        for k, v in config.items()
        if any(p in str(v).lower() for p in _PLACEHOLDERS) or str(v).startswith("<")
    ]
    if placeholder_keys:
        logger.warning(
            f"JARVIS: configure_server '{server_id}' rejected placeholder value(s): {placeholder_keys}"
        )
        context = build_root_context(app, logger)
        context += (
            f"\nCONFIGURE_BLOCKED: The value(s) for {placeholder_keys} look like placeholders, "
            "not real credentials. Use respond to ask the user for the actual value(s) before calling configure_server."
        )
        response = await ask_llm(
            app, logger, context, tag="root-configure-placeholder", mode="root"
        )
        await app._act_on_root_response(response, depth + 1)
        return

    try:
        await app.dispatch.set_server_config(server_id, config)
        from ..core.params_store import ParamsStore

        ParamsStore(server_id).set_many(
            {app.dispatch._sanitize_config_key(k): v for k, v in config.items() if v}
        )
        logger.info(f"JARVIS: configure_server '{server_id}' set {list(config.keys())}")
        label = (
            f"CONFIGURE_RESULT: set {len(config)} value(s) on {server_id}. "
            f"Now call get_server_docs for {server_id} to verify the server starts correctly."
        )
    except Exception as e:
        logger.warning(f"JARVIS: configure_server '{server_id}' failed: {e}")
        label = f"CONFIGURE_ERROR: {e}"

    context = build_root_context(app, logger)
    context += f"\n{label}"
    response = await ask_llm(
        app, logger, context, tag="root-configure-server", mode="root"
    )
    await app._act_on_root_response(response, depth + 1)


VISION_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VISION_MAX_IMAGE_BYTES = 20 * 1024 * 1024


async def _handle_analyze_image(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    import asyncio
    import os

    from ..llm.provider_pool import NoVisionProviderError

    path = parsed["path"]
    query = parsed["query"]

    if not Config.VISION_ENABLED:
        await feed_root_summary(
            app,
            logger,
            "VISION_ERROR",
            "vision is disabled (set VISION_ENABLED=true to allow image analysis)",
            depth,
        )
        return

    if not os.path.isfile(path):
        await feed_root_summary(
            app, logger, "VISION_ERROR", f"file not found: {path}", depth
        )
        return

    suffix = os.path.splitext(path)[1].lower()
    if suffix not in VISION_IMAGE_SUFFIXES:
        await feed_root_summary(
            app,
            logger,
            "VISION_ERROR",
            f"unsupported type '{suffix or path}' — supported: png, jpg, jpeg, webp, gif",
            depth,
        )
        return

    size = os.path.getsize(path)
    if size > VISION_MAX_IMAGE_BYTES:
        await feed_root_summary(
            app,
            logger,
            "VISION_ERROR",
            f"image too large ({size} bytes; max {VISION_MAX_IMAGE_BYTES})",
            depth,
        )
        return

    emit_activity(app, f"Analyzing image {os.path.basename(path)}…", kind="llm")
    message = {"role": "user", "content": query, "images": [path]}
    try:
        result = await asyncio.to_thread(
            app.llm.provider.chat, [message], require_vision=True
        )
    except NoVisionProviderError:
        await feed_root_summary(
            app,
            logger,
            "VISION_ERROR",
            "no vision-capable provider configured — add one with "
            "'jarvis providers add --type ollama --model llama3.2-vision --vision'",
            depth,
        )
        return
    except Exception as e:
        logger.warning(f"JARVIS: analyze_image '{path}' failed: {e}")
        await feed_root_summary(app, logger, "VISION_ERROR", str(e), depth)
        return

    logger.info(f"JARVIS: analyze_image '{path}' → {len(result)} char(s)")
    await feed_root_summary(app, logger, "VISION_RESULT", result, depth)


def _split_task_tails(tasks: list) -> tuple[list, list]:
    """Lift each running task's live-output tail out of the status rows (#212).

    The rows are rendered through compact_payload_for_llm's character budget;
    inlining a couple of multi-thousand-character tails there would push the
    pids and states themselves past the truncation point, so the tail has to be
    rendered outside that budget to be readable at all.

    The tail keeps dispatch's provenance wrapper verbatim — it is tool-authored
    output, and unwrapping it here would strip the very marker that tells the
    model to read it as data.
    """
    rows: list = []
    tails: list = []
    for task in tasks:
        if not isinstance(task, dict):
            rows.append(task)
            continue
        tail = task.get("tail")
        if isinstance(tail, str) and tail:
            tails.append((task.get("pid"), tail))
            task = {k: v for k, v in task.items() if k not in ("tail", "tail_hash")}
        rows.append(task)
    return rows, tails


async def _handle_status(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    """Read-only task/goal introspection (#191) — no dispatch, no side effects.

    Lets ROOT answer "what's the status?" by actually looking, instead of
    firing a no-op task just to read the signal window back. The read carries
    each running task's live output (#212), so "still running" can be told
    apart from "still running, and waiting for an answer".
    """
    goal_id = parsed.get("goal_id")
    emit_activity(app, "Checking task status…", kind="dispatch")

    tasks = await app.dispatch.get_task_status()

    goal = app.goals.get_goal(goal_id) if goal_id else None
    if goal:
        tasks = [t for t in tasks if t.get("pid") in goal.task_pids]

    held_pids = [
        t["pid"]
        for t in tasks
        if t.get("state") not in ("running", None) and "pid" in t
    ]
    held_output = await app.dispatch.get_task_output(held_pids) if held_pids else ""

    context = build_root_context(app, logger)
    if goal:
        context += f'\nSTATUS_RESULT (goal [{goal.id}] "{goal.description}"):'
    else:
        context += "\nSTATUS_RESULT (all active tasks):"
    rows, tails = _split_task_tails(tasks)
    context += f"\n{compact_payload_for_llm(rows)}"
    for pid, tail in tails:
        context += f"\nLIVE_OUTPUT (pid {pid}, newest output so far):\n{tail}"
    if held_output:
        context += f"\nHELD_OUTPUT:\n{held_output}"
    if not tasks:
        context += "\n(no running or held tasks)"

    response = await ask_llm(app, logger, context, tag="root-status", mode="root")
    await app._act_on_root_response(response, depth + 1)


async def _handle_skill_write(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    """Create, replace, or delete one server's skill file (#202).

    Nothing gates the content: a skill is self-authored, so what keeps a
    poisoned one out is the prompt discipline governing what the LLM puts in,
    not a filter here. The id is gated, because that one becomes a path.
    """
    server_id = parsed["server_id"]
    content = parsed["content"]

    if not content.strip():
        try:
            removed = delete_skill(server_id)
        except (ValueError, OSError) as e:
            logger.warning(f"JARVIS: skill_write '{server_id}' delete failed: {e}")
            await feed_root_summary(app, logger, "SKILL_WRITE_ERROR", str(e), depth)
            return
        logger.info(f"JARVIS: skill_write '{server_id}' deleted (existed={removed})")
        await feed_root_summary(
            app,
            logger,
            "SKILL_WRITE_RESULT",
            (
                f"skill for {server_id} deleted"
                if removed
                else f"no skill file existed for {server_id}"
            ),
            depth,
        )
        return

    emit_activity(app, f"Saving skill for {server_id}…", kind="memory")
    try:
        save_skill(server_id, content)
    except (ValueError, OSError) as e:
        logger.warning(f"JARVIS: skill_write '{server_id}' failed: {e}")
        await feed_root_summary(app, logger, "SKILL_WRITE_ERROR", str(e), depth)
        return

    size = len(content.encode("utf-8"))
    logger.info(f"JARVIS: skill_write '{server_id}' wrote {size} byte(s)")
    summary = f"skill for {server_id} saved ({size} bytes)"
    if size > Config.SKILL_MAX_BYTES:
        summary += (
            f" — over the {Config.SKILL_MAX_BYTES}-byte cap, so it will not load;"
            " rewrite it shorter"
        )
    await feed_root_summary(app, logger, "SKILL_WRITE_RESULT", summary, depth)


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
        err = parsed["error"]
        logger.warning(f"JARVIS: Root parse error: {err} — retrying with correction")
        context = build_root_context(app, logger)
        context += (
            f"\nYour last response had a format error: {err}\n"
            "Fix the JSON and try again. Reminder:\n"
            '  {"action": "search_tools", "capability": "<domain or service needed>", "goal_updates": []}\n'
            '  {"action": "get_server_docs", "server_id": "<id from SEARCH_RESULTS>", "goal_updates": []}\n'
            '  {"action": "dispatch", "tasks": [{"server": "<id>", "tool": "<tool>", "params": {"command": "..."}}], "goal_updates": []}\n'
            '  {"action": "respond", "output": "<message>", "goal_updates": []}\n'
            "Put action fields at the top level. For a dispatch task, keep the "
            "tool's arguments inside that task's \"params\" object. "
            "Output exactly one JSON object."
        )
        retry_response = await ask_llm(
            app, logger, context, tag="root-retry-parse", mode="root"
        )
        await app._act_on_root_response(retry_response, depth + 1)
        return

    action = parsed["action"]
    logger.info(f"JARVIS: Root action='{action}'")
    if action == "respond":
        emit_activity(app, "Composing response…", kind="llm")
    elif action == "dispatch":
        emit_activity(app, "Planning tool execution…", kind="dispatch")
    elif action in ("store", "recall", "search_memory", "list_memory"):
        emit_activity(app, f"Running memory action: {action}", kind="memory")

    # For respond, defer goal_updates until we confirm the output is non-empty.
    # Applying them on an empty respond dismisses goals before the user sees anything,
    # leaving subsequent retries with no context.
    if action != "respond":
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

    if action == "update_server":
        await _handle_update_server(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "uninstall_server":
        await _handle_uninstall_server(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "configure_server":
        await _handle_configure_server(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "analyze_image":
        await _handle_analyze_image(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "status":
        await _handle_status(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "skill_write":
        await _handle_skill_write(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "respond":
        output = parsed["output"]
        if not output.strip():
            logger.warning("JARVIS: LLM returned empty respond — retrying")
            context = build_root_context(app, logger)
            context += "\nYour previous response had an empty output. Please respond to the user."
            retry_response = await ask_llm(
                app, logger, context, tag="root-retry-empty", mode="root"
            )
            await app._act_on_root_response(retry_response, depth + 1)
            return
        apply_goal_updates(app, parsed.get("goal_updates", []))
        app.output_manager.handle_response({"output": output})
        persist_assistant_turn(app, output)
        if hasattr(app, "mcp_dispatch_docs"):
            app.mcp_dispatch_docs.clear()
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
