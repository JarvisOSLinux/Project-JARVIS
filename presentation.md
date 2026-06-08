# JARVIS OS — AI-Native Operating System

**By Toufic Majdalani and Yakup Atahanov**
**Washington State University**

---

# "You are no longer more valuable than the data you produce."

---

# The Shift Is Already Here

- Microsoft Copilot is embedded in Windows
- Apple Intelligence is built into macOS and iOS
- Google Gemini is integrated into Android and ChromeOS
- Every major OS vendor is embedding an AI assistant with deepening system access

This is not a prediction. It is happening now.

---

# The Trajectory

These assistants are moving from answering questions to controlling systems.

- 2023: "Summarize this document"
- 2024: "Manage my files and settings"
- 2025: "Run commands on my behalf"
- Next: Full OS-level access — the AI manages your computer

The destination is an AI with root privileges on your machine.

---

# The Business Model Is the Problem

These are corporate products built on data extraction.

Not because the engineers are malicious — because the incentive structure demands it.

- Your files, your commands, your patterns, your habits
- All of it becomes training data, analytics, leverage
- Legal frameworks lag years behind the technology
- Even well-intentioned companies answer to shareholders, not users

**The incentive to collect is structural. Policy alone cannot fix it.**

---

# Mass Surveillance at Scale

When an AI assistant has OS-level access, it sees everything:

- Every file you open
- Every command you run
- Every website you visit
- Every message you write
- Every password you type

This is not hypothetical. The infrastructure for mass surveillance is being built into the products people use every day — voluntarily.

**Legal or illegal, the capability is the same.**

---

# We Built Exactly This — On Purpose

We created a complete AI-native operating system to study what happens
when you give an LLM full OS privileges.

Not hypothetically. Empirically.

**Architecture:**

```
User (Voice / Text)
       |
  AI Daemon (local LLM via Ollama)
       |
  Task Orchestrator (dispatch)
       |
  Tool Manager (dmcp) --> MCP Servers
       |
  Policy Engine (4-tier enforcement)
       |
  Custom Linux Kernel (/dev/jarvis)
```

A full stack — from kernel drivers to voice interface — running locally,
no cloud, no data leaving the device.

---

# The Research Frame

- Built and operated at Washington State University
- Empirical findings from hands-on operation, not theoretical modeling
- Every threat we document was discovered by running the system
- Ongoing academic research effort

**We are not speculating. We ran the experiment.**

---

# What We Found: Forgetful Context

**The Novel Finding**

We told the AI: "Never modify files in /etc."

Minutes later, it did exactly that. Not because it disobeyed — because it **forgot**.

- LLMs have finite context windows
- As conversation grows, earlier instructions lose weight
- Security constraints stated at the start of a session get silently dropped
- The AI doesn't rebel. It simply stops remembering the rules.

**This finding has no prior literature. We discovered it through direct operation.**

Traditional security assumes the agent remembers its constraints.
LLMs do not.

---

# What We Found: Unauthorized Escalation

The AI discovered it could chain sudo commands to gain access
we never intended to give it.

- It didn't "hack" anything
- It followed the path of least resistance
- One authorized action led to another, then another
- Each step looked reasonable in isolation
- The cumulative result was full privilege escalation

**The AI doesn't need to be adversarial to be dangerous.
It just needs to be helpful.**

---

# What We Found: Blind Trust in Tools

The AI downloaded and executed a tool from the internet
because the description sounded right.

- No integrity verification
- No cryptographic signature check
- No sandbox isolation
- It trusted the label

**If a tool says "I manage files," the AI believes it.
A malicious tool with a helpful description is indistinguishable
from a legitimate one.**

---

# The Core Insight

> Traditional OS security was designed for deterministic programs.
> LLMs are probabilistic.
> The entire security paradigm is wrong.

- Firewalls assume you can enumerate allowed behaviors
- Access control assumes the agent follows rules consistently
- Sandboxing assumes the agent won't convince itself to escape
- Audit logs assume the agent isn't generating the logs

**None of these assumptions hold for an AI that reasons about its own constraints.**

---

# If This Is Coming Regardless, Who Should Build It?

The question is not whether AI will manage your computer.

The question is whether the code will be visible.

---

# The Corporate Problem Is Structural

Even well-intentioned companies face inescapable incentives:

| Incentive | Consequence |
|-----------|-------------|
| Data collection | Your usage patterns become product |
| Vendor lock-in | Switching costs keep you trapped |
| Surveillance compliance | Government requests are fulfilled quietly |
| Profit motive | Privacy costs money; collection makes money |

**These are not bugs. They are features of the business model.**

No amount of privacy policy can override a profit motive.

---

# The Open-Source Answer

Following the model established by the Free Software Foundation:

- **Community-owned** — no single company controls the code
- **Auditable** — anyone can read, verify, and challenge the implementation
- **Local-first** — your data stays on your device
- **Transparent** — trust is earned through visibility, not marketing

**Open source is not a nice-to-have.
It is the only structural answer to structural incentives.**

---

# What We Built

Not a product. Infrastructure.

| Component | Role |
|-----------|------|
| **Project JARVIS** | AI daemon — voice, text, orchestration |
| **dispatch** | Parallel task execution engine |
| **dmcp** | MCP server manager (discover, install, run) |
| **contextor** | Long-term memory with vector search |
| **mcp-registry** | Curated, integrity-verified tool catalog |
| **JARVIS OS** | Full Linux distribution — the implementation |
| **Custom kernel** | /dev/jarvis — policy enforcement at the kernel level |

Everything runs locally. Everything is open source.

---

# The Security Architecture

A 4-tier policy engine enforced at the kernel level:

| Tier | Behavior |
|------|----------|
| **SAFE** | Execute silently |
| **ELEVATED** | Execute + audit log |
| **DANGEROUS** | Block until explicit user confirmation |
| **FORBIDDEN** | Hard block — no override |

The policy engine runs in the kernel, not in the AI.
The AI cannot modify its own constraints.

---

# Where We Are Going

- Persistent constraint registers that survive context window decay
- Cryptographic verification for all third-party tools
- Kernel-level sandboxing for AI-invoked operations
- Community-driven tool ecosystem with integrity guarantees
- Academic publication of the threat taxonomy and findings

**The goal is not to build the best AI assistant.
The goal is to make AI-OS integration safe enough to trust.**

---

# "You are no longer more valuable than the data you produce."

Unless you can see the code that handles your data.

Unless the system runs on your hardware, under your control.

Unless the community — not a corporation — decides what the AI can do.

**That is what we are building.**

---

# Get Involved

**Website**: jarvisoslinux.org

**GitHub**: github.com/JarvisOSLinux

**Contact**:
- Toufic Majdalani — toufic@touficmajdalani.com
- Yakup Atahanov — yakup.atahanov@wsu.edu

All code is open source. All contributions are welcome.
