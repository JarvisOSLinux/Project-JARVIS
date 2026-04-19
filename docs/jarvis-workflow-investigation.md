# JARVIS Workflow Investigation Notes

This document reflects the workflow currently implemented in `jarvis/main.py` and related runtime components.

## 1) Runtime Topology (Current)

JARVIS runs as a hierarchical orchestrator with one event-driven main loop:

- **ROOT mode (LLM):** user-facing reasoning and action selection.
- **DISPATCH mode (LLM):** iterative tool-planning and task orchestration.
- **dispatch binary (Rust MCP server):** concurrent task execution + signal window.
- **contextor binary (Rust):** memory storage, session metadata, semantic retrieval.
- **EventMerger:** single queue for user input + dispatch signals + confirmation responses.

Design intent: one reasoning loop, many concurrent tool workers.

## 2) Input and Event Flow

All runtime inputs are normalized into one async queue:

1. Voice command input (wake-word flow)
2. Interactive stdin (`jarvis chat`)
3. Unix socket input (`jarvis send`)
4. Dispatch signals (`log` polling from dispatch adapter)
5. Confirmation responses (desktop notification / socket UI / CLI prompt)

`Jarvis.run()` consumes this queue sequentially. Task execution can be parallel in dispatch, but ROOT event handling remains serialized.

## 3) ROOT Context Assembly

When ROOT handles user input or a signal, `_build_root_context()` assembles context in this order:

1. `GOALS`
2. `SIGNAL` (if handling dispatch signal)
3. session-scoped RAG context from contextor (only when new user input exists)
4. `CONVERSATION_SUMMARY`
5. `NEW INPUT`

Notes:
- RAG retrieval is scoped to the active session plus global memory (`include_global=True`).
- User prompts are auto-stored via `contextor.auto_store_prompt(...)`.
- If RAG retrieval returns nothing, it is omitted rather than injecting empty blocks.

## 4) ROOT Action Model

After each ROOT LLM response, `TaskParser` validates one action from:

- `respond`
- `dispatch` (with intent or concrete tasks)
- `store`
- `recall`
- `search_memory`
- `list_memory`

Memory actions are direct operations through contextor (no sub-chain). Results are compacted and fed back into ROOT as `*_RESULT` labels.

## 5) DISPATCH Sub-Chain State Machine

For routed tool work (`action=dispatch` with intent), JARVIS enters DISPATCH mode:

1. Select discovery backend (`embedding` or `keyword`) via `DispatchAdapter.select_discovery_mode()`.
2. Install the matching dispatch system prompt.
3. Iterate up to `MAX_CHAIN_DEPTH` through parsed actions:
   - `plan` -> discover matching tools
   - `search` -> keyword server search
   - `list_tools` -> list tools for server
   - `install` -> install server (auto-index if needed)
   - `dispatch` -> send executable tasks
   - `wait` -> pause for external completion
   - `kill` / `defer` -> lifecycle control
   - `done` -> return summary to ROOT

Each iteration logs step number, context size, and selected action.

## 6) Tool Discovery Behavior

Discovery is adaptive:

- **Embedding mode:** uses local embeddings and vector browse against dmcp index.
- **Keyword mode:** uses `dmcp browse -k`.
- **Fallback:** if embedding search has no useful match for a sub-task, keyword fallback is used.

Formatting behavior for prompt injection:

- `MATCHED_TOOLS` = installed + concrete tool names (dispatch-ready)
- `CANDIDATE_SERVERS` = not installed or unresolved server-level candidates

## 7) Confirmation Gate (Non-Blocking)

Tool-level approval is controlled by `ConfirmationManager` and `CONFIRMATION_MODE`:

- `allow_all`: execute everything
- `smart`: confirm only tools marked `confirmation_required`
- `ask_all`: confirm all tools

Flow:
1. `_dispatch_send()` splits approved vs gated tasks.
2. Gated tasks are stashed under a confirmation id.
3. Notification is sent (desktop, socket, or CLI).
4. Dispatch loop returns immediately (`awaiting_confirmation`), without blocking event processing.
5. User response arrives as `CONFIRMATION_RESPONSE` event and resumes execution.

## 8) Session + Concurrency Model

Current behavior is intentionally **single active session + serialized event handling**:

- One active session pointer at a time (`SessionManager`).
- One event processed at a time from `EventMerger`.
- Dispatch tasks run concurrently under Rust `dispatch`, but ROOT reasoning is single-threaded.

This keeps goal tracking and memory scoping deterministic.

## 9) Payload Compaction and Safety

Before reinjecting subsystem outputs into ROOT, payloads are compacted:

- oversized JSON/text is truncated
- noisy fields like large vector dumps are reduced
- full details remain in logs

Purpose: preserve context window quality while retaining operational diagnostics.

## 10) Operational Summary

Current JARVIS workflow is not a linear "STT -> tool -> TTS" chain. It is an event-driven control loop with:

- hierarchical ROOT/DISPATCH reasoning,
- session-scoped memory and RAG,
- concurrent external tool execution via dispatch,
- and non-blocking confirmation + signal feedback.
