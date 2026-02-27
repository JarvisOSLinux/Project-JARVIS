# Multicommand — Single Agent Multitasking System

## Core Idea

A single LLM instance acts as a decision maker. It dispatches multiple MCP server tool calls simultaneously, then goes idle. The orchestration layer runs those tasks concurrently in the background and only wakes the LLM when a signal arrives — a task completes, fails, or needs attention.

This achieves multi-agent-level parallelism without loading multiple LLM instances, keeping RAM usage flat and power consumption low.

---

## How It Works

### 1. LLM Dispatch
The LLM receives a user request and responds with a structured JSON list of tasks to initiate:

```json
{
  "tasks": [
    { "server": "git", "tool": "pull", "params": { "url": "https://github.com/..." } },
    { "server": "browser", "tool": "search", "params": { "query": "Rust async MCP" } },
    { "server": "vscode", "tool": "open_file", "params": { "path": "./src/main.rs" } }
  ]
}
```

After dispatching, the LLM goes idle. It does not poll, it does not wait — it simply sleeps until the orchestrator sends it a signal.

### 2. Orchestrator
The orchestrator is the always-alive event loop. It:
- Receives the JSON dispatch from the LLM
- Spawns each task as a concurrent async process (via Tokio's `tokio::spawn`)
- Assigns each task a **PID** for tracking
- Listens on an **mpsc channel** for incoming signals from tasks
- Decides what to do when a signal arrives — notify the LLM, kill a process, or log silently

The orchestrator never loads an LLM. It is pure coordination logic.

### 3. Tasks (MCP Servers)
Each task runs independently inside the Tokio async runtime. When a task finishes — success or failure — it pushes a signal into the shared channel:

```
tx.send(Signal { pid: 1002, status: EXIT, output: "search results..." })
```

Tasks do not know about each other. They only know their PID and where to send their signal.

### 4. Signal Schema
Every event in the system is represented as a structured log entry:

```
[TIME]: [PID] [INIT]  git pull <url>
[TIME]: [PID] [EXIT]  <output, regardless of success or failure>
[TIME]: [PID] [WAIT]  <reminder received, LLM commanded wait>
[TIME]: [PID] [KILL]  <process killed, no output>
```

| Signal | Meaning |
|--------|---------|
| `INIT` | Task started by LLM via orchestrator |
| `EXIT` | Task finished (success or failure), output sent to LLM |
| `WAIT` | LLM was reminded, decided to let task keep running |
| `KILL` | LLM killed the process by PID, no output returned |

### 5. LLM Wakeup
The orchestrator wakes the LLM only when there is something meaningful to reason about — a completed task, a failure, or a reminder threshold being hit. The LLM receives a log snapshot of the last 20 task events as context, reconstructs the current state, and decides next steps.

---

## Why PID Matters
Each task has a unique PID (OS-level or custom-assigned). This allows the LLM to:
- Reference specific tasks unambiguously in its decisions
- Issue a `KILL` command targeting exactly one process
- Track which outputs belong to which task when multiple complete around the same time

---

## Context Management
Rather than maintaining a growing conversation history, the LLM works from a **rolling 20-entry log window**. This keeps context size bounded and predictable regardless of how many tasks have run.

Output verbosity is delegated to MCP server providers — a well-implemented MCP server returns concise structured output, not raw data dumps. The log stores a summary or pointer to full output if needed.

---

## Key Properties

- **Single LLM instance** — RAM usage is flat no matter how many tasks run
- **True parallelism** — tasks run concurrently via Tokio async runtime
- **Event-driven** — LLM only consumes compute when it needs to reason
- **Model-agnostic** — any LLM with JSON structured output support works
- **Server-agnostic** — works with any MCP-compatible server
- **Predictable resource usage** — no idle agents holding memory

---

## System Prompt Responsibility
Multicommand does not enforce a specific LLM or prompt. Instead, it ships a **sample system prompt context** that developers add to their LLM setup. This defines:
- Available MCP servers and their tools
- Expected JSON dispatch format
- Signal types and how to interpret them
- PID-based process management instructions

---

## What Multicommand Is NOT
- It is not a multi-agent system — there is one LLM, not many
- It is not a wrapper around an existing orchestration framework
- It does not manage LLM configuration — that is the developer's responsibility
- It does not store persistent memory — the log window is the working state

---

## Tech Stack (Planned)
- **Language**: Rust
- **Async Runtime**: Tokio
- **Concurrency**: `tokio::spawn` + `tokio::sync::mpsc` channels
- **Process Management**: `tokio::process::Command` for MCP server child processes
- **Signal Queue**: mpsc channel shared across all spawned tasks
- **Interface**: Exposes itself as an MCP server so any LLM with MCP support can connect
