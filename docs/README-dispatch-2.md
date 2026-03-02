# dispatch

**Signal-driven task orchestrator for MCP servers.**

One LLM dispatches multiple MCP tool calls concurrently, then goes idle. `dispatch` runs those tasks in parallel and only wakes the LLM when a signal arrives — a task completes, fails, or needs attention.

Multi-agent-level parallelism without loading multiple LLM instances.

## Why

Most LLM orchestration systems either run tasks sequentially (slow) or spawn multiple LLM agents (expensive). `dispatch` takes a different approach: **one brain, many hands.** The LLM is the decision maker. MCP servers are the workers. `dispatch` is the nervous system — routing signals, tracking processes, and waking the brain only when there is something to reason about.

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

| Component | Role |
|-----------|------|
| **LLM** | Decision maker. Produces dispatch lists, interprets signals. |
| **dispatch** | Orchestrator. Spawns tasks, assigns PIDs, manages signal queue, fires reminders, exposes MCP interface. |
| **dmcp** | Server manager. Discovers installed MCP servers, handles install/config/invocation. Required on PATH. |
| **MCP servers** | Workers. Execute actual operations (git, file I/O, web search, etc). |

## Requirements

- [dmcp](https://github.com/YakupAtahanov/dmcp) installed and on PATH
- Rust toolchain (for building from source)

```bash
# Install dmcp first
cargo install --git https://github.com/YakupAtahanov/dmcp

# Build dispatch
cargo build --release
cargo install --path .
```

## Usage

```bash
dispatch serve    # Run as MCP server (stdio)
dispatch help     # Show help
```

Add to your MCP client config (Claude, Cursor, etc.):

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

## Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `dispatch` | Dispatch tasks for concurrent execution | `tasks: [{server, tool, params, remind_after?}]` |
| `kill` | Terminate running tasks by PID | `pids: [int]` |
| `wait` | Acknowledge reminder, keep tasks running | `pids: [int]` |
| `status` | Get current state of all active tasks | — |
| `log` | Get the signal window (last N entries) | `count?: int` (default: 20) |
| `timer` | Set a one-shot timer that fires a REMIND signal after a duration | `label: string`, `duration: int` (seconds), `metadata?: object` |

## Signals

Every event is a signal. The signal window (last 20 entries) is the LLM's working memory.

| Signal | Meaning | Triggered by |
|--------|---------|-------------|
| `INIT` | Task started | dispatch (on spawn) |
| `EXIT` | Task finished (success or failure) | Task completion |
| `REMIND` | Task/timer running beyond threshold | dispatch (timer or `remind_after`) |
| `WAIT` | LLM acknowledged reminder, continuing | LLM response |
| `KILL` | Task terminated | LLM response |

```
[14:02:01] PID 1 INIT    git pull origin main
[14:02:01] PID 2 INIT    browser search "Rust async patterns"
[14:02:02] PID 1 EXIT    Already up to date.
[14:02:05] PID 2 EXIT    Found 12 results: ...
```

## Timers

Timers are one-shot delays that fire a `REMIND` signal after a duration. No MCP server is involved — just `tokio::time::sleep`. Use timers for goal deferral, scheduled re-checks, or any timed reminder.

### Timer lifecycle

1. Client calls the `timer` tool with a label, duration, and optional metadata.
2. Dispatch assigns a PID and fires an `INIT` signal immediately.
3. After `duration` seconds, dispatch fires a `REMIND` signal (with metadata passed through), then an `EXIT` signal.
4. The timer does not repeat. To set another, dispatch a new timer.

Timers are killable via the `kill` tool and visible in `status` (showing remaining time).

### Timer signals

| Signal | When | Payload |
|--------|------|---------|
| `INIT` | Timer created | `{pid, type: "INIT", label, metadata, duration}` |
| `REMIND` | Timer expired | `{pid, type: "REMIND", label, metadata, elapsed}` |
| `EXIT` | After REMIND fires | `{pid, type: "EXIT", label, output: "timer completed"}` |
| `KILL` | Timer cancelled | `{pid, type: "KILL", label}` |

### Timer example

```
→ LLM defers a goal: "remind me about dependency updates in 30 minutes"

[14:30:00] PID 42 INIT    timer "goal_reminder:a1b2" (1800s)

→ 30 minutes later...

[15:00:00] PID 42 REMIND  timer "goal_reminder:a1b2" — 1800s elapsed
[15:00:00] PID 42 EXIT    timer completed

→ dispatch wakes LLM — LLM sees the REMIND signal with metadata and decides what to do
```

Metadata is opaque to dispatch — it stores and passes through whatever the client provides. This lets the client (e.g., JARVIS) track goal IDs, deferral types, or any context without dispatch needing to understand it.

## Example Session

User: *"Update my repo, check open issues, and find API docs."*

```
[14:02:00] PID 1 INIT    git pull origin main
[14:02:00] PID 2 INIT    github issues list --state open
[14:02:00] PID 3 INIT    dmcp browse keywords=["api", "documentation"]

[14:02:01] PID 3 EXIT    Found: openapi-mcp (validate, generate, serve)
[14:02:02] PID 1 EXIT    Updated. 3 files changed.
[14:02:03] PID 2 EXIT    4 open issues: #12 "Fix auth", #15 "Add tests", ...

→ dispatch wakes LLM — LLM dispatches follow-up:

[14:02:04] PID 4 INIT    openapi-mcp generate --spec ./api/v2.yaml
[14:02:06] PID 4 EXIT    Generated docs at ./docs/api-v2.html

→ LLM responds to user with summary
```

Total LLM invocations: 3. Wall time: ~6 seconds. Parallel tasks: 3.

## Project Structure

```
src/
├── main.rs          # CLI entry point (dispatch serve)
├── lib.rs           # Library root
├── orchestrator.rs  # Core event loop: spawn tasks, route signals, block until event
├── task.rs          # Task struct, state machine (Running → Exited/Killed)
├── signal.rs        # Signal types, rolling signal window
├── pid.rs           # PID assignment and tracking
├── reminder.rs      # Timer-based reminder system
├── mcp_client.rs    # Client for calling dmcp
├── mcp_server.rs    # MCP server interface (JSON-RPC 2.0 over stdio)
└── error.rs         # Error types
```

## References

- [dmcp — MCP server manager](https://github.com/YakupAtahanov/dmcp)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Tokio — async runtime for Rust](https://tokio.rs/)
