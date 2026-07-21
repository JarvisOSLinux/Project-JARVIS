# Project-JARVIS vs. Hermes Agent — Comparative Analysis

**Last updated:** 2026-07-21
**Subject:** Nous Research **Hermes Agent** (`NousResearch/hermes-agent`, MIT) vs. **Project-JARVIS** — the core AI agent (`pip install project-jarvis`), *not* the JarvisOS distribution. Both are model-agnostic agent cores; JarvisOS is merely one place Project-JARVIS is embodied and is out of scope here.
**Method:** Hermes facts are from its primary sources (repo README, `website/docs/`, `optional-mcps/` manifests), adversarially verified; claims that couldn't be confirmed are quarantined in [§ Unverified](#unverified--corrected-claims). Project-JARVIS facts are from this repository + `dmcp`/`dispatch`/`contextor`.

> **Changelog:** the first draft of this doc mistakenly compared *JarvisOS (the distro)* to Hermes and stated Project-JARVIS was "local-only by thesis." That was wrong. Project-JARVIS's core is **model-agnostic and does not enforce local-only execution** — corrected throughout.

---

## TL;DR

- **Both are model-agnostic agent cores.** Neither is locked to one model. Project-JARVIS's edge is an **automatic provider pool** that *recycles to the next API on failure* (rate-limit / quota / 5xx cooldowns with auto-restore); Hermes switches models **manually** (`hermes model`). Project-JARVIS does **not** enforce local-only usage.
- **Both are pip-/one-line installable.** Project-JARVIS is published on **PyPI** (`pip install project-jarvis`, versioned); Hermes has a one-line installer.
- **Project-JARVIS wins on context economy and extensibility.** `dmcp` uses **semantic (embedding) search** to surface only the servers relevant to a task — it never preloads unused MCP servers into context. And `sources.list` lets a user opt into **any** community/custom registry, not one fixed catalog.
- **Hermes wins decisively on out-of-box breadth.** ~73 built-in tools vs. Project-JARVIS's ~8 installable capability servers. (Its *curated catalog* is only 4 servers — the popular "Hermes MCP servers" are mostly its built-ins.)
- **Action:** close the breadth gap by vetting established upstream MCP servers into `mcp-registry` (see the capability-gap epic), while leaning on the two structural advantages above.

---

## What each one is

**Hermes Agent** — Nous Research's open-source (MIT) agent, *"the agent that grows with you,"* with a *"built-in learning loop"* (autonomous skill creation, self-improving skills, memory nudges, cross-session recall). Runs as a terminal app + gateway. Model-agnostic across 40+ backends, switchable with `hermes model`; the *recommended* path is the cloud **Nous Portal** (300+ models + a Tool Gateway for web/image/TTS/browser), though you can bring your own keys.

**Project-JARVIS** — a model-agnostic AI agent core: a Python daemon plus specialized components — `dmcp` (dual-scope MCP manager/client), `dispatch` (signal-driven parallel orchestrator), `contextor` (SQLite vector memory) — coordinating tools discovered from a **community-vetted registry** with a real supply-chain trust model. Installed with `pip install project-jarvis`. It is the flagship agent of JarvisOS but runs as an ordinary package on any host. Its research contribution is a **six-threat taxonomy** for privileged LLM agents (incl. *Bloated Context* as a discrete threat) plus mitigations (Cryptographic Boundary Protocol, TLA + PolicyKit sudo gating, dispatch rolling window + contextor pruning).

---

## Side-by-side

| Axis | Hermes Agent | Project-JARVIS (core) |
|---|---|---|
| **Install** | One-line installer | **PyPI** — `pip install project-jarvis`, versioned/taggable |
| **Model posture** | Model-agnostic (40+ backends); switch **manually** via `hermes model`; recommended path is cloud Nous Portal | Model-agnostic; **automatic provider pool** — on failure it *recycles to the next provider/API* (429→60 s, 402→1 h, 5xx/timeout→30 s cooldowns, auto-restore). Does **not** enforce local-only |
| **MCP client** | Auto-discovers and **registers all configured servers at startup**; tools namespaced `mcp_<server>_<tool>`; hot-reload; stdio+HTTP; static per-server include/exclude filtering | `dmcp` — **dual-scope** (user `~/.local` vs system `/usr` via pkexec); **semantic vector search** over server/tool embeddings surfaces only relevant servers *on demand*; SHA-256 integrity; PolicyKit-gated elevation |
| **Context economy** | All configured servers' tool defs enter context at startup (config-time filtering to trim) | **Dynamic retrieval** — unused servers never load, so tool metadata doesn't bloat the window. Context saturation is a named threat (**Threat 6: Bloated Context**) |
| **Registry / catalog** | Single in-repo catalog (`optional-mcps/`, **4 servers**, off by default); human PR-review vetting; git-commit-pinned manifests | `mcp-registry` (community-vetted, integrity hashes, `trustStatus` tiers + revocation, embeddings). **Federated:** `sources.list` lets the user opt into *any* community/custom registry (user+system scope) — not locked to one catalog |
| **Built-in tool breadth** | **~73 tools** (README says "40+"): web/12-tool browser suite, vision, image-gen, TTS, video, files, terminal (6 backends), memory, kanban, Home Assistant, Spotify, Discord, `computer_use`, … | Capability comes from **installable MCP servers** (~8 real today); orchestration/memory primitives live in dispatch + contextor |
| **Orchestration** | Subagents + `delegate_task`; single synchronous agent loop | `dispatch` — signal-driven *"one brain, many hands"* parallel execution (INIT/EXIT/**REMIND**/WAIT/KILL, `fire_wake`, `remind_after`); non-blocking long-running tasks with progress signals |
| **Memory / context** | Bounded agent-curated Markdown (`MEMORY.md` ~800 tok, `USER.md` ~500 tok) + SQLite FTS5 `session_search`; **default context strategy is lossy summarization** (lossless DAG is a *pluggable community plugin*, not built-in) | `contextor` — purpose-built SQLite **vector** store (cosine similarity, sessions w/ rolling summaries, 13-command protocol), embeddings via the daemon |
| **Privilege / sudo** | `sudo -A` + per-OS GUI askpass (ksshaskpass/osascript/UAC) auto-wrapping privileged commands | **TLA confirmation gate** (human-in-the-loop, logged) + **PolicyKit/pkexec** enforcement; dual-scope user/system servers. Privilege is a first-class researched concern (threats 4 & 5) |
| **Security model** | Documented **eight-layer** defense-in-depth (authorization, dangerous-command approval, file-write safety, container isolation, MCP env filtering, injection scanning, cross-session isolation, input sanitization) | **Six-threat taxonomy** + mitigations; Cryptographic Boundary Protocol wraps tool output so injected content can't spoof status |

---

## Where Project-JARVIS leads

1. **Automatic provider failover.** The provider pool *recycles to the next API on failure* and auto-restores cooled-down providers — resilience Hermes's manual `hermes model` switch doesn't offer. (Seen live: `gemma4` cooldown → auto-failover to `nemotron-3-ultra`.)
2. **Context economy by design.** Semantic search means unused servers never enter the window — Hermes registers all configured servers at startup. This is *Bloated Context* (Threat 6) mitigated architecturally, not bolted on.
3. **Federated, user-controlled registries.** `sources.list` opts into any community or custom registry with a real supply-chain trust model (integrity hashes, `trustStatus`, revocation, pinned clones) — vs. one in-repo catalog.
4. **Parallel orchestration as a first-class layer.** `dispatch` runs long tasks non-blocking with REMIND/wait/kill — proven end-to-end running a multi-minute `pacman -Syu` while streaming progress to the user.
5. **Privilege + security as the research core.** The TLA/PolicyKit gate and the six-threat taxonomy (incl. Bloated Context) are a novel academic contribution.

## Where Hermes leads

1. **Turnkey breadth.** ~73 built-in tools on install — a working browser suite, vision, image-gen, TTS, kanban, Home Assistant, Spotify, messaging — zero server setup.
2. **A learning loop.** Autonomous skill creation + `agentskills.io`, memory nudges, FTS5 cross-session recall, Honcho-style user modeling.
3. **Execution flexibility.** Six terminal backends (local, Docker, SSH, Singularity, Modal, Daytona) incl. serverless persistence; 20+ messaging gateways.
4. **Polish & docs maturity.**

---

## The capability gap → what to build

Hermes's advantage is **surface area**, and nearly all of it wraps an established open-source upstream — so the gap closes primarily through **vetting**, not net-new engineering. The registry covers the developer core (shell, filesystem, git, GitHub, fetch, Brave search, SQLite, time) but has **none of the interactive or personal-life surfaces**. Tracked as the capability-gap epic + child issues in `mcp-registry`:

- **On-device media (privacy edge — regardless of LLM choice, tool data stays local):** Piper TTS, ComfyUI image-gen, Ollama vision.
- **Interactive surfaces (highest agent-value, highest risk):** Playwright browser automation, `computer-use-linux` (Wayland/KDE control), Home Assistant. *Each expands exactly the attack surface the six-threat taxonomy studies — tier via the trust model, gate privileged ones via TLA.*
- **Personal productivity + data:** Obsidian, email (IMAP/SMTP), CalDAV, Postgres, n8n (Hermes catalog parity), Slack, Blender (parity), sequential-thinking, KG-memory.

> **On privacy, precisely:** because both cores are model-agnostic, privacy *from the LLM provider* is a deployment choice for either (run local models = private; use a cloud API = not). Project-JARVIS's distinct win is that its **on-device MCP servers** (Piper/ComfyUI/Ollama-vision) keep *tool-side* data local even when Hermes's equivalents route through the cloud Nous Portal.

---

## Unverified / corrected claims

Recorded for honesty — not relied on above:

- **Hermes "lossless / DAG context" is not built-in** — it's a *pluggable* context-engine plugin interface; the default engine is lossy summarization. Community DAG "LCM" plugins exist (third-party, not Nous); the associated paper attribution is unconfirmed.
- **Hermes's catalog is 4 servers, not a large ecosystem.** "GitHub as a catalog entry" was refuted (GitHub is a *built-in* toolset).
- **Version attributions** (MCP client ≈ v0.2.0, `hermes mcp serve` ≈ v0.6.0) and a possible 7th terminal backend couldn't be pinned to primary release notes.
- **Star/fork counts** surfaced during research are unreliable and not cited.
- The hosted docs site is bot-protected (HTTP 403); content verified via `raw.githubusercontent.com`.

---

## Sources

- Hermes repo: <https://github.com/NousResearch/hermes-agent> — README, `website/docs/{integrations/providers,user-guide/security,user-guide/features/memory,developer-guide/architecture,developer-guide/context-engine-plugin,reference/tools-reference}.md`, `optional-mcps/{blender,linear,n8n,unreal-engine}/manifest.yaml`
- Project-JARVIS: this repo (`pyproject.toml`, `jarvis/llm/provider_pool.py`), `dmcp` (`src/sources.rs`, `src/vector_index.rs`, `src/elevation.rs`), `dispatch`, `contextor`.
