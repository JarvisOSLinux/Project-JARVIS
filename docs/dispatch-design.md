# dispatch

**Signal-driven task orchestrator for MCP servers.**

A single LLM instance dispatches multiple MCP tool calls concurrently, then goes idle. `dispatch` runs those tasks in parallel and only wakes the LLM when a signal arrives — a task completes, fails, or needs attention.

This achieves multi-agent-level parallelism without loading multiple LLM instances.

---

## Why

Most LLM orchestration systems either:
- Run tasks sequentially (slow, wastes time waiting for I/O)
- Spawn multiple LLM agents (expensive, high RAM, redundant reasoning)

`dispatch` takes a different approach: **one brain, many hands.** The LLM is the decision maker. MCP servers are the workers. `dispatch` is the nervous system connecting them — routing signals, tracking processes, and waking the brain only when there is something to reason about.

---

## Architecture

```
LLM (any model, via client app)
 │
 │  MCP protocol (stdio)
 ▼
dispatch (Rust, Tokio async runtime)
 │
 │  Spawns tasks, routes signals
 ▼
dmcp (MCP server manager)
 │
 │  Discovers, runs, invokes
 ▼
MCP Servers (git, shell, browser, ...)
```

### Components

| Component | Role |
|-----------|------|
| **LLM** | Decision maker. Produces dispatch lists, interprets signals, decides next action. Not managed by dispatch. |
| **dispatch** | Orchestrator. Spawns tasks, assigns PIDs, manages signal queue, fires reminders, exposes MCP interface. |
| **dmcp** | Server manager. Discovers installed MCP servers, handles install/config/invocation. Required on PATH. |
| **MCP servers** | Workers. Execute actual operations (git pull, file read, web search, etc). Managed by dmcp. |

`dispatch` does not load or manage any LLM. It is a pure coordination layer that any LLM application can connect to via standard MCP protocol.

---

## Requirements

- [dmcp](https://github.com/YakupAtahanov/dmcp) installed and on PATH
- Rust toolchain (for building from source)

```bash
# Install dmcp first
cargo install --git https://github.com/YakupAtahanov/dmcp

# Install dispatch
cargo install --git https://github.com/YakupAtahanov/dispatch
```

---

## How It Works

### 1. Dispatch

The LLM sends a structured task list to `dispatch` via MCP tool call:

```json
{
  "tasks": [
    {
      "server": "git",
      "tool": "pull",
      "params": { "url": "https://github.com/..." },
      "remind_after": 60
    },
    {
      "server": "browser",
      "tool": "search",
      "params": { "query": "Rust async patterns" },
      "remind_after": 15
    }
  ]
}
```

After dispatching, the LLM goes idle. It does not poll. It does not wait. It sleeps until `dispatch` sends it a signal.

### 2. Task Execution

`dispatch` receives the task list and:
1. Resolves each server through `dmcp` (discovery, invocation)
2. Spawns each task as a concurrent async operation via `tokio::spawn`
3. Assigns each task a unique **PID**
4. Pushes an `INIT` signal for each task into the signal queue
5. Starts a reminder timer per task (if `remind_after` is set)

Tasks run independently. They share nothing except the signal channel. When a task finishes — success or failure — it pushes an `EXIT` signal with its output.

### 3. Signals

Every event in the system is a signal. Signals are structured log entries that form the LLM's working memory.

```
[14:02:01] PID 1001 INIT    git pull https://github.com/...
[14:02:01] PID 1002 INIT    browser search "Rust async patterns"
[14:02:03] PID 1001 EXIT    Already up to date.
[14:02:05] PID 1002 EXIT    Found 12 results: [1] "Tokio MCP tutorial"...
```

| Signal | Meaning | Triggered by |
|--------|---------|-------------|
| `INIT` | Task started | dispatch (on spawn) |
| `EXIT` | Task finished (success or failure) | Task completion |
| `REMIND` | Task has been running beyond its `remind_after` threshold | dispatch (timer) |
| `WAIT` | LLM was reminded, decided to let task continue | LLM response |
| `KILL` | Task terminated by LLM decision | LLM response |

### 4. LLM Wakeup

When a signal arrives that the LLM should see, `dispatch` sends a **signal window** — the last 20 signal entries — as context. The LLM reconstructs the current state from this window and decides what to do next.

The LLM responds with one of:

| Action | Meaning | Example |
|--------|---------|---------|
| `dispatch` | Start new tasks | `{"action": "dispatch", "tasks": [...]}` |
| `respond` | Done, deliver answer to user | `{"action": "respond", "message": "Here's what I found..."}` |
| `wait` | Acknowledged reminder, keep task running | `{"action": "wait", "pids": [1003]}` |
| `kill` | Terminate specific tasks | `{"action": "kill", "pids": [1003, 1005]}` |

Any action can include an optional `message` field. If present, the client application should deliver it to the user immediately (e.g., "I'm looking into that, one moment."). This enables the LLM to communicate while tasks are still running.

### 5. Reminders

A reminder is a timer-based signal that fires when a task exceeds its `remind_after` duration (in seconds). The reminder does not kill the task — it wakes the LLM to decide.

```
[14:02:01] PID 1003 INIT    shell execute "cargo build --release"
[14:04:01] PID 1003 REMIND  Running for 120s, no output yet
```

The LLM sees the `REMIND` and decides:
- **Wait**: Task is expected to be slow (e.g., large build). Log a `WAIT`, check again later.
- **Kill**: Task is stuck. Terminate it and try something else.
- **Dispatch**: Start a fallback task while this one continues.

If the LLM responds with `wait`, a new reminder fires after the same interval. This continues until the task exits or the LLM kills it.

Reminder intervals are set per-task by the LLM at dispatch time. The LLM chooses the interval based on its understanding of the expected duration:
- Quick lookups: `"remind_after": 10`
- Network operations: `"remind_after": 30`
- Builds and heavy computation: `"remind_after": 120`
- No reminder needed: omit the field

---

## Signal Window

Rather than maintaining a growing conversation history, the LLM works from a **rolling signal window** — the last 20 signal entries across all tasks. This keeps context size bounded and predictable regardless of how many tasks have run.

The window is the LLM's only state. It does not receive its own previous responses back. It reconstructs its understanding of the situation purely from the signal log.

```
Signal window (last 20):
[14:02:01] PID 1001 INIT    git pull https://github.com/...
[14:02:01] PID 1002 INIT    browser search "Rust async patterns"
[14:02:03] PID 1001 EXIT    Already up to date.
[14:02:05] PID 1002 EXIT    Found 12 results: ...
[14:02:06] PID 1003 INIT    shell execute "cargo build"
[14:04:06] PID 1003 REMIND  Running for 120s
[14:04:06] PID 1003 WAIT    LLM decided to continue waiting
[14:05:30] PID 1003 EXIT    Build succeeded (0 errors, 3 warnings)
```

Output verbosity is the responsibility of MCP server authors. A well-implemented MCP server returns concise, structured output — not raw data dumps. The signal window stores what the server returns as-is.

---

## PID System

Each task receives a unique PID (process identifier) at spawn time. PIDs allow the LLM to:
- Reference specific tasks unambiguously in `kill` and `wait` actions
- Correlate `EXIT` signals with the tasks it dispatched
- Track multiple tasks of the same type (e.g., two parallel git pulls to different repos)

PIDs are assigned incrementally by `dispatch` per session. They are not OS-level PIDs — they are internal identifiers scoped to the current `dispatch` instance.

---

## Server Discovery

`dispatch` does not maintain its own server registry. It delegates discovery entirely to `dmcp`.

For LLM-driven discovery, the recommended pattern is keyword-based browsing rather than full listing. This prevents context window pollution when hundreds or thousands of MCP servers are installed:

```json
{
  "tasks": [
    { "server": "dmcp", "tool": "browse", "params": { "keywords": ["git", "version control"] } }
  ]
}
```

The browse results appear in the signal window as an `EXIT` signal, and the LLM now knows which servers are relevant. It dispatches actual tool calls in the next cycle.

A small set of always-available servers (e.g., `dmcp` itself, `shell`) can be listed in the client's system prompt so the LLM can browse and execute basic commands without a discovery round-trip.

---

## MCP Interface

`dispatch` exposes itself as an MCP server. Client applications connect via stdio:

```bash
dispatch serve
```

### Exposed Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `dispatch` | Dispatch a list of tasks for concurrent execution | `tasks: [{server, tool, params, remind_after?}]` |
| `kill` | Terminate running tasks by PID | `pids: [int]` |
| `wait` | Acknowledge reminder, keep tasks running | `pids: [int]` |
| `status` | Get current state of all active tasks | — |
| `log` | Get the signal window (last N entries) | `count?: int` (default: 20) |

### Client Configuration

Add to your MCP client config:

```json
{
  "mcpServers": {
    "dispatch": {
      "command": "dispatch",
      "args": ["serve"]
    }
  }
}
```

---

## Error Handling

### MCP Server Errors

When an MCP server fails, the task produces an `EXIT` signal with the error as output. This is normal operation — the LLM sees the error and decides what to do (retry, try a different server, report to user).

```
[14:02:05] PID 1004 EXIT    Error: repository not found (404)
```

### dispatch Errors

`dispatch` itself must not crash. The error boundaries:

| Failure | Handling |
|---------|----------|
| dmcp not found on PATH | Fail at startup with clear error message |
| MCP server spawn fails | `EXIT` signal with spawn error |
| MCP server crashes mid-task | `EXIT` signal with crash output |
| Task panics (Rust) | Caught by `tokio::spawn`, converted to `EXIT` signal |
| Signal channel closed | Should never happen (dispatch owns both ends). If it does, log and restart channel. |
| Client disconnects | Clean up all running tasks, shut down gracefully |

`dispatch` uses Rust's type system and error handling to prevent panics. All fallible operations use `Result`, and `tokio::spawn` tasks are wrapped to catch unexpected failures. The signal channel (`tokio::sync::mpsc`) is the backbone — if it works, dispatch works.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Rust |
| Async runtime | Tokio |
| Concurrency | `tokio::spawn` per task |
| Signal queue | `tokio::sync::mpsc` channel |
| Process management | `tokio::process::Command` for MCP server child processes |
| MCP protocol | stdio (JSON-RPC over stdin/stdout) |
| Server discovery | Delegates to `dmcp` |

---

## Project Structure

```
src/
├── main.rs          # CLI entry point (dispatch serve, dispatch status)
├── lib.rs           # Library root
├── orchestrator.rs  # Core event loop: receive dispatch, spawn tasks, route signals
├── task.rs          # Task struct, state machine (Init → Running → Exit/Killed)
├── signal.rs        # Signal types, signal queue, window formatting
├── pid.rs           # PID assignment and tracking
├── reminder.rs      # Timer-based reminder system
├── mcp_client.rs    # Client for calling dmcp / MCP servers
├── mcp_server.rs    # MCP server interface (dispatch serve)
└── error.rs         # Error types and handling
```

---

## What dispatch Is NOT

- **Not a multi-agent system** — there is one LLM, not many. dispatch multiplexes a single decision maker across concurrent tasks.
- **Not an LLM wrapper** — dispatch does not load, configure, or prompt any LLM. The LLM is the client application's responsibility.
- **Not a replacement for dmcp** — dmcp manages MCP servers (install, discover, invoke). dispatch orchestrates concurrent calls to them. They are complementary.
- **Not a persistent service** — dispatch runs for the duration of a session. When the client disconnects, dispatch cleans up and exits. No daemon, no database, no state between sessions.

---

## Example Session

User asks their LLM assistant: *"Update my repo, check if there are any open issues, and find documentation about the new API."*

```
[14:02:00] ← LLM dispatches 3 tasks

[14:02:00] PID 1 INIT    git pull origin main
[14:02:00] PID 2 INIT    github issues list --state open
[14:02:00] PID 3 INIT    dmcp browse keywords=["api", "documentation"]

[14:02:01] PID 3 EXIT    Found: openapi-mcp (validate, generate, serve)
[14:02:02] PID 1 EXIT    Updated. 3 files changed, 14 insertions.
[14:02:03] PID 2 EXIT    4 open issues: #12 "Fix auth", #15 "Add tests", ...

[14:02:03] → dispatch wakes LLM with signal window

[14:02:04] ← LLM dispatches 1 follow-up task

[14:02:04] PID 4 INIT    openapi-mcp generate --spec ./api/v2.yaml

[14:02:06] PID 4 EXIT    Generated API docs at ./docs/api-v2.html

[14:02:06] → dispatch wakes LLM with signal window

[14:02:06] ← LLM responds:
           "Done. Repo is updated (3 files changed). You have 4 open issues —
            #12 and #15 look actionable. I also generated the API v2 docs
            at ./docs/api-v2.html."
```

Total LLM invocations: 3 (initial dispatch, follow-up dispatch, final response).
Total wall time: ~6 seconds.
Tasks that ran in parallel: 3.

---

## References

- [dmcp — MCP server manager](https://github.com/YakupAtahanov/dmcp)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Tokio — async runtime for Rust](https://tokio.rs/)
