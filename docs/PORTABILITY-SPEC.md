# JARVIS Core Portability Specification

**Status: canonical.** This is the single source of truth for making the JARVIS
*core* (everything except the JARVIS OS embodiment — the Arch distro, the
`/dev/jarvis` kernel driver, systemd packaging) run on operating systems beyond
Linux. It is written to be cited by the research paper: every surface below is
backed by `file:line` evidence from the 2026-07-12 org-wide portability audit,
and every claim is marked against the code as it actually stands.

Where it disagrees with `README.md`'s "runs on any platform" copy or
`docs/architecture.md`, this document wins; those are being reconciled to it.

---

## 1. Thesis — why this is research, not just engineering

The project's central claim is that **traditional OS security models are
inadequate for probabilistic AI agents**, and it answers that with a six-threat
taxonomy plus mitigations (the TLA confirmation gate, the Cryptographic Boundary
Protocol, the vetted registry, integrity verification). The taxonomy is
**about the agent**, not about Linux — so it should be *universal*. The
mitigations, however, are currently *instantiated* on Linux primitives:
sudoers.d, pkexec/polkit, Unix-domain-socket `0600` permissions, `ksshaskpass`.

Porting the core is therefore a research probe with a falsifiable question:

> **Does each mitigation re-instantiate on a different privilege model
> (macOS Authorization Services, Windows UAC) without weakening the guarantee —
> or does the taxonomy leak?**

The audit already produced two concrete data points that answer it, and both
*strengthen* the paper rather than complicating it:

- **The mitigations do not transfer for free.** On the current Windows fallback,
  the TLA confirmation gate is bypassable (§3.1) and the threat classifier is
  blind to Windows-native destructive payloads (§3.3). A naive port is a
  *security regression*. This is direct evidence that "the taxonomy is universal;
  the enforcement is platform-instantiated" — you have to rebuild each mitigation
  against each OS's primitives, deliberately.
- **Windows UAC is arguably *closer* to the TLA ideal than sudoers.** There is no
  standing sudo grant on Windows; every elevation is a per-operation consent
  prompt — which is exactly the goal-scoped, per-action confirmation the TLA
  design argues for. sudoers' persistent grant is the weaker model. Worth a
  sentence in the paper.

**Framing for the port:** *the taxonomy is the invariant; the mitigations are
platform backends behind a common interface.* Everything below serves that.

---

## 2. Platform tiers

| Tier | OS | Commitment | Rationale |
|---|---|---|---|
| **T1 — reference** | Linux | The paper, SURCA, and all current testing target this. Nothing here may regress. | It is the JARVIS OS embodiment; sudoers/pkexec/`/dev/jarvis` live here. |
| **T2 — committed** | macOS 13+ | Full support, planned in detail (§8). | POSIX cousin: AF_UNIX, `/etc/sudoers.d`, `dirs`-resolved paths, `osascript` all carry over. Most of the work is a handful of branches. |
| **T3 — scoped, deferred** | Windows 10+ | Design constraints fixed now (§9); detailed build deferred. | The real redesign: no sudoers, no Unix-socket security semantics, no bash, process-tree kill needs Job Objects. |

The audit's headline: **the core is much closer to T2 than expected.**
`jarvis/platform/{linux,macos,windows}.py` already exists and auto-selects at
import (`jarvis/platform/__init__.py:11-20`); `dispatch` and `contextor` already
**compile clean** for `x86_64-pc-windows-msvc` and `aarch64-apple-darwin`
(verified by `cargo check --target`); `dmcp` is the only crate that fails to
compile off-Unix.

---

## 3. Hard invariants — properties the port MUST NOT break

These are non-negotiable. A backend that violates one is not "partial support,"
it is a regression and must not ship.

### 3.1 No network-exposed IPC. Ever.

The security architecture and the OpenClaw CVE comparison (`CVE-2026-25253`,
"no TCP listener, not Shodan-discoverable") rest on the daemon's IPC being
**file-system objects reachable only by the owning user**, not TCP ports.

- **Linux / macOS:** Unix domain sockets, `chmod 0600`, parent dir `0700`,
  accept-time peer-UID check. Already implemented
  (`jarvis/platform/linux.py:65-88`, `macos.py:58-81`).
- **Windows (when built):** **named pipes** (`\\.\pipe\jarvis-<user>-…`) with an
  explicit SDDL/DACL granting the current-user SID only — the true `0600`
  equivalent — plus `GetNamedPipeClientProcessId`/session as the `SO_PEERCRED`
  analog.
- **Forbidden on every OS:** falling back to loopback TCP. The lazy port is
  `127.0.0.1:port`; it deletes the differentiator.

> **⚠ Live regression.** The *current* Windows backend already violates this:
> `jarvis/platform/windows.py:36-46` binds `127.0.0.1`, `ipc_secure()` is a
> no-op (`:64-65`), and `ipc_verify_owner()` returns `True` unconditionally
> (`:67-68`). Combined with unauthenticated `approve_confirmation` /
> `shutdown_request` over that socket (`jarvis/runtime/io.py:103-127,305-333`),
> **any local process of any user can approve a TLA-gated privileged tool
> call.** This is tracked as a Phase-3 blocker, and until the named-pipe backend
> lands the interim requirement is a per-startup auth token (§9).

### 3.2 The confirmation gate fails **closed**, and never silently.

The gate must never auto-*approve*. It must also never silently auto-*deny* in a
way that shadows a working channel — a gate that always denies is a broken gate,
and a broken gate erodes trust in the whole mechanism.

> **⚠ Live regression.** `jarvis/platform/windows.py:72-79`
> (`has_desktop_notifications()` returns `True` whenever `ctypes.windll` exists,
> i.e. always) + the button-less balloon-tip stub that returns `None`
> (`:81-110`) means `confirmation_manager.py:366` computes `approved=False` for
> **every** desktop confirmation, and the early-return at `:299` prevents
> fallback to the working socket/CLI channels. **On Windows every confirmation is
> silently denied.** One-line interim fix: `has_desktop_notifications()` returns
> `False` on Windows until the real WinRT toast lands.

### 3.3 Threat classification is re-instantiated per OS, or it is a regression.

`jarvis/core/threat_level.py` is the daemon-side TLA gate. Its host floor
(`HOST_DANGEROUS_TOOLS`, `:38-50`) and payload patterns (`_DANGEROUS_PAYLOAD_PATTERNS`,
`:82-95`) recognize **only Unix syntax** (`bash`, `rm -rf`, `dd if=`, `| sh`).
On Windows, `Remove-Item -Recurse -Force`, `del /s /q`, `rd /s /q`, `format`,
`diskpart clean`, `reg delete HKLM`, `vssadmin delete shadows`,
`Start-Process -Verb RunAs` all classify **SAFE** and bypass confirmation
entirely. A port without per-OS payload signatures is a hole in the flagship
mitigation. The classifier *mechanism* is portable; its *signature list* must
grow an OS-specific table.

### 3.4 The integrity chain survives per-platform script variants.

The registry's "what was vetted is what runs" guarantee (manifest + setup-script
SHA-256) currently hashes exactly one `setup.sh` (`mcp-registry/scripts/validate_registry.py:80`,
`sync_registry.py:56`). Any per-OS setup variant (`setup.ps1`) must be hash-covered
too, and a `.gitattributes` must force LF so byte-exact hashes survive Windows
`autocrlf` checkouts. No platform may be installable via an unverified script.

---

## 4. Current state — evidence-grounded

### Already portable (verified, no work)
- **Platform abstraction exists and is used consistently.** No raw
  `AF_UNIX`/`start_unix_server` outside `jarvis/platform/` (grep-verified); IPC,
  paths, notifications, signals all route through it.
- **`dispatch` compiles clean** for Windows + macOS (`cargo check --target`,
  0 warnings); `libc` is target-gated (`Cargo.toml:24-25`), every unix construct
  is `cfg(unix)`-gated.
- **`contextor` is fully portable** — no `cfg(unix)`, no unix-only code;
  `dirs`-resolved storage, LE-encoded vectors, CRLF-tolerant NDJSON, bundled
  SQLite (builds under MSVC). Runs on all three as-is.
- **Voice stack is library-based, not shell-based**: `sounddevice` (PortAudio
  wheels for win/mac), `piper` as a Python lib (not a binary), `vosk` as a
  package, wake-chime via stdlib `wave` + sounddevice. No `aplay`/`afplay`/`winsound`.
- **Per-OS config/data dirs** already resolve (`%APPDATA%`/`%LOCALAPPDATA%`,
  `~/Library/Application Support`, XDG).
- **Linux-only features are correctly gated** and degrade to no-ops off-Linux:
  the `/dev/jarvis` kernel client (`kernel_client.py:245-249`), `is_sudo_enabled()`.

### Broken / blocking (with owners below)
| # | Surface | OS | Severity | Evidence |
|---|---|---|---|---|
| B1 | `dmcp` won't compile (unconditional `nix`) | Win | **blocks all** | `dmcp/Cargo.toml:14`, `src/elevation.rs:8` |
| B2 | IPC unauthenticated (TCP, no owner check) | Win | **security** | `windows.py:36-68`, `io.py:103-127` |
| B3 | Confirmations silently auto-deny | Win | **security/UX** | `windows.py:72-110`, `confirmation_manager.py:299,366` |
| B4 | `vosk>=0.3.45` has no macOS wheel → voice extras uninstallable | macOS | **blocks voice** | `pyproject.toml:47-51`; PyPI (0.3.44 is newest darwin) |
| B5 | Threat classifier blind to Windows payloads | Win | **security** | `threat_level.py:38-50,82-95` |
| B6 | shellmcp stdio loop can't start (`connect_read_pipe`) | Win | **blocks shellmcp** | `shellmcp/src/server.py:341-346` |
| B7 | shellmcp `_display_env()` calls `os.getuid()` unconditionally → crash on every `run_command` | Win | **crash** | `shellmcp/src/server.py:70` |
| B8 | `jarvis sudo` → `os.geteuid()` AttributeError instead of clean message | Win | crash | `sudo_manager.py:95,106` |
| B9 | dmcp system-scope paths (`/usr/share/mcp`) unwritable on macOS (SIP-sealed) | macOS | breaks system scope | `dmcp/src/paths.rs:43-44` |
| B10 | dispatch task-abort orphans MCP-server grandchildren | Win | correctness | `dispatch/src/mcp_client.rs` (Job Object needed) |

### Cross-cutting bugs found during the audit (fix regardless of OS)
- **`transport.py:22-24` replaces the child env** (`env={"RUST_LOG": …}`), which
  under the mcp SDK drops `PATH` and forces `os.defpath` resolution — silently
  works on system installs but **breaks `~/.local/bin` and Homebrew on every
  OS**. Fix: `env={**get_default_environment(), "RUST_LOG": …}`.
- **`MODELS_DIR`/`VOSK_MODEL_PATH` default cwd-relative** (`config.py:20,30-33`)
  — fragile everywhere; route through `platform.data_dir()`.
- **`packaging/jarvis.service:62` sets `JARVIS_MODELS_DIR` but `config.py:20`
  reads `MODELS_DIR`** — the systemd-installed daemon never sees its model dir
  (Linux packaging bug).
- **Stale deps**: `packaging/install.sh` + `debian/control` pull
  `python3-pyaudio`/`whisper`/`torch` the code never imports.

---

## 5. The platform-abstraction contract (the port's spine)

The Python daemon already has `jarvis/platform/base.py`. The port grows it (and
adds a small Rust mirror) so **every** OS-specific decision hides behind one
interface. Above the interface, nothing branches on OS.

### Python — `BasePlatform` additions
| Method | Purpose | Backends |
|---|---|---|
| `ipc_verify_peer(sock) -> bool` | accept-time peer check (replaces the `verify_owner` file check for connection auth) | Linux `SO_PEERCRED`; macOS `LOCAL_PEERCRED`; Windows pipe client PID/session or token |
| `resolve_sidecar(name) -> str\|None` | find `dispatch`/`dmcp`/`contextor` | `Config` override → `shutil.which` (handles `.exe`/PATHEXT) → per-OS install dirs |
| `grant_privilege()` / `revoke()` / `is_granted()` | the `jarvis sudo` mechanism | Linux/macOS sudoers.d; Windows Administrators-group or "unsupported" |
| `elevate(cmd)` | run one command elevated | Linux pkexec; macOS `osascript … with administrator privileges`/sudo; Windows `runas` |
| `system_ipc_candidates()` | endpoint discovery list | Linux `['/run/jarvis/…']`; macOS/Windows `[]` |

Kernel (`/dev/jarvis`) and sudoers packaging stay **out** of the abstraction —
they are OS-embodiment features, gated, not ported.

### Rust — new `platform` module per crate
- **dmcp** `src/elevation.rs`: `cfg(unix)` real impl, `cfg(windows)` stub;
  `re_exec_elevated()`, `write_file_elevated()`, `remove_dir_elevated()` replace
  inline `pkexec cp`/`pkexec rm`. `src/paths.rs`: per-OS system-scope defaults.
- **dispatch** `src/tree_kill.rs`: `TreeKillGuard` — unix = current
  `GroupKiller` (`process_group(0)`+`killpg`); Windows = Job Object with
  `KILL_ON_JOB_CLOSE`. Call sites become cfg-free.

---

## 6. Registry manifest v2 (platform-aware)

The registry *format* is Linux-shaped in four load-bearing places: single-string
stdio commands with `.venv/bin/python3` paths, `bash`-mandated `setupScript`,
`/usr/share`+pkexec system scope, and `setup.sh`-hardcoded integrity tooling.
A case-insensitive grep for `platform|os|win32|darwin` across the whole registry
returns **zero hits** — there is no way to express per-OS behavior today.

Manifest v2 (back-compatible: v1 clients ignore unknown keys):
1. **`platforms: ["linux","macos","windows"]`** per manifest (mirrored into
   `registry.json` for browse-time filtering). Absent ⇒ `["linux"]`.
   `jarvis-shell-system` declares `["linux"]` and stays linux-only by design.
2. **`transports[].platformOverrides: {"windows": {command,args}, "macos": {…}}`**
   — base fields are the default.
3. **Substitution variables** `${VENV_PYTHON}` (`.venv/bin/python3` vs
   `.venv\Scripts\python.exe`), `${EXE}` — fixes all six `.venv/bin/python3`
   manifests without duplication.
4. **`requirements: [{command,minVersion}]`** — moves bash-trapped version gates
   out of scripts so dmcp can pre-check on any OS.
5. **`setupScripts: {"posix":"setup.sh","windows":"setup.ps1"}`** (legacy
   `setupScript` aliases posix); **`integrity.setupScriptsSha256`** map per file.
6. **`validate_registry.py`/`sync_registry.py`**: glob `setup.*`, hash each,
   validate `platforms`, require a runnable transport per declared OS, add
   `.gitattributes` forcing LF (§3.4).

---

## 7. Per-repo work summary

- **Project-JARVIS (daemon):** grow `BasePlatform` (§5); implement Windows peer
  auth (named pipe / token) — closes B2; fix Windows confirmation (WinRT toast;
  interim `has_desktop_notifications→False`) — closes B3; Windows payload table
  in `threat_level.py` — closes B5; gate `jarvis sudo`/`sudo_manager` (B8);
  `transport.py` env-merge + `resolve_sidecar` (cross-cutting); macOS notification
  string-escaping; `pyproject` markers for vosk/aec (B4).
- **shellmcp (in Project-JARVIS):** thread-based stdio reader (B6);
  `cfg`-guard the Wayland/`getuid` block (B7); per-OS elevation strategy
  (osascript askpass on macOS; UAC on Windows); per-OS `PRIVILEGED_PREFIXES`;
  `open_app` via `open`/`os.startfile`.
- **dmcp:** target-gate `nix` + `cfg`-gate elevation (B1, unblocks compile);
  per-OS system-scope paths (B9); `.env.example` shadowing gate; `.cmd`/PATHEXT
  command resolution (npx/uvx); read-only `.git` handling; per-OS setup runner.
- **dispatch:** `TreeKillGuard`/Job Object (B10); cross-target CI. (Compiles
  today.)
- **contextor:** cross-target CI only. (Portable today.)
- **mcp-registry:** manifest v2 + validator (§6); author `setup.ps1` for the
  ~10 Windows-relevant servers; `.gitattributes`.

---

## 8. macOS — Tier 2 plan (committed)

macOS is a POSIX cousin; most of the daemon and all of dispatch/contextor work
today. The concrete list:

1. **`pyproject.toml` markers** (B4, the one true blocker):
   `vosk>=0.3.45; sys_platform != "darwin"` + `vosk>=0.3.44; sys_platform == "darwin"`;
   gate `aec-audio-processing` off macOS (no wheel, sdist needs a toolchain).
2. **dmcp on macOS** (compiles today via `nix`): system-scope paths →
   `/Library/Application Support/mcp` (not SIP-sealed `/usr/share`, B9); elevation
   → `sudo` re-exec when a TTY, else `osascript … with administrator privileges`.
3. **shellmcp askpass**: ship an `osascript`-based askpass shim so `sudo -A`
   works without `ksshaskpass`.
4. **Notifications**: `macos.py` `osascript display dialog` is already correct in
   shape — add title/body escaping (`:94-98` interpolates unescaped).
5. **`launchctl` label**: fix the `com.<name>.<name>` guess to try
   `homebrew.mxcl.<name>` + `launchctl kickstart` (`macos.py:114-128`).
6. **Peer auth**: `LOCAL_PEERCRED` accept-time UID check (§5).
7. **CI**: `macos-latest` job running `pip install .[voice]` + `pytest`, and
   `cargo test` matrices for the three crates.

Exit criterion for T2: `pip install project-jarvis[all]` succeeds on macOS 13+,
the daemon runs, a benign dispatch + a `smart`-mode confirmation + contextor
memory all work, and CI is green on `macos-latest`.

---

## 9. Windows — Tier 3 (scoped, deferred)

Detailed Windows planning is deferred, but the **design constraints are fixed
now** so nothing gets built into a corner, and the **live regressions get
interim mitigations** because the current Windows fallback ships insecure:

**Design constraints (locked):**
- IPC = **named pipes + per-user DACL**, never TCP (§3.1). Interim if the pipe
  backend isn't ready: a 32-byte per-startup token, written owner-only, required
  as the first line of every connection — this alone closes the cross-user hole
  because `%LOCALAPPDATA%` is per-user ACL'd.
- Elevation = **UAC** (`ShellExecute "runas"` / `Start-Process -Verb RunAs`),
  which cannot relay a stdio child's pipes — so system-scope `dmcp run` requires
  an already-elevated terminal (`is_elevated()` via `TokenElevation`), not
  re-exec.
- Setup = **`setup.ps1`** via `powershell -ExecutionPolicy Bypass`, hash-covered.
- Process-tree kill = **Job Object** (`KILL_ON_JOB_CLOSE`), not `taskkill /T`.

**Interim mitigations to apply before any Windows use (small, safe):**
- `has_desktop_notifications() → False` on Windows (stops the silent auto-deny,
  B3).
- Token-auth the Windows IPC or disable it (B2).
- `cfg`-guard shellmcp's `getuid`/Wayland block (B7) and thread the stdio loop
  (B6) so the server can start.

**The hard, deferred build:** WinRT actionable toast; named-pipe IPC backend;
Windows payload signatures (B5); Job Object (B10); `.cmd`/PATHEXT resolution;
read-only `.git` handling; manifest-v2 `setup.ps1` authoring; Windows service
wrapper. These become detailed issues when Windows is picked up.

---

## 10. Phasing & milestone

- **Phase 0 — compile everywhere.** dmcp `nix` gate + elevation stub + per-OS
  paths (B1, B9-compile); cross-target CI for all crates. *Unblocks the exact
  `cargo install` error and is a prerequisite for any non-Linux target.*
- **Phase 1 — grow the abstraction.** `BasePlatform` additions (§5);
  `transport.py` env-merge + `resolve_sidecar`; `MODELS_DIR` fix. Cross-cutting,
  benefits Linux too.
- **Phase 2 — macOS (T2).** §8 in full. Exit criterion above.
- **Phase 3 — Windows (T3, deferred).** §9. Interim mitigations may land early
  (they close live regressions); the full build is scheduled later.

A GitHub milestone tracks Phases 0–2; Phase 3 items are filed but not scheduled.

---

## 11. Non-goals (deliberate)

- **Porting the OS embodiment.** systemd unit, `/dev/jarvis`, polkit policy,
  sudoers packaging, Arch PKGBUILDs stay Linux-only by design. They are the
  research artifact, not the app.
- **A Windows/macOS kernel policy engine.** The 4-tier `/dev/jarvis` engine is
  Linux-only; the userspace TLA gate is the cross-platform mitigation.
- **Feature parity for privileged shell on Windows day one.** The privileged
  `jarvis-shell-system` server is JARVIS OS; a Windows analog is a separate
  server, not a port.

---

## Appendix — audit provenance

Findings from the 2026-07-12 org-wide portability audit: 6 parallel per-repo
auditors (daemon runtime/IPC, daemon privilege/notifications, daemon
voice/packaging, dmcp, dispatch, contextor+registry) + a completeness critic,
each reading source and verifying `file:line` claims (dispatch/contextor compile
status verified empirically via `cargo check --target`; PyPI wheel availability
verified live). Every surface in §4 traces to that audit.
