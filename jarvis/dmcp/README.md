# dmcp

**MCP Manager** — a modular, system- and user-level manager for MCP (Model Context Protocol) servers.

## What it does

dmcp discovers, manages, and invokes MCP servers installed on your system. It works at two scopes:

- **User scope** — per-user, no root required (`~/.local/share/mcp/`, `~/.config/mcp/`)
- **System scope** — system-wide, visible to all users (`/usr/share/mcp/`, `/etc/mcp/`)

It supports both **local** (stdio) and **remote** (SSE, WebSocket) servers. Local servers are cloned and run from disk; remote servers are metadata-only, with connection endpoints stored in manifests.

## Features

- **Discovery** — List installed servers (user + system)
- **Registry** — Browse servers from configurable registry URLs
- **Install** — Install from registry (by ID) or from URL (manifest/endpoint)
- **Connect** — Add remote servers by URL (fetches manifest if valid JSON, else treats as raw endpoint)
- **Config** — Get and set per-server configuration (API keys, endpoints, etc.)
- **Invocation** — Spawn stdio servers; SSE/WebSocket: print connection URL
- **Setup** — Run setup scripts at install (dependencies, config) or via `dmcp setup <id>`

## Configuration

Paths are configurable via environment variables. Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

See [MCP-SYSTEM-SPEC.md](MCP-SYSTEM-SPEC.md) for the full specification and [MCP-REGISTRY-GUIDE.md](MCP-REGISTRY-GUIDE.md) for registry format and install flow.

## Build & Run

Requires [Rust](https://rustup.rs/).

```bash
cargo build --release
cargo install --path .   # Install to ~/.cargo/bin
```

## Commands

| Command | Description |
|---------|-------------|
| `dmcp list [--user] [--system] [--json]` | List installed MCP servers (default: both) |
| `dmcp info <id> [--json]` | Show detailed info for a server |
| `dmcp config <id> get [key] [--json]` | Get config value(s) |
| `dmcp config <id> set <key> <value>` | Set a config value (uses pkexec for system scope) |
| `dmcp sources list [--user] [--system]` | List registry source URLs |
| `dmcp sources add <url> [--system]` | Add a registry source (default: user) |
| `dmcp sources remove <url> [--system]` | Remove a registry source |
| `dmcp browse [url] [--user] [--system] [--json]` | Browse servers in registries (or from specific URL) |
| `dmcp install <id or url> [--system] [--no-setup]` | Install from registry (by ID) or from manifest/endpoint URL |
| `dmcp uninstall <id>` | Remove installed server |
| `dmcp run <id> [--verbose]` | Run server (stdio: spawn; SSE/WebSocket: print URL) |
| `dmcp tools <id> [--json]` | List tools on a server |
| `dmcp call <id> <tool> [--args JSON]` | Call a tool on a server |
| `dmcp serve` | Run dmcp as MCP server (for LLM integration) |
| `dmcp setup <id>` | Run setup script for an installed server |
| `dmcp connect <url> [--id] [--name] [--summary] [--version] [-c key=value...] [--system] [--no-setup]` | Connect to remote server |
| `dmcp paths` | Show resolved paths (debug) |

## Project Structure

```
src/
├── main.rs      # CLI entry point
├── lib.rs       # Library root
├── paths.rs     # Path resolution (env, XDG)
├── discovery.rs # List servers, get_server, load index/manifests
├── sources.rs   # Registry sources (sources.list)
├── config.rs    # Config get/set
├── install.rs   # Install, uninstall
├── run.rs       # Run servers (stdio spawn, SSE/WS URL)
├── setup.rs     # Setup script execution

├── browse.rs    # Browse registry servers
├── connect.rs   # Connect to remote by URL (manifest or raw)
├── elevation.rs # pkexec for system scope
└── models.rs    # Index, Manifest, Transport structs
```

## Connect

`dmcp connect` supports two modes:

1. **Manifest URL** — Fetches the URL as JSON. If valid (has `id` and `transports`), uses it and applies overrides.
2. **Raw fallback** — If fetch fails, treats URL as a raw SSE/WebSocket endpoint and auto-generates metadata.

## Status

Core features implemented: list, info, config, sources, browse, install, uninstall, connect, run, setup.

## LLM Integration

Run dmcp as an MCP server so LLMs (Cursor, Claude, etc.) can control it:

```bash
dmcp serve
```

Add to your MCP client config:

```json
{
  "mcpServers": {
    "dmcp": {
      "command": "dmcp",
      "args": ["serve"]
    }
  }
}
```

See [docs/LLM-INTEGRATION.md](docs/LLM-INTEGRATION.md) for details.

## References

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html)
