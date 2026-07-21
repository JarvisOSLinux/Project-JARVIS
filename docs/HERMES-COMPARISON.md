# JARVIS vs. Hermes Agent — Comparative Analysis

**Last updated:** 2026-07-21
**Subject:** Nous Research **Hermes Agent** (`NousResearch/hermes-agent`, MIT) vs. **JARVIS OS**.
**Method:** Findings below were gathered from Hermes's primary sources (repo README, `website/docs/`, and the `optional-mcps/` manifests) and adversarially verified. Claims that could **not** be confirmed from a primary source are quarantined in [§ Unverified / corrected claims](#unverified--corrected-claims) rather than stated as fact.

---

## TL;DR

- **JARVIS is architecturally more ambitious.** It is a local-first *AI-native operating system* — dual-scope MCP management (`dmcp`), signal-driven parallel orchestration (`dispatch`), vector memory (`contextor`), a community-vetted catalog (`mcp-registry`), and a security-research core (the six-threat taxonomy, TLA/PolicyKit sudo gating, and context saturation treated as a discrete threat).
- **Hermes decisively wins on out-of-box breadth.** ~73 built-in tools versus JARVIS's ~8 installable capability servers, plus a slicker one-click install flow.
- **The popular "Hermes MCP servers" are mostly its *built-in tools*, not its catalog.** Hermes's curated catalog (`optional-mcps/`) contains **exactly four** servers (blender, linear, n8n, unreal-engine), disabled by default.
- **JARVIS's local-first constraint is a latent advantage.** Hermes's TTS / image-gen / vision route through the cloud (Nous Portal). JARVIS can deliver equivalents **on-device** (Piper, ComfyUI, Ollama vision) and be *strictly better* on privacy.
- **Action:** close the breadth gap by vetting established upstream MCP servers into `mcp-registry` (see the capability-gap epic), leading with the local-media and interactive-OS surfaces that play to the thesis.

---

## What each one is

**Hermes Agent** — Nous Research's open-source (MIT) agent, billed as *"the agent that grows with you"* with a *"built-in learning loop"* (autonomous skill creation, self-improving skills, memory nudges, cross-session recall). It runs as a terminal app plus a gateway process. It is deliberately **model/provider-agnostic** (40+ backends — Anthropic, OpenAI, Google, xAI, DeepSeek, Qwen, self-hosted Ollama/vLLM/LM Studio, …; switch with `hermes model`). The *recommended* path is the **Nous Portal** cloud subscription (300+ models + a Tool Gateway for web search, image-gen, TTS, cloud browser), but it is never mandatory — you can bring your own keys per-tool. Despite the name, the agent framework is separate from the "Hermes" LLM family.

**JARVIS OS** — an Arch-based, KDE Plasma 6 / Wayland, **AI-native operating system** built as a WSU research project. Local Ollama inference, **no cloud dependency by default**. Capability is delivered as an ecosystem of specialized components: `dmcp` (MCP manager/client), `dispatch` (parallel orchestrator), `contextor` (vector memory), `mcp-registry` (catalog), and the Project-JARVIS daemon. Its academic contribution is the *platform + a six-threat taxonomy + mitigations*, with the thesis that traditional OS security models are inadequate for probabilistic AI agents.

---

## Side-by-side

| Axis | Hermes Agent | JARVIS OS |
|---|---|---|
| **Shape** | Single-process CLI + gateway; many entry points (CLI, gateway, ACP, API, library) | Distributed OS-native ecosystem (dmcp / dispatch / contextor / registry / daemon) |
| **Model posture** | Provider-agnostic (40+ backends); **recommended path is cloud Nous Portal**; local possible via Ollama/vLLM/LM Studio | **Local-first by thesis** — Ollama on-device, no cloud round-trips by default |
| **MCP client** | Auto-discovers from `~/.hermes/config.yaml`; tools namespaced `mcp_<server>_<tool>`; hot-reload on `tools/list_changed`; stdio + HTTP; per-server include/exclude | `dmcp` — **dual-scope** (user `~/.local` vs system `/usr` via pkexec); semantic vector search over registry embeddings; SHA-256 integrity; **PolicyKit-gated** elevation |
| **Curated catalog / trust** | `optional-mcps/` — **exactly 4** servers (blender, linear, n8n, unreal-engine), off by default; human PR-review vetting; manifests pin git commits | `mcp-registry` — community-vetted, AUR-style; `registry.json` with integrity hashes, `trustStatus` (community/official + deprecated/removed), embedding vectors; ~8 real capability servers today |
| **Built-in tool breadth** | **~73 tools** (README says "40+"): web/12-tool browser suite, vision, image-gen, TTS, video, files, terminal (6 backends), memory, kanban, Home Assistant, Spotify, Discord, `computer_use`, … | Capability comes from **installable MCP servers**, not a monolith; orchestration/memory primitives live in dispatch + contextor |
| **Memory / context** | Bounded agent-curated Markdown (`MEMORY.md` ~800 tok, `USER.md` ~500 tok) as a frozen prompt snapshot + SQLite FTS5 `session_search`; **default context strategy is lossy summarization** (lossless DAG is a *pluggable community plugin*, not built-in) | `contextor` — purpose-built SQLite **vector** store (cosine similarity, sessions w/ rolling summaries, 13-command protocol); context saturation elevated to **Threat 6: Bloated Context** |
| **Privacy** | Documented **eight-layer** defense-in-depth; but privacy *from providers* depends on backend — cloud path sends data off-machine | **Structural** — inference is local, no cloud by default; open-source treated as a necessity; security is the research core |
| **Terminal / execution** | **Six backends**: local, Docker, SSH, Singularity, Modal, Daytona (Modal/Daytona = serverless hibernation) | Native OS execution; dual-scope shell (user + system/root via PolicyKit); dispatch runs tasks in parallel with REMIND/wait/kill |

---

## Where Hermes leads (today)

1. **Turnkey breadth.** ~73 built-in tools across ~28 toolsets — a working browser suite, vision, image-gen, TTS, kanban, Home Assistant, Spotify, and messaging — all available on install with zero server setup.
2. **Install polish.** One-line install and a one-command interactive picker for its (small) catalog.
3. **Provider flexibility.** 40+ model backends switchable with one command; 6 execution backends including serverless persistence.
4. **A learning loop.** Autonomous skill creation + `agentskills.io` skills, memory nudges, FTS5 cross-session recall, Honcho-style user modeling.
5. **Mature docs and a broad messaging-gateway surface.**

## Where JARVIS leads

1. **It's an OS, not an app on top of one.** Kernel-level `/dev/jarvis` drivers, sudo-capable autonomous tool creation, dual-scope system/user MCP management.
2. **Local-first as a guarantee, not an option.** No cloud round-trips by default — the privacy posture Hermes can only *approximate* when configured for self-hosting.
3. **A real trust model.** `mcp-registry` + `dmcp` enforce integrity hashes, `trustStatus` tiers, revocation, and clone-at-pinned-commit — a genuine supply-chain mechanism, not just PR review.
4. **Privilege as a first-class, researched concern.** The TLA confirmation gate + PolicyKit enforcement (threats 4 & 5) is a designed, auditable escalation path; Hermes uses `sudo -A` + askpass.
5. **A novel academic contribution.** The six-threat taxonomy (incl. *Bloated Context* as a discrete security threat) + platform + mitigations — none of which Hermes claims.

---

## The capability gap → what to build

Hermes's advantage is **surface area**, and nearly all of it wraps an established open-source upstream — so the gap closes primarily through **vetting**, not net-new engineering. The registry currently covers the *developer core* (shell, filesystem, git, GitHub, fetch, Brave search, SQLite, time) but has **none of the interactive or personal-life surfaces**. Highest-leverage additions (tracked as the capability-gap epic + child issues in `mcp-registry`):

- **Privacy-advantage plays (beat Hermes, don't just match):** Piper TTS, ComfyUI image-gen, Ollama vision — all on-device where Hermes goes cloud.
- **Interactive OS surfaces (highest thesis-fit):** Playwright browser automation, `computer-use-linux` (Wayland/KDE control), Home Assistant. *Note:* these expand exactly the attack surface the six-threat taxonomy studies — each should be tiered against the trust model and gated by TLA where privileged.
- **Personal productivity + data:** Obsidian, email (IMAP/SMTP), CalDAV calendar, Postgres, n8n (exact catalog parity), Slack, Blender (exact catalog parity), sequential-thinking, KG-memory (complements contextor).

---

## Unverified / corrected claims

Recorded for honesty — these did **not** survive verification and are **not** relied on above:

- **"Lossless / DAG context" is not a built-in Hermes feature.** It's a *pluggable* context-engine plugin interface; the default engine is lossy summarization. Community DAG "LCM" plugins exist but are third-party (not Nous), and the associated paper attribution is unconfirmed.
- **The catalog is 4 servers, not a large ecosystem.** "GitHub as a catalog entry" was refuted (GitHub is a *built-in* toolset, not a catalog MCP).
- **Version attributions** (e.g. MCP client ≈ v0.2.0, `hermes mcp serve` ≈ v0.6.0) and a possible 7th terminal backend ("Vercel Sandbox") could not be pinned to primary release notes.
- **Star/fork counts** surfaced during research are unreliable summarizer output and are not cited.
- The hosted docs site (`hermes-agent.nousresearch.com/docs`) is bot-protected (HTTP 403); identical content was verified via `raw.githubusercontent.com`.

---

## Sources

- Repo: <https://github.com/NousResearch/hermes-agent> · README, `website/docs/`, `optional-mcps/`
- Providers: `website/docs/integrations/providers.md`
- Security (8-layer): `website/docs/user-guide/security.md`
- Memory: `website/docs/user-guide/features/memory.md`
- Architecture: `website/docs/developer-guide/architecture.md`
- Context-engine plugins: `website/docs/developer-guide/context-engine-plugin.md`
- Tools reference (~73 tools): `website/docs/reference/tools-reference.md`
- Curated catalog manifests: `optional-mcps/{blender,linear,n8n,unreal-engine}/manifest.yaml`
