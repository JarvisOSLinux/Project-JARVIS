# Research

## Security Threats in AI-Native Operating Systems: An Empirical Study Using Privilege-Escalated LLM Agents

**Yakup Atahanov, Toufic Majdalani — Washington State University Everett**
**Faculty Advisor: Dr. Jeremy Thompson**

---

### Overview

As Large Language Models transition from conversational tools to autonomous agents with system-level control, a critical question emerges: what security threats arise when AI operates with elevated privileges — and how can they be mitigated?

JarvisOS was built to answer that question empirically. Rather than studying LLM security in isolation, we chose an operating system as our research environment because it represents the broadest possible integration surface — encompassing file management, process execution, network operations, package installation, and privilege escalation. If we can characterize and mitigate threats at the OS level, the findings generalize to every narrower integration context.

The result is both a fully functional AI-native operating system and a controlled security research testbed — purpose-built to study what actually happens when an LLM agent is given unrestricted access to a real computing environment.

---

### The Problem

Traditional OS security models assume deterministic software: a program does exactly what its code specifies. An LLM agent violates that assumption fundamentally. Its behavior is probabilistic, context-dependent, and shaped by natural language inputs that are difficult to sanitize or predict.

Existing research on LLM security focuses primarily on model-level attacks — jailbreaking, adversarial inputs, prompt injection in isolation. Empirical research on what happens when an LLM agent is placed in control of a real system, with real privileges, has remained scarce.

---

### What We Built

JarvisOS is an Arch Linux-based AI-native operating system built around a dynamic Model Context Protocol (MCP) orchestration layer. Its core action layer is implemented in three Rust packages:

- **dispatch** — a signal-driven parallel task orchestrator. Its design philosophy is *one brain, many hands*: a single LLM instance acts as the sole decision maker while multiple MCP servers execute operations concurrently as workers. The LLM dispatches tasks and immediately returns to conversation — it does not wait or poll. dispatch wakes the LLM only when a signal arrives: a task completing, a reminder threshold firing, or a user action.

- **dmcp** — the MCP server lifecycle manager. It handles discovery, installation, configuration, invocation, and removal of MCP servers at both user scope and system scope. dmcp also runs as an MCP server itself, exposing its capabilities as tools callable by the LLM. Tool discovery uses an adaptive search strategy: keyword search for small catalogs, embedding-based cosine similarity for larger ones — ensuring the LLM only loads the tools it actually needs.

- **contextor** — a persistent Rust memory backend providing vector similarity search over conversation history, rolling session summaries, and retention-based pruning to keep the LLM's working context bounded and relevant.

The system is built on a modular nine-script build pipeline that transforms a base Arch Linux ISO into a bootable AI-native OS with KDE Plasma 6 on Wayland.

---

### The Threat Taxonomy

Through designing, building, and operating JarvisOS, we empirically identified seven security threats that emerge when LLMs are granted elevated system privileges:

| Threat | Escalation Stage | Primary Mitigation |
|--------|-----------------|-------------------|
| Malicious MCP Servers | User / Sudo / Web | Community-vetted AUR-style registry |
| Prompt Injection | User / Sudo / Web | Cryptographic Boundary Protocol |
| Misleading MCP Server Usage | User / Sudo / Web | Registry vetting + structured tool schema |
| Unauthorized Sudo Requests via MCP | Sudo / Web | TLA system + PolicyKit enforcement |
| Sudo Capability Exploitation | Sudo / Web | TLA confirmation gate |
| Bloated Context | User / Sudo / Web | dispatch rolling window + contextor pruning |
| Forgetful Context | User / Sudo / Web | Not yet mitigated — persistent constraint register planned |

Each threat was observed through direct system operation. **Bloated Context** and **Forgetful Context** are distinct, adjacent threats: Bloated Context is security constraints getting crowded out of a saturated context window; Forgetful Context is the agent never durably storing a constraint in the first place, so a context refresh loses it structurally rather than incidentally. No prior literature identifies the latter as a discrete security threat.

---

### Architectural Mitigations

**Cryptographic Boundary Protocol**
When an MCP server task completes, dispatch wraps its output in boundary tags keyed by a 128-bit provenance nonce drawn from the OS CSPRNG, making boundary-tag forgery by injected tool output computationally negligible. The daemon verifies the tag against the trusted per-task nonce and marks any mismatch as untrusted. Output returned to the LLM is thus structurally marked as data, separating the instruction plane from the data plane; large payloads can additionally be deferred out-of-band (`defer_output`) and retrieved on demand via `get_output`, keeping the signal stream compact.

**TLA (Threat Level Access) System**
A four-tier threat classification (Safe, Elevated, Dangerous, Forbidden) shared between the userspace confirmation gate and the kernel policy engine. Every tool invocation is classified as max(host floor, manifest declaration, payload scan) and gated at or above the confirmation threshold. Escalation requires explicit out-of-band user confirmation — it cannot be triggered by model output or MCP server response alone. Sudo capability is an explicit, user-toggled grant (a validated, password-required sudoers drop-in); every individual escalation still requires the user to enter their password in an out-of-band GUI prompt, so no single grant gives the agent unattended root.

**Community-Vetted MCP Registry**
Modeled on the Arch Linux User Repository proofread model. Third-party MCP servers must pass community review — covering code, declared capabilities, and tool description accuracy — before being listed. Malicious or deceptive servers are filtered before they are ever discoverable by the tool search engine.

**Bloated Context Mitigation**
dispatch's bounded rolling signal window presents only the last twenty signal entries at each LLM wakeup, keeping context size predictable regardless of how many tasks have run. contextor complements this with retention-based pruning of stale conversation history, while the daemon's context manager preserves the system prompt — including its security constraints — and a rolling summary across every context refresh. Durable, per-constraint preservation is the unmitigated core of Forgetful Context (threat #7).

---

### A Note on Independent Convergence

In October–November 2025, JarvisOS implemented a structured MCP tool-description architecture — a design that major AI platforms independently converged on in early 2026. JarvisOS did not publish this design earlier because the security threats documented in this research had not yet been characterized or mitigated. We note this not as a priority claim, but as a validation: the problems this architecture addresses were real enough that multiple independent teams arrived at the same solution.

---

### Research Methodology

We evaluated threats across three escalation stages:

1. **User-level privileges** — standard access, no sudo. Establishes the baseline threat surface.
2. **Sudo-enabled** — full root control. The LLM can modify anything on the system.
3. **Web-enabled** — sudo plus internet access. Enables data exfiltration and remote prompt injection.

---

### Contributions

This research makes four concrete contributions:

1. A taxonomy of seven empirically-identified security threats specific to privilege-escalated LLM agents — including Bloated Context (the first identification of context-window saturation as a discrete security threat rather than a reliability problem) and Forgetful Context (the first identification of the absence of persistent security constraints as a discrete security threat).
2. Architectural mitigations for each threat class, implemented and verified against source code in the JarvisOS platform.
3. JarvisOS itself — a fully functional, bootable, open-source AI-native OS released as a research and development platform for the community.
4. A documented MCP tool-description architecture, independently developed in October–November 2025, predating its appearance in commercial deployments.

---

### Future Work

- **Empirical evaluation** — quantitative attack reproduction results measuring attack success rates, detection rates, and mitigation effectiveness under controlled conditions across the full seven-threat taxonomy.
- **Fine-tuning** — a LoRA/QLoRA fine-tune of Llama 3.1 8B using the NVIDIA NeMo Framework on the provenance-nonce labeled dataset generated by dispatch, with the resulting model and dataset released publicly on HuggingFace.
- **Platform expansion** — evolving JarvisOS from a research testbed into a general-purpose OS accessible to cybersecurity researchers, developers, and everyday users.
- **Community registry** — a public MCP registry infrastructure allowing third-party developers to submit and vet servers under the proofread model described in this research.

---

### Publications & Presentations

- **SURCA 2026** — Poster presentation, Washington State University Everett. *Winner, Gray Grant.*
- **Full paper** — *Security Threats in AI-Native Operating Systems: An Empirical Study Using Privilege-Escalated LLM Agents.* Pre-publication manuscript available on request.

---

### Source Code

The full platform is open-source under a dual-license model (AGPLv3 for community use; SCCL commercial licensing for entities that do not wish to comply with AGPLv3 source-disclosure terms).

- **Project-JARVIS** — [github.com/JarvisOSLinux/Project-JARVIS](https://github.com/JarvisOSLinux/Project-JARVIS)
- **dispatch** — [github.com/JarvisOSLinux/dispatch](https://github.com/JarvisOSLinux/dispatch)
- **dmcp** — [github.com/JarvisOSLinux/dmcp](https://github.com/JarvisOSLinux/dmcp)
- **contextor** — [github.com/JarvisOSLinux/contextor](https://github.com/JarvisOSLinux/contextor)
- **mcp-registry** — [github.com/JarvisOSLinux/mcp-registry](https://github.com/JarvisOSLinux/mcp-registry)

> *"Built for people, not corporations."*

---

### Changelog — corrected claims

*2026-07-22:* taxonomy updated to seven threats — Forgetful Context split from Bloated Context (2026-07) as the unmitigated novel finding; boundary protocol corrected to the current 128-bit CSPRNG nonce with daemon-side verification (the six-character Splitmix64 scheme and out-of-band-by-default description were the old design); TLA corrected to the four-tier Safe/Elevated/Dangerous/Forbidden classification (no Guest→Kernel levels, no goal-scoped sudo expiry — sudo is an explicit toggled grant with per-escalation password); Bloated Context mitigation no longer claims a contextor constraint-priority mechanism that does not exist; license corrected to AGPLv3 + SCCL; Rust package count and build-pipeline script count corrected.
