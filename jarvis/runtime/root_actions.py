"""ROOT-mode response action handling helpers."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any

from ..config import Config
from ..core.setup_queue import missing_required, setting_fields
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


def _sensitive_keys(props: Any) -> set:
    return {
        p["key"]
        for p in props or []
        if isinstance(p, dict) and p.get("key") and p.get("sensitive")
    }


async def _handle_install_server(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
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

    # Step 2: a required setting nobody has filled in becomes a pending setup
    # the user completes whenever and wherever suits them (#254). Waiting here
    # on a form held the whole goal, crashed the TUI once, and gave up with no
    # display. With nothing required missing, setup runs now on the defaults:
    # optional settings are no reason to stop.
    manifest = await app.dispatch.get_server_manifest(server_id)
    missing = missing_required(
        manifest.get("configurableProperties") or [], manifest.get("config") or {}
    )
    if missing:
        await _queue_setup(app, logger, server_id, manifest, missing, depth)
        return
    await finish_install(app, logger, server_id, manifest, depth)


async def finish_install(
    app: Any,
    logger: Logger,
    server_id: str,
    manifest: dict,
    depth: int,
    *,
    run_setup: bool = True,
    preface: str = "",
) -> None:
    """Run the server's setup script, then hand the model its docs (INSTALL_RESULT)."""
    if run_setup:
        # The script receives MCP_CONFIG_* env vars from manifest.config.
        emit_activity(app, f"Running setup for {server_id}…", kind="dispatch")
        setup_result = await app.dispatch.run_server_setup(server_id)
        if "error" in setup_result:
            logger.warning(
                f"JARVIS: setup '{server_id}' failed: {setup_result['error']}"
            )
            context = build_root_context(app, logger)
            context += preface
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

    # Fetch docs so the LLM can dispatch immediately.
    tools_result = await app.dispatch.list_server_tools(server_id)
    tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
    tools_error = tools_result.get("error") if isinstance(tools_result, dict) else None

    context = build_root_context(app, logger)
    context += preface
    context += f"\nINSTALL_RESULT: {server_id} installed successfully."
    context += "\n" + format_server_docs(
        server_id,
        tools,
        error=tools_error,
        configurable_props=manifest.get("configurableProperties") or None,
    )
    response = await ask_llm(
        app, logger, context, tag="root-install-result", mode="root"
    )
    await app._act_on_root_response(response, depth + 1)


async def _queue_setup(
    app: Any,
    logger: Logger,
    server_id: str,
    manifest: dict,
    missing: list,
    depth: int,
    *,
    kind: str = "install",
    props: Any = None,
    proposed: Any = None,
    note: str = "",
) -> None:
    """Record a pending setup, offer the form where there is one, tell the model."""
    active = app.goals.get_active_goals()
    pending = app.setups.add(
        server_id,
        manifest,
        missing,
        active[-1] if active else None,
        kind=kind,
        props=props,
        proposed=proposed,
    )
    keys = [f["key"] for f in pending.fields]
    logger.info(
        f"JARVIS: setup {pending.setup_id} ({pending.kind}) for {server_id} waits "
        f"for the user: {keys}"
    )
    emit_activity(
        app,
        (
            f"{server_id} needs setup: {', '.join(missing)}"
            if pending.kind == "install"
            else f"{server_id}: settings to review: {', '.join(keys)}"
        ),
        kind="dispatch",
    )

    form_open = False
    offer = getattr(app, "setup_ready_callback", None)
    if offer is not None:
        try:
            await offer(pending)
            form_open = True
        except Exception as e:
            logger.warning(
                f"JARVIS: could not open the setup form for {server_id}: {e}"
            )

    where = (
        "A setup form has opened in the TUI; it can also be filled in from any "
        "terminal with"
        if form_open
        else "The user fills it in outside the chat: in the TUI, or from any terminal with"
    )
    context = build_root_context(app, logger)
    if pending.kind == "install":
        context += (
            f"\nSETUP_PENDING: {server_id} is installed but cannot run until the user "
            f"sets {', '.join(pending.missing)}. {where} `jarvis setups fill "
            f"{pending.setup_id}`. Tell the user that, briefly. Never ask for these "
            "values in chat. You will be told when the setup is done, and can then "
            "carry on with what the user asked."
        )
    else:
        context += (
            f"\nCONFIGURE_PENDING: the user reviews these settings of {server_id} in "
            f"a form, with the values you proposed filled in: {', '.join(keys)}. "
            f"Nothing changes until they save it. {where} `jarvis setups fill "
            f"{pending.setup_id}`. Tell the user that, briefly. Never ask for these "
            "values in chat. You will be told when they are saved."
        )
    context += note
    response = await ask_llm(
        app, logger, context, tag="root-install-setup-pending", mode="root"
    )
    await app._act_on_root_response(response, depth + 1)


async def on_setup_result(app: Any, logger: Logger, data: dict) -> None:
    """A pending setup was filled in or cancelled (#254): resume, or say so."""
    ref = str(data.get("id") or "")
    pending = app.setups.pop(ref)
    if pending is None:
        logger.warning(f"JARVIS: Ignoring a result for unknown setup {ref!r}")
        return
    server_id = pending.server_id
    origin = (
        f"\nThe user originally asked: {pending.goal_description}"
        if pending.goal_description
        else ""
    )

    if data.get("type") == "cancel_setup":
        logger.info(f"JARVIS: setup {pending.setup_id} for {server_id} cancelled")
        context = build_root_context(app, logger)
        if pending.kind == "configure":
            context += (
                f"\nCONFIGURE_CANCELLED: the user closed the settings of {server_id} "
                "without saving; nothing changed. Do not ask for the values in chat."
                + origin
            )
        else:
            context += (
                f"\nSETUP_CANCELLED: the user cancelled the setup of {server_id}. It "
                "stays installed but cannot run until it is set up; installing it again "
                "asks again." + origin
            )
        response = await ask_llm(
            app, logger, context, tag="root-setup-cancelled", mode="root"
        )
        await app._act_on_root_response(response)
        return

    manifest = await app.dispatch.get_server_manifest(server_id)
    config = manifest.get("config") or {}
    if pending.kind == "configure":
        missing = missing_required(pending.fields, config)
    else:
        missing = missing_required(manifest.get("configurableProperties") or [], config)
    if missing:
        # The form saved, but something required is still unset (a write
        # failed, or the manifest changed): it stays pending rather than half-run.
        await _queue_setup(
            app,
            logger,
            server_id,
            manifest,
            missing,
            0,
            kind=pending.kind,
            props=pending.fields,
            proposed=pending.proposed,
        )
        return

    if pending.kind == "configure":
        logger.info(f"JARVIS: settings {pending.setup_id} for {server_id} saved")
        await feed_root_summary(
            app,
            logger,
            "CONFIGURE_RESULT",
            f"the user saved the settings of {server_id}: "
            f"{_describe_settings(pending.fields, config)}. Call get_server_docs for "
            f"{server_id} to verify the server starts." + origin,
            0,
        )
        return

    logger.info(f"JARVIS: setup {pending.setup_id} for {server_id} filled in")
    await finish_install(
        app,
        logger,
        server_id,
        manifest,
        0,
        preface=f"\nSETUP_COMPLETED: the user filled in the setup of {server_id}."
        + origin,
    )


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


_PLACEHOLDERS = ("your_", "your-", "<your", "placeholder", "api_key_here", "insert_")


def _looks_like_placeholder(value: Any) -> bool:
    text = str(value).strip().lower()
    return text.startswith("<") or any(p in text for p in _PLACEHOLDERS)


def _describe_settings(fields: list, config: dict) -> str:
    """The settings' state for the model: values of plain ones, secrets only as set."""
    parts = []
    for f in fields:
        key = f["key"]
        value = str(config.get(key) or "").strip()
        if f.get("sensitive"):
            parts.append(f"{key} {'set' if value else 'not set'} (secret, not shown)")
        else:
            parts.append(f"{key} = {value}" if value else f"{key} not set")
    return "; ".join(parts)


async def _handle_configure_server(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    """Queue the settings the model named, for the user to review in the setup form.

    The model never writes a setting itself (#254; decided 2026-10-18): a
    read-only switch or a server URL can do as much harm as a bad API key. A
    secret is never taken from the model at all (#242). A non-secret value it
    proposes prefills its field, marked as its suggestion, and the user saves
    it, changes it, or closes the form.
    """
    server_id = parsed["server_id"]
    emit_activity(app, f"Configuring {server_id}…", kind="dispatch")

    manifest = await app.dispatch.get_server_manifest(server_id)
    if not manifest:
        await feed_root_summary(
            app,
            logger,
            "CONFIGURE_ERROR",
            f"{server_id} is not installed, or its manifest cannot be read; nothing was set",
            depth,
        )
        return
    props = [
        p
        for p in manifest.get("configurableProperties") or []
        if isinstance(p, dict) and isinstance(p.get("key"), str)
    ]
    declared = [p["key"] for p in props]
    # Named the way the adapter writes them, before anything is classified: a
    # secret given as `--api-key` was checked as an unknown plain key and then
    # stored as API_KEY.
    config = {
        app.dispatch._sanitize_config_key(k): v for k, v in parsed["config"].items()
    }
    # A setting reaches the server as an environment variable of that name, so
    # an undeclared one (NODE_OPTIONS, LD_PRELOAD) would run code in it. dmcp
    # refuses them too; this says why to the model.
    undeclared = [k for k in config if k not in declared]
    note = ""
    if undeclared:
        settings = (
            f"its settings are {', '.join(declared)}"
            if declared
            else "it has no settings"
        )
        note = (
            f" Note: {', '.join(undeclared)} {'is' if len(undeclared) == 1 else 'are'} "
            f"not a setting of {server_id} and was dropped; {settings}."
        )
    requested = [p for p in props if p["key"] in config]
    if not requested:
        await feed_root_summary(
            app,
            logger,
            "CONFIGURE_RESULT",
            f"nothing was set on {server_id}.{note}",
            depth,
        )
        return

    secret = _sensitive_keys(props)
    current = manifest.get("config") or {}
    # Only a plain value the model really has. A placeholder is no proposal:
    # the field is left for the user, who sees it empty rather than "<your url>".
    proposed = {
        k: str(v).strip()
        for k, v in config.items()
        if k in declared
        and k not in secret
        and str(v).strip()
        and not _looks_like_placeholder(v)
    }
    missing = missing_required(requested, current)
    unchanged = not missing and all(
        p["key"] not in secret
        and proposed.get(p["key"]) == str(current.get(p["key"]) or "").strip()
        for p in requested
    )
    if unchanged:
        await feed_root_summary(
            app,
            logger,
            "CONFIGURE_RESULT",
            f"{server_id} already has those values: "
            f"{_describe_settings(setting_fields(requested), current)}.{note}",
            depth,
        )
        return
    await _queue_setup(
        app,
        logger,
        server_id,
        manifest,
        missing,
        depth,
        kind="configure",
        props=requested,
        proposed=proposed,
        note=note,
    )


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
