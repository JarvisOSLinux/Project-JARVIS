# dispatch: Timer Tool Spec

**For:** The dispatch implementation (Rust/Tokio).
**Context:** JARVIS (the Python client) needs dispatch to support goal deferral — the ability to set a timer that fires a REMIND signal after a duration, without invoking any MCP server.

This document describes what dispatch needs to add. JARVIS will handle goal state, context scoping, and LLM prompt construction on its side once this is implemented.

---

## What's Needed

A built-in `timer` tool inside dispatch. No MCP server involved — just `tokio::time::sleep(duration)` then fire a REMIND signal.

### Why

JARVIS tracks user goals (e.g., "update dependencies"). When the LLM decides to defer a goal ("handle this later"), JARVIS needs a way to get woken up after N seconds. Dispatch already has the remind_after mechanism on tasks, but those tasks invoke MCP servers. A timer is a task that does nothing except wait and remind.

Use cases:
- Goal deferral: "Remind me about this in 30 minutes"
- Scheduled re-check: "Check back on this build in 5 minutes"
- Forgotten goal detection: Timer fires, LLM sees time metadata, decides to notify user or archive

---

## Spec

### 1. Timer Tool

Dispatch should expose a `timer` tool alongside its existing `dispatch`, `kill`, `wait`, `status`, and `log` tools.

**Tool name:** `timer`

**Parameters:**

```json
{
  "label": "string (required) — human-readable label for the signal window",
  "duration": "int (required) — seconds until REMIND fires",
  "metadata": "object (optional) — arbitrary key-value data passed through to the signal"
}
```

**Example call from client:**

```json
{
  "tool": "timer",
  "params": {
    "label": "goal_reminder:a1b2c3d4",
    "duration": 1800,
    "metadata": {
      "goal_id": "a1b2c3d4",
      "type": "goal_defer"
    }
  }
}
```

### 2. Behavior

1. Dispatch assigns a PID to the timer (same PID system as regular tasks).
2. Dispatch pushes an INIT signal immediately:
   ```
   [14:30:00] PID 42 INIT timer "goal_reminder:a1b2c3d4" (1800s)
   ```
3. Dispatch starts a `tokio::time::sleep(Duration::from_secs(duration))`.
4. When the timer expires, dispatch pushes a REMIND signal:
   ```
   [15:00:00] PID 42 REMIND timer "goal_reminder:a1b2c3d4" — 1800s elapsed
   ```
   The REMIND signal payload should include:
   ```json
   {
     "pid": 42,
     "type": "REMIND",
     "label": "goal_reminder:a1b2c3d4",
     "metadata": {
       "goal_id": "a1b2c3d4",
       "type": "goal_defer"
     },
     "elapsed": 1800
   }
   ```
5. After firing REMIND, the timer task is done. It pushes an EXIT signal:
   ```
   [15:00:00] PID 42 EXIT timer completed
   ```
   The timer does NOT repeat automatically. If the client wants another timer, it dispatches a new one.

### 3. Killable

Timers must be killable via the existing `kill` tool, just like any other task.

```json
{"tool": "kill", "params": {"pids": [42]}}
```

This cancels the sleep. The timer pushes a KILL signal and exits:
```
[14:45:00] PID 42 KILL timer cancelled
```

Use case: User says "never mind about that" — JARVIS kills the timer PID.

### 4. Visible in Status

The `status` tool should list active timers alongside active MCP tasks:

```
Active tasks:
  PID 1  — git.clone (running 12s)
  PID 2  — browser.search (running 8s)
  PID 42 — timer "goal_reminder:a1b2c3d4" (fires in 1788s)
```

Timers should show remaining time, not elapsed time, since the only interesting question about a timer is "when does it fire?"

### 5. Visible in Signal Window

Timer INIT, REMIND, EXIT, and KILL signals appear in the signal window (`log` tool) exactly like regular task signals. No special treatment. The label field distinguishes them from MCP task signals.

---

## What Dispatch Does NOT Need to Know

- **Goals.** Dispatch has no concept of goals. The `metadata.goal_id` is opaque data — dispatch stores it and passes it through in signals. It never interprets it.
- **Deferral count or history.** JARVIS tracks how many times a goal has been deferred. Dispatch just runs timers.
- **What to do when a timer fires.** Dispatch fires the signal. The client decides.
- **Goal archival or cancellation.** That's JARVIS-side logic.

Dispatch's job is simple: run a sleep, fire a signal, be killable, show up in status.

---

## Implementation Notes

### Suggested Rust approach

The timer is essentially the simplest possible task variant. In the existing task model, a regular task spawns an MCP server call. A timer task spawns a sleep:

```
// Pseudocode — adapt to your actual task model

enum TaskKind {
    McpCall { server: String, tool: String, params: Value },
    Timer { label: String, duration: u64, metadata: Option<Value> },
}

// In orchestrator, when spawning:
match task.kind {
    TaskKind::McpCall { .. } => {
        // existing MCP invocation code
    }
    TaskKind::Timer { duration, .. } => {
        tokio::time::sleep(Duration::from_secs(duration)).await;
        signal_tx.send(Signal::remind(pid, &task)).await;
    }
}
```

The `tokio::select!` pattern for kill support:

```
tokio::select! {
    _ = tokio::time::sleep(Duration::from_secs(duration)) => {
        // Timer expired — fire REMIND then EXIT
    }
    _ = kill_rx.recv() => {
        // Killed — fire KILL signal
    }
}
```

### What goes through the dispatch tool vs the timer tool

The client (JARVIS) calls **either**:
- `dispatch` tool — for MCP task batches (existing behavior, unchanged)
- `timer` tool — for a single timer (new)

They can be mixed in the same session. A timer PID and an MCP task PID coexist in the same PID space and signal window.

### Alternative: timer as a dispatch task variant

Instead of a separate `timer` tool, timers could be dispatched through the existing `dispatch` tool with a special server name:

```json
{
  "tool": "dispatch",
  "params": {
    "tasks": [
      {
        "server": "__timer__",
        "tool": "wait",
        "params": {"label": "goal_reminder:a1b2", "duration": 1800, "metadata": {...}}
      }
    ]
  }
}
```

This avoids adding a new top-level tool but feels a bit hacky. Either approach works — pick whichever fits the codebase better. The separate `timer` tool is cleaner; the `__timer__` variant is less surface area.

---

## Signal Format Reference

For consistency, here are all the signals a timer can produce:

| Signal | When | Payload |
|--------|------|---------|
| INIT | Timer created | `{pid, type: "INIT", label, metadata, duration}` |
| REMIND | Timer expired | `{pid, type: "REMIND", label, metadata, elapsed}` |
| EXIT | After REMIND fires | `{pid, type: "EXIT", label, output: "timer completed"}` |
| KILL | Timer cancelled | `{pid, type: "KILL", label}` |

---

## Testing

Suggested test cases:

1. **Basic timer:** Create timer with 2s duration. Verify INIT signal immediately. Verify REMIND signal after ~2s. Verify EXIT signal after REMIND.
2. **Kill timer:** Create timer with 60s duration. Kill it after 1s. Verify KILL signal. Verify no REMIND fires.
3. **Multiple timers:** Create 3 timers with different durations. Verify they fire independently in correct order.
4. **Timer + MCP tasks:** Create a timer and an MCP task simultaneously. Verify both get PIDs, both appear in status, both show in signal window.
5. **Status display:** Create a timer. Call status. Verify timer shows remaining time.
6. **Metadata passthrough:** Create timer with metadata. Verify metadata appears unchanged in REMIND signal.

---

## Summary

| What | Details |
|------|---------|
| New tool | `timer` (or `__timer__` dispatch variant) |
| Parameters | `label`, `duration` (seconds), `metadata` (optional, opaque) |
| Signals produced | INIT, REMIND, EXIT, KILL |
| Killable | Yes, via existing `kill` tool |
| Visible in status | Yes, shows remaining time |
| MCP server needed | No — pure tokio::time::sleep |
| Repeating | No — one-shot. Client dispatches new timer if needed |
