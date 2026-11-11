# Brahma AI Evo — User & Operations Guide

**Version:** v1.1.1
**Verified against:** `main` @ `f17d7c9` (2026-11-09)
**Audience:** end users, power users, operators, and developers working on the
repository.

This guide has two halves. **Part I** is how to run Brahma as a user. **Part II**
is how the machine works and how to control it as an operator or developer.

> **The code wins.** Where a design document, a README bullet, or an older guide
> disagrees with the tree, believe the tree. `docs/ARCHITECTURE-SPEC.md` is the
> system map and status authority; `docs/AGENTIC-HARNESS.md` is the harness
> contract. This guide is a guided tour of both, not a third authority.

---

## Contents

- [0. Document status — what is real and what is aspirational](#0-document-status--what-is-real-and-what-is-aspirational)
- [Part I — Using Brahma](#part-i--using-brahma)
  - [1. Install, launch, and first run](#1-install-launch-and-first-run)
  - [2. The three doors: voice, text, CLI](#2-the-three-doors-voice-text-cli)
  - [3. The Settings Hub](#3-the-settings-hub)
  - [4. Voice, speech, and avatars](#4-voice-speech-and-avatars)
  - [5. Identity, behavior, and proactive speech](#5-identity-behavior-and-proactive-speech)
  - [6. Skills](#6-skills)
  - [7. Memory](#7-memory)
  - [8. Sessions, resume, and `/new`](#8-sessions-resume-and-new)
  - [9. Approvals in the HUD](#9-approvals-in-the-hud)
  - [10. Shortcuts, tray, and window controls](#10-shortcuts-tray-and-window-controls)
  - [11. OpenAI-compatible local API](#11-openai-compatible-local-api)
  - [12. Outbound MCP server](#12-outbound-mcp-server)
  - [13. Mobile Connect and the device gateway](#13-mobile-connect-and-the-device-gateway)
  - [14. Agent & Orchestrator Bridge](#14-agent--orchestrator-bridge)
  - [15. Auto-Heal](#15-auto-heal)
  - [16. Troubleshooting](#16-troubleshooting)
- [Part II — Operating and developing Brahma](#part-ii--operating-and-developing-brahma)
  - [17. Architecture in one page](#17-architecture-in-one-page)
  - [18. Repository layout](#18-repository-layout)
  - [19. The agent harness contract](#19-the-agent-harness-contract)
  - [20. Permissions and approvals](#20-permissions-and-approvals)
  - [21. Command containment and `exec.sandbox`](#21-command-containment-and-execsandbox)
  - [22. Sessions, storage, and resume](#22-sessions-storage-and-resume)
  - [23. The event stream](#23-the-event-stream)
  - [24. Prompt and instruction discovery](#24-prompt-and-instruction-discovery)
  - [25. Two skill systems](#25-two-skill-systems)
  - [26. Subagents](#26-subagents)
  - [27. MCP: client and server](#27-mcp-client-and-server)
  - [28. CLI reference](#28-cli-reference)
  - [29. Security model and audit](#29-security-model-and-audit)
  - [30. Gates, testing, packaging, and release signing](#30-gates-testing-packaging-and-release-signing)
  - [31. Data paths and configuration files](#31-data-paths-and-configuration-files)
  - [32. Known limitations and the roadmap](#32-known-limitations-and-the-roadmap)
- [Appendix — Where to read next](#appendix--where-to-read-next)

---

## 0. Document status — what is real and what is aspirational

The `docs/` tree deliberately mixes current truth, design records, historical
logs, and proposals. Treating them as one authority level is the main source of
confusion. Use this table before quoting any document.

| Status | Meaning |
|---|---|
| **Implemented** | In the tree, exercised by tests, safe to rely on. |
| **Built-partial** | Usable, with a stated limit. Read the limit before depending on it. |
| **Designed** | A contract exists; the implementation is gated or not started. |
| **Aspirational** | Ideation, analysis, or a plan. No implementation. Do not describe it as a feature. |

### 0.1 Documents that are *not* descriptions of shipped behavior

These are the ones most likely to mislead a reader:

| Document | What it actually is | How to read it |
|---|---|---|
| `docs/SWARM-FEDERATION-INVESTIGATION.md` | Current-state investigation of a three-machine Brahma swarm. Its own verdict: **there is no swarm today and no code path that lets one Brahma call another as a peer.** | Inventory of `brahma_connect`, `brahma_api`, and the remote-agent client. Not a feature list. |
| `docs/SWARM-FEDERATION-DEEP-DIVE.md` | Peer-task-federation architecture (task envelope, leases, SAS pairing, token binding, ADR-030…038). | **Federation implemented & verified (S0–S5 & Artifact Lane).** BTE-1 routes, store, SAS pairing, identity, SPKI-pinned TLS on port 8443, content-addressed artifacts, LAN discovery, dynamic capability routing, and remote bridge are built and verified. |
| `docs/CAPABILITY-MAXIMIZATION-ANALYSIS.md` | Ideation and ranked roadmap for whole-system capability. Explicitly says "analysis and proposal, no implementation" and "believe the code." | Planning input, not a capability claim. |
| `docs/REBRAND-EXECUTION-PLAN.md` (+ ADR-029, `REBRAND-PROPOSITION.md`) | Plan to rename the assistant to "Abhasa". | **Planned.** Nothing has been renamed. |
| `docs/agent-orchestrator-bridge.md`, `docs/AGENT-BRIDGE-BUILD-TODO.md`, `docs/AGENT-BRIDGE-ADR.md` | Pre-harness "god orchestrator" design. | **Aspirational / superseded.** The bridge that actually shipped is `core/agent_bridge/` (ADR-018). |
| `docs/OPENAI-COMPATIBLE-API-DESIGN.md` | Design for the loopback API. Phases 0–2 are implemented; Phase 3 is gated. | Read for rationale; use §11 below for the shipped behavior. |
| `docs/SECURITY-ARCHITECTURE-ROADMAP.md` | Security workstream plan. Much of WS-1…WS-10 has since landed. | Planning record; verify against `ARCHITECTURE-SPEC.md` §5 and the tracker. |
| `docs/VERSION-1.0.1-*`, `docs/ONE-391-*`, `docs/CODE-REVIEW-TODO.md` | Historical build logs. Counts and line numbers are stale. | Rationale only. |

### 0.2 Known drift in the previous version of this guide

Corrected here, because these were the most common support questions:

| Old claim | Reality |
|---|---|
| Settings opens with `Ctrl+,` | There is **no** `Ctrl+,` binding. Click the **Settings** button in the top navigation bar. |
| `Ctrl+Shift+W` toggles the workspace sidebar | `Ctrl+[` and `Ctrl+]` toggle the left and right sidebars. |
| Push-to-talk is hold `Space` | Hold `Ctrl+Space` **when Push-to-Talk is enabled** in Settings. |
| API config is `config/brahma_api/config.json` | The config is `config/brahma_api.json`; tokens live in `config/brahma_api/tokens.json`. |
| Chat history is `workspace_store.db` | It is `%LOCALAPPDATA%\BrahmaAI\config\workspace_store.sqlite3`. |
| Proactive speech is under Speech Engines | The **Proactive Spoken Interjections** toggle is under **Identity & Profile**. |
| "15 built-in skills" are all core library skills | 3 live in the core library (`skills_library/`) and 12 are repo project skills (`skills/`). See §6. |

---

# Part I — Using Brahma

## 1. Install, launch, and first run

### 1.1 Installed build

1. Run `BrahmaEvo_Setup.exe`.
2. Accept or change the install directory. The default is
   `%LOCALAPPDATA%\Brahma_Evo`.
3. The installer creates a Desktop shortcut and a Start Menu entry.
4. Launch Brahma Evo from either.

The release bundle is produced by `build_all.ps1` and includes the app, a native
uninstaller, a CycloneDX SBOM, and an Ed25519-signed `release.json`. Updates are
verified fail-closed against the pinned public key in `core/release_manager.py`.

### 1.2 Developer checkout

```powershell
git clone https://github.com/titechprabhasolutions/Brahma-Ai-Evo.git
cd Brahma-Ai-Evo
.\scripts\setup_dev.ps1      # Python 3.11 venv + runtime, dev, and build deps
.\.venv\Scripts\python.exe main.py
```

`bootstrap.ps1` is the one-shot user path (installs Python if missing, creates
`.venv`, starts the app). `setup.py` refuses to run below Python 3.11.

### 1.3 First run

On first launch Brahma asks for your name and a **Google Gemini API key**. The
Gemini key is required for the Live voice path. Optional providers (OpenRouter,
a custom OpenAI-compatible endpoint, a local server) and integrations (Spotify,
Google Workspace, weather, smart home, Discord) are configured later in
Settings.

API keys and other secrets are encrypted at rest with Windows DPAPI
(`CryptProtectData`) through `core/secret_store.py` / `CredentialVault`. They
are not stored in plaintext.

### 1.4 What "Brahma" is made of

- **Voice door** — Gemini Live bidirectional audio, tool calls, and turn
  archival.
- **Text door** — the HUD chat, using Gemini, OpenRouter, a custom endpoint, or
  a local model.
- **CLI door** — `python -m agent`, the same harness without the GUI.
- **Local services** — the loopback OpenAI-compatible API (`brahma_api`), the
  device gateway (`brahma_connect`), and the outbound MCP server.

All four share one execution boundary: `AgentRuntime.execute`.

---

## 2. The three doors: voice, text, CLI

| Door | Best for | Tools | Approval surface |
|---|---|---|---|
| **Voice** (Gemini Live) | Conversation, quick desktop actions, vision | Advertised subset; dispatch tools omitted | HUD banner |
| **Text** (HUD chat) | Multi-step work, code, file edits | Full harness surface | HUD banner |
| **CLI** (`python -m agent`) | Automation, CI, headless runs, scripting | Full harness surface | TTY prompt, `--allow/--deny`, or headless deny |

The voice door deliberately advertises `list_subagents` but **not**
`run_subagent`, `run_subagent_batch`, or `list_subagents`' dispatch siblings:
the Gemini Live session has no text completer with which to build a child loop.
It can tell you what specialists exist; it cannot run one. Text and CLI can.

The retired `agent_task` tool is gone. A stale call receives a pointer to the
harness instead of dispatching the old autonomous executor. That executor
(`agent/planner.py`, `executor.py`, `task_queue.py`, `error_handler.py`) was
deleted because it bypassed the policy, approvals, containment, sessions, and
events.

---

## 3. The Settings Hub

Open Settings with the **Settings** button in the top navigation bar. There is
no keyboard shortcut.

The left sidebar has three top-level sections:

1. **Brahma Evo Home** — smart-home integrations.
2. **Devices** — connected hardware and device control.
3. **System & Connect** — providers, APIs, speech, memory, identity, UI, and
   diagnostics.

### 3.1 Cards under System & Connect

| Card | What it controls |
|---|---|
| **Spotify MCP Server** | Spotify Web API client ID/secret and playback tools. |
| **Google Workspace** | Gmail, Calendar, and Drive integration. |
| **Custom MCP Servers** | Additional stdio/HTTP MCP servers and their tools. |
| **Agent & Orchestrator Bridge** | Discovery, enablement, and probing of external agents. |
| **OpenAI-Compatible Local API** | Enable the loopback port, list/revoke/mint bearer tokens. |
| **Tool & Skill Index** | Tool-index on/off and advertised-tool budget. |
| **Tools & MCP Access** | Per-tool visibility (does not delete a tool; it hides it). |
| **Skills Library** | Core skill catalogue, master switch, per-skill enablement. |
| **Memory System & Connectors** | Local / Mem0 / Letta backend and connection test. |
| **Speech Engines** | STT and TTS engine selection, voices, local models. |
| **Popup Timers** | Auto-dismiss behavior for popups. |
| **Apply & Diagnostics** | Apply settings, inspect status. |
| **Providers & Resources** | Provider keys, model selection, resource diagnostics. |
| **Avatar Instructions** | Avatar master switch, renderer, assets, reactivity. |
| **Identity & Profile** | Owner/assistant identity, behavior mode, custom instructions, proactive speech. |
| **AI Providers** | Default provider and the supported provider list. |
| **Mobile Connect** | Pair a phone, generate the pairing QR. |
| **Attention Prompts** | Incoming message and call prompts. |
| **Startup** | Windows startup, launch-minimized, update check. |
| **Shortcuts & Pinning** | Desktop/Start Menu shortcuts and pinning. |
| **App Theme** | Primary color theme. |
| **Startup Animation** | Boot animation behavior and speed. |
| **Discord Bot** | Mirror the app to a Discord server. |
| **About Brahma Evo** | Version and product information. |
| **Auto-Heal** | Sentry status, patch history, rollback. |
| **Audio Routing & Hardware Controls** | Microphone/speaker selection, Push-to-Talk, memory inspector. |

Settings changes are persisted under `%LOCALAPPDATA%\BrahmaAI\config\` (for
example `app_settings.json` and `identity.json`). User data is never committed.

---

## 4. Voice, speech, and avatars

### 4.1 Gemini Live voices

Under **Speech Engines → Live Voice**, choose one of five:

| Voice | Character |
|---|---|
| **Charon** *(default)* | Deep, authoritative, confident |
| **Puck** | Energetic, dynamic, playful |
| **Aoede** | Warm, melodic, conversational |
| **Fenrir** | Grounded, direct, straightforward |
| **Kore** | Calm, gentle, soothing |

The choice applies to live audio conversations immediately.

### 4.2 Speech-to-text and text-to-speech

The speech pipeline is modular:

**STT**
- **Google (cloud)** — default.
- **faster-whisper (local)** — select a model such as `tiny.en` or `base.en`.
- **Disabled**.

**TTS**
- **Microsoft Edge (cloud)** — default.
- **Piper (local neural)** — choose an installed voice.
- **Kokoro (local neural ONNX)** — choose a model and voice.
- **Windows SAPI (system voice)**.
- **Google Gemini (cloud)**.

A local engine that is not installed does not break the app; it is marked
unavailable and the pipeline falls back. Piper and Kokoro keep separate voice
selections so switching engines never leaves the other engine's value behind.

### 4.3 Push-to-talk

Enable **Push-to-Talk** under **Audio Routing & Hardware Controls**. While it is
on, hold **`Ctrl+Space`** to speak. When it is off, the microphone follows the
normal live-session behavior.

### 4.4 Avatars

Under **Avatar Instructions**:

- **Master switch** — enable or disable avatar rendering.
- **Renderer** — the default 3D holographic orb (`three_reactive`), Live2D, VRM,
  or an OSC/VMC host.
- **Assets** — install or import Live2D models, `.vrm` files, and scene
  backgrounds.
- **Reactivity intensity** — how strongly the avatar responds to emotion tags
  and vocal inflection.

Renderer availability is honest about its dependencies:

| Renderer | Status |
|---|---|
| `three_reactive` | Implemented; safe to disable pixel-identically. |
| `vrm` | Fully available. |
| `live2d` | Implemented; needs the user-supplied Cubism runtime. |
| `osc_vmc` | Implemented; needs a VMC host. |

The VRM loader reads permission flags from the file, not the license name, and
refuses models it cannot prove permissive.

---

## 5. Identity, behavior, and proactive speech

Everything here is under **Identity & Profile**.

### 5.1 Behavior presets

| Preset | Directive |
|---|---|
| **Professional** *(default)* | Executive, structured, objective, polished; concise bullet points; no filler. |
| **Casual** | Friendly, conversational, empathetic, natural. |
| **Technical** | Engineering/architectural depth; exact terminology, code, root-cause diagnostics. |
| **Minimal** | Maximum brevity; no pleasantries or preamble. |
| **Proactive** | Anticipates next steps, suggests optimizations, surfaces risks unprompted. |

### 5.2 Custom instructions

The **Custom Instructions (Persistent)** box adds permanent rules to the
assistant's identity. Examples:

- "Always format SQL in uppercase."
- "When reviewing code, list security concerns first."
- "Call me by my preferred name."

These are identity-level preferences, not project instructions. Project rules
belong in `AGENTS.md` / `BRAHMA.md` / `.brahma/AGENTS.md` (see §24).

### 5.3 Proactive spoken interjections

The **Proactive Spoken Interjections (Health, System Alerts, Idle Checks)**
toggle lives here, not under Speech Engines. It maps to
`identity.is_proactive()` / `set_proactive()`. When off, Brahma speaks only when
addressed or when finishing a requested action.

---

## 6. Skills

Brahma has **two skill systems** on purpose. They share a store but have
different tools and different trust levels.

### 6.1 Core Skills Library (the "playbooks")

The app's core library (`core/skills_library.py`) is the catalogue the voice and
GUI surfaces use through `search_skills` / `load_skill` / `list_skills`. It is
just-in-time: only the name and description are resident; the body is fetched
when the model decides the skill matches.

The catalogue currently has **15 entries**:

**3 core skills** in `skills_library/`:

1. Reasoning Rigor
2. Web Research
3. Word Count & Text Processing

**12 repository skills** in `skills/`:

4. Code Review & Security Audit
5. Browser Automation
6. API Mock Engine
7. Database Architect
8. Docker & DevOps
9. Fullstack Scaffolder
10. Git Workflow
11. GitHub Automation
12. Performance Profiler
13. Security Auditor
14. Test-Driven Development
15. Web Scraper (Stealth)

Enable or disable individual skills, or the whole library, in the **Skills
Library** card. A repository may also ship `.brahma/skills/<name>/SKILL.md`;
those project skills are the highest-precedence source in the core library.

### 6.2 Project skills (the harness's view)

The harness (`agent/skills.py`) discovers `.brahma/skills/<name>/SKILL.md` in the
workspace, up to three ancestors, and the user skills directory. Only the name
and description are resident; `load_project_skill` fetches the body on demand.

Project skills are **instructional only**. A file that claims
`executable: true` is still treated as prompt text. `allowed-tools` is parsed
and shown by the CLI but **not enforced** — enforcement would be a second
permission boundary, and the policy must stay the only one.

The tool is named `load_project_skill`, not `load_skill`, because
`core/tool_exec.py` runs the harness branch first; a harness tool named
`load_skill` would shadow the core library tool on the voice and GUI surfaces.

### 6.3 The forge

**Skills Forge** (`core/skill_forge.py`) can synthesize a new executable skill.
Drafts are staged in `%LOCALAPPDATA%\BrahmaAI\staged_skills\`, verified by
`SkillCrucible` in an isolated subprocess (`-I`, scrubbed env, `run_captured`),
and promoted only through the human **Skill Review** dialog (`/skills`). The
forge is powerful and is treated as a security-sensitive area.

---

## 7. Memory

### 7.1 Backends

Open **Memory System & Connectors** and pick one:

1. **Local Storage (default)** — offline, zero setup. Facts live in
   `%LOCALAPPDATA%\BrahmaAI\memory\long_term.json`; chat and attachments live in
   the workspace SQLite database. Click **Open Memory Folder** to inspect or
   back it up.
2. **Mem0 Hybrid Vector Memory** — paste a Mem0 API key and save. The key is
   encrypted with DPAPI.
3. **Letta / MemGPT Tiered Archival** — enter the server URL (default
   `http://localhost:8283`), agent ID, and optional token, then **Save &
   Connect**.

**Test Memory Recall** stores and retrieves a probe entry and reports the live
backend status and entry count.

### 7.2 What is stored where

| Data | Path |
|---|---|
| Semantic facts and preferences | `%LOCALAPPDATA%\BrahmaAI\memory\long_term.json` |
| Conversations, messages, agent sessions, memories | `%LOCALAPPDATA%\BrahmaAI\config\workspace_store.sqlite3` |
| Identity/profile | `%LOCALAPPDATA%\BrahmaAI\config\identity.json` |
| API keys / secrets | `%LOCALAPPDATA%\BrahmaAI\config\api_keys.json` (DPAPI-encrypted) |

Memory extraction is heuristic plus model-assisted; it is not a background
surveillance loop. Audio and video are streamed only during an active session.

---

## 8. Sessions, resume, and `/new`

The GUI text path and the CLI share one session store
(`agent_sessions`, `agent_messages`, `agent_events` in
`workspace_store.sqlite3`). The HUD supports three slash commands:

| Command | Effect |
|---|---|
| `/sessions` | List recent agent sessions. |
| `/resume` | Open a picker; `/resume <id>` binds a session (unique prefix accepted). |
| `/new` | Drop the current binding and start a fresh session. |
| `/skills` or `/skill-review` | Open the Skill Review dialog for staged skills. |

The tray menu's **Resume Agent Session…** submits `/resume` for you.

How the binding behaves:

- The first agentic turn creates a session row against the runtime workspace.
- Later turns are seeded as `[fresh system, stored history, new user]`.
- The system prompt is **never** persisted; only the transcript and the new tail
  are. A resumed session rebuilds the system prompt and appends only new
  messages.
- A completed Gemini Live turn is archived into the same row as a cleaned
  user/assistant pair, so voice conversations appear in the list and can be
  replayed as text. The live socket itself is never re-seeded from disk.
- A spoken turn that never reaches `turn_complete` (a dropped socket) is lost.

See §22 for the schema and the resume contract.

---

## 9. Approvals in the HUD

When a tool call needs a human, the HUD shows a banner with:

- the matched rule and its reason,
- the exact command, resolved `cwd`, and timeout for `run_command`,
- a real unified diff for `edit_file` / `write_file`,
- the resolved path and pattern/range for reads.

Buttons:

- **Approve** — run this once.
- **Deny** — refuse; the model is told not to claim it happened.
- **ALWAYS ALLOW** — shown only for session-approvable calls. It remembers:
  - the **exact command** for `run_command`,
  - the **resolved path** for outside-workspace/protected-path asks,
  - the **tool name** for other file/native asks.

`ALWAYS ALLOW` never widens a policy `deny`, never applies to a
`requires_approval` spec (irreversible actions ask every time), and never writes
to disk. It lasts only for the current process. To make an approval permanent,
edit the policy file (§20).

When the detail contains a diff, the banner shows a one-line summary
(`+2 −1 in 1 hunk`) and a **CHANGES ONLY** button that folds unchanged context.
The underlying diff is unchanged, so either view executes the same call.

---

## 10. Shortcuts, tray, and window controls

These are the bindings that actually exist in `ui.py` and `core/hotkey.py`:

| Shortcut | Action |
|---|---|
| `F4` | Mute / unmute the microphone. |
| `F11` | Toggle fullscreen. |
| `Ctrl+[` | Toggle the left sidebar. |
| `Ctrl+]` | Toggle the right sidebar. |
| `Ctrl+Shift+X` | Request the running agent turn to stop (sets `runtime.cancel()`). |
| `Ctrl+Space` *(hold)* | Push-to-talk, **only when enabled** in Audio Routing. |
| Click the chat bubble | Reveal the complete streamed reply immediately. |

There is no `Ctrl+,` Settings shortcut and no `Ctrl+Shift+W` workspace toggle.

The tray menu offers **Open App**, **Open Workspace**, **Close Workspace**,
**Show Workspace On Startup**, **Show/Hide Floating Icon**, **Resume Agent
Session…**, **Restart**, and **Quit**.

Cancellation is cooperative: `Ctrl+Shift+X` asks the loop to stop after the
current call. A second stop (or `Ctrl-C` in the CLI while already cancelling)
aborts harder. Writes and child processes are allowed to finish; the worker
thread is never killed.

---

## 11. OpenAI-compatible local API

`brahma_api` is a loopback HTTP surface that lets external editors, scripts, and
coding agents use Brahma as their backend.

### 11.1 Enable it

Open **Settings → System & Connect → OpenAI-Compatible Local API** and toggle it
on. The card also lists active tokens and lets you revoke or mint them.

Configuration is `config/brahma_api.json` (copy `config/brahma_api.example.json`
to start). The example ships **disabled**; enabling writes the same file. Default
listener: `http://127.0.0.1:8010/v1`.

Key defaults from the example:

| Setting | Default |
|---|---|
| `host` / `port` | `127.0.0.1` / `8010` |
| `enabled` | `false` |
| `allow_remote_bind` | `false` |
| `auth_required` | `true` |
| `requests_per_minute` | 30 |
| `concurrent_runs` | 2 |
| `concurrent_runs_per_token` | 1 |
| `max_turns` | 12 |
| `max_messages` | 200 |
| `max_body_bytes` | 1,000,000 |
| `wall_clock_s` | 300 |
| `approval_timeout_s` | 60 |
| `stream_mode` | `final` |
| `audit` | `true` |

### 11.2 Tokens and presets

Every call needs a bearer token. Tokens are stored as SHA-256 hashes in
`config/brahma_api/tokens.json`; the plaintext is shown once at mint time.

| Preset | Capability |
|---|---|
| **`chat`** | Model conversation only. No tools, no workspace. |
| **`reader`** | Read-only tools. Needs a workspace alias. |
| **`worker`** | State-modifying tools inside the configured workspace. Needs a workspace alias. |
| **`operator`** | Full local capability. Equivalent to shell access; minting requires typing `operator` to confirm. Needs a workspace alias. |

A key can always ask for a **lower** capability than its preset, never a higher
one: a `reader` key cannot become a writer by naming a model. A `worker` key may
deliberately downgrade to `brahma-evo:read`.

### 11.3 Model IDs

| Model | Lane |
|---|---|
| `brahma-evo` | Agent loop with tools. |
| `brahma-evo:read` | Read-only agent lane. |
| `brahma-evo:plan` | Planning lane, no tool execution. |
| `brahma-evo:passthrough` | Forwards the client's own tool calls; Brahma routes but does not execute them. |

`hybrid` is parsed for forward compatibility but is **not implemented** in this
build. A request for it is rejected with the available model list.

### 11.4 Client examples

**Cline (VS Code):** provider `OpenAI-Compatible`, base URL
`http://127.0.0.1:8010/v1`, API key = minted token, model `brahma-evo` or
`brahma-evo:passthrough`.

**Continue.dev:**

```json
{
  "models": [
    {
      "title": "Brahma Evo",
      "provider": "openai",
      "model": "brahma-evo",
      "apiBase": "http://127.0.0.1:8010/v1",
      "apiKey": "YOUR_MINTED_TOKEN"
    }
  ]
}
```

**Python SDK:**

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8010/v1",
    api_key="YOUR_MINTED_TOKEN",
)

response = client.chat.completions.create(
    model="brahma-evo",
    messages=[{"role": "user", "content": "List the files in my workspace"}],
)
print(response.choices[0].message.content)
```

The API is a **transport, not a fourth door**: every tool call still goes
through `AgentRuntime.execute`, the same policy, the same approvals, and the
same event stream.

---

## 12. Outbound MCP server

Brahma can expose its repository tools to an external MCP client (Claude
Desktop, Cursor, Windsurf, Zed) over stdio JSON-RPC.

```powershell
python -m agent mcp-server --cwd "C:\path\to\your\project"
```

Options:

| Flag | Meaning |
|---|---|
| `--cwd` | Workspace root. |
| `--permissions` | Explicit `agent_permissions.json`. |
| `--allow` / `--deny` | Class overrides. |
| `--yolo` | Allow every class. Sandbox only. |
| `--name` | Server name (default `brahma-agent`). |
| `--timeout` | Per-command tool timeout. |

What it exposes:

- **Tools** — every registered canonical `ToolSpec` (code tools, MCP-discovered
  tools, native specs), all executed through `AgentRuntime.execute`.
- **Prompts** — project skills (`prompts/list`, `prompts/get`).
- **Resources** — workspace instruction/config files such as `AGENTS.md`,
  `BRAHMA.md`, `.brahma/AGENTS.md`, and MCP config (`resources/list`,
  `resources/read`).

Claude Desktop configuration (`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "brahma": {
      "command": "python",
      "args": [
        "-m", "agent", "mcp-server",
        "--cwd", "C:\\Users\\Admin\\YourProject"
      ]
    }
  }
}
```

If you use a specific interpreter, point `command` at it (for example the
`.venv\Scripts\python.exe` from `setup_dev.ps1`). The server's version is
reported as `1.1.1`.

---

## 13. Mobile Connect and the device gateway

**Mobile Connect** pairs a phone or tablet with the desktop app. Click
**Generate QR Code** to show the pairing offer; the companion app scans it and
authenticates.

The gateway (`brahma_connect/gateway/`) listens on `127.0.0.1:8765` by default
and is **disabled** in the shipped configuration. Config is
`config/brahma_connect.json`; paired devices live in
`config/brahma_connect/devices.json`.

Defaults:

| Setting | Default |
|---|---|
| `host` | `127.0.0.1` |
| `port` | `8765` |
| `enabled` | `false` |
| `advertise` | `false` (mDNS) |

Binding to a LAN address (`0.0.0.0`) requires an explicit `host` **and**
`enabled: true`. Control routes require a paired-device bearer token
(`/gateway/info`, `/gateway/pair`, `/gateway/execute`, `/gateway/devices`,
`/gateway/logs`, `/gateway/pending`, approve/reject). The only exceptions are
`/health` (public) and the two pairing-bootstrap routes,
`/gateway/pair/start` and `/gateway/pair/verify`, which a not-yet-paired device
must be able to reach; those are rate-limited and use a short-lived PIN/SAS
code. WebSocket connections authenticate the paired device before commands are
accepted.

This is a **device** surface, not a peer-agent surface. It does not let one
Brahma call another Brahma as an equal; see §32 and
`docs/SWARM-FEDERATION-INVESTIGATION.md`.

---

## 14. Agent & Orchestrator Bridge

The bridge (`core/agent_bridge/`) discovers external agents and publishes them
as ordinary `ToolSpec`s, so the model can call them through the same permission
boundary as any other tool. It is configured under **Agent & Orchestrator
Bridge**: scan, enable/disable, and probe.

Layers:

| Tier | What it covers |
|---|---|
| **Tier 1 — local** | Local model servers (Ollama `:11434`, LM Studio `:1234`, vLLM `:8000`) and CLI coding agents (Claude Code, Codex CLI, Aider, Gemini CLI). |
| **Tier 2 — orchestrators** | Orca and Paperclip adapters for worktrees/tickets. |
| **Tier 3 — remote** | An A2A-shaped HTTP client (`/.well-known/agent.json`, `POST /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel`) for remote agents. |

Important: the Tier 3 **client** exists; no Brahma process serves those routes
as a peer task server. The bridge also publishes `read_agent_artifact` and
stores large agent output under `%LOCALAPPDATA%\BrahmaAI\agent_artifacts\` with
`agent-out://` references.

Discovery is opt-in per layer. Nothing starts a local server, installs a CLI
agent, or connects to a remote endpoint without being enabled.

---

## 15. Auto-Heal

The **Auto-Heal & Continuous Self-Improvement** card reports sentry status and
offers **Patch History** and **Rollback Last Patch**.

When a runtime exception is captured, the sentry diagnoses the traceback, asks
the model for a patch, validates it (syntax/AST), and applies it with an atomic
backup. Patch history and rollback are available from the card. This is a
high-privilege feature: it edits the running codebase, so review the history
rather than assuming every automatic patch is desirable.

---

## 16. Troubleshooting

### Settings will not open with `Ctrl+,`

It never did. Use the **Settings** button in the top navigation bar.

### Custom endpoint connection issues

- The **Remote URL** field expects the OpenAI **base** URL, for example
  `http://127.0.0.1:20128/v1`. Brahma appends `/chat/completions`.
- On Windows, prefer `127.0.0.1` over `localhost`: `localhost` may resolve to
  IPv6 (`[::1]`) first and time out.
- HTTP 401/400 usually means a missing key or an inactive model ID. If a router
  upstream has exhausted quota, switch to a live backup model.

### "The agent denied my command"

That is the policy, not a bug. `exec` defaults to `ask`; in `--json`/CI runs
there is no human, so it becomes `deny`. Inspect the policy with
`python -m agent permissions show`, or pass `--allow exec` for a trusted run.
Do not use `--yolo` outside a container or disposable VM.

### "A tool ran but the file did not change"

Check whether the call was a **write** that needed approval. A denied write is
reported as a tool result and counted as `permission_denied`; the model is told
not to claim it happened.

### The voice session drops with `1008`

Gemini Live closes the socket when the model calls a tool that was discovered
but not advertised. The mitigation is built in: `search_tools`/`list_tools`
return a literal `call_tool(...)` template, and a bounded reconnect re-advertises
the names that were taught. If it persists, restart the session; the pin buffer
is session-scoped.

### The local API returns 401

Mint a token in the OpenAI card, copy it immediately (only its hash is stored),
and send it as `Authorization: Bearer <token>`. A `reader`/`worker`/`operator`
token also needs a workspace alias configured in `config/brahma_api.json`.

### The gateway is unreachable from the phone

It is disabled and loopback-only by default. Enable it and set an explicit LAN
`host` only on a trusted network, then pair the device. Every control route
requires the paired-device token.

### Where are the logs?

HUD diagnostics and the **Apply & Diagnostics** card are the first stop. Agent
events are in `agent_events`; the CLI's `--json` mode emits the same events as
NDJSON. Gateway logs are exposed at `/gateway/logs` to a paired device.

---

# Part II — Operating and developing Brahma

## 17. Architecture in one page

```
User
 ├─ Voice (Gemini Live WebSocket) ─┐
 ├─ HUD text (Qt) ─────────────────┤
 └─ CLI (`python -m agent`) ───────┤
                                    ▼
                         AgentRuntime.execute
                    (policy → approval → handler)
                                    │
             ┌──────────────────────┼──────────────────────┐
             ▼                      ▼                      ▼
      code tools (agent/)     native ToolSpecs        MCP tools
      read/write/edit/        (core/desktop_tools,   (agent/mcp.py,
      glob/grep/run_command    actions/, features/)   core/mcp_manager.py)
             │                      │                      │
             └──────────────────────┴──────────────────────┘
                                    ▼
                         Tools / OS / files / network
```

Non-negotiable properties:

1. **One boundary.** Every harness tool call on every door passes through
   `AgentRuntime.execute`. `core/tool_exec.py` has **zero** `elif` branches for
   tool dispatch; all 84 desktop specs register as `ToolSpec`s.
2. **One transcript shape.** The stored transcript is OpenAI-shaped
   (`assistant.tool_calls` followed by one `tool` message per call, in order).
   Gemini declarations are converted only at the edge.
3. **One MCP client per layer.** `agent/mcp.py` (stdlib) for the harness;
   `core/mcp_manager.py` (SDK) for the voice path. No third client.
4. **One process path.** `run_command` goes through `agent.process.run_captured`;
   no second `subprocess.run(shell=True, timeout=...)`.
5. **One cancellation model.** A flag (`runtime.cancel_event`), never a killed
   worker thread.

Provider chain (text): `gemini > openrouter > local > custom`, each gated by its
kill switch **before** the call. Gemini Live is special because it is a
bidirectional audio socket; the other three share an OpenAI-compatible adapter.

---

## 18. Repository layout

| Path | Contents |
|---|---|
| `main.py` | Entry point; `BrahmaLive`, provider routing, native declarations. |
| `ui.py` | Thin `MainWindow`; behavior lives in `core/ui_*`. |
| `core/` | Seams: `tool_exec.py`, `tool_index.py`, `confirm.py`, `undo.py`, `user_paths.py`, UI mixins. |
| `actions/` | One module per desktop capability. |
| `agent/` | The stdlib-only harness: loop, code tools, permissions, context, sessions, subagents, MCP. |
| `brahma_api/` | OpenAI-compatible loopback server, auth, model lanes, config. |
| `brahma_connect/` | Device gateway, pairing, WebSocket protocol. |
| `core/agent_bridge/` | External-agent discovery and ToolSpec publication. |
| `skills_library/` | 3 core skills (instructional + executable). |
| `skills/` | 12 repo project skills (`SKILL.md`). |
| `subagents/` | Vendored specialist library: `catalog.json` plus ~600 bodies. Data, not code. |
| `tests/` | Pytest suite; portable tests need no display or model. |
| `docs/` | Design records, status logs, and this guide. |

The harness is **stdlib-only** and must stay that way. CI runs
`tests/test_agent_*.py` on Python 3.11, 3.12, and 3.13 with only pytest
installed. Do not import PyQt, a provider SDK, or a Windows COM module from
`agent/` at module scope. `agent/__init__.py` stays empty so `python -m agent`
never imports PyQt.

---

## 19. The agent harness contract

### 19.1 The loop

`AgentLoop` is a bounded observe → act → observe loop:

- **Turn cap** — default 40 (`--max-turns`).
- **Repeat detection** — identical `name + canonical JSON` calls are skipped
  after N occurrences (default 3, `--repeat-limit`).
- **Stop reasons** — `completed`, `max_turns`, `repeat_loop`, `cancelled`,
  `error`, `permission_denied`.
- **Retries** — transient provider failures are retried with backoff
  (`--retries`, default 3).
- **Compaction** — the context is folded at a character threshold
  (`--max-context-chars`, default 120,000) without orphaning tool results.

### 19.2 Tools

The harness primitives are:

| Tool | Class | Purpose |
|---|---|---|
| `read_file` | `read` | Read a file with line ranges and whitespace hints. |
| `write_file` | `write` | Create or overwrite a file. |
| `edit_file` | `write` | Replace an exact text span. |
| `glob_files` | `read` | Path globbing. |
| `grep_files` | `read` | Content search. |
| `run_command` | `exec` | Shell command through `run_captured`. |
| `run_subagent` | `native` | One bounded child agent. |
| `run_subagent_batch` | `native` | Read-only fan-out across children. |
| `list_subagents` | `native` | Discover specialist templates. |
| `load_project_skill` | `native` | Load a `.brahma/skills` body on demand. |
| `mcp_<server>_<tool>` | `mcp` | A tool contributed by an MCP server. |

### 19.3 Adding a tool

Write a `ToolSpec` (or use the `@tool` decorator) in `agent/spec.py` or a
sibling module, import it for registration, choose its `tool_class`, and add
tests.

```python
from agent.spec import ToolSpec, register

def _handler(args, ctx):
    return "ok"

register(ToolSpec(
    name="my_tool",
    description="What it does, for the model.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    tool_class="read",          # read|write|exec|network|device|mcp|native
    handler=_handler,
    requires_approval=False,    # True = ask every time, never remembered
))
```

**Do not** add an `elif` branch to `core/tool_exec.py`. A new harness tool is a
`ToolSpec` plus tests. A tool that needs a mixed read/write verb can supply a
`classifier`; an exception in it fails safe to the declared class.

### 19.4 Parallel tool calls

Dispatch is decided per **call**, not per turn:

- Unrelated reads and writes become `(kind, path)` effects and may overlap.
- Writes never overlap each other.
- A read/write pair whose paths are equal or nested keeps model order.
- `exec`, a native call, an approval prompt, a denied call, a path-less write,
  and a repeated call are **fences**: earlier calls finish, the fence runs
  alone, then dispatch resumes.
- Results are appended in submission order.
- A cancelled turn still writes one `tool` message per `tool_call`
  (`CANCELLED_TOOL_OUTPUT`).

`--max-parallel-tools` (default 4; `1` disables) caps concurrency.

### 19.5 Subagents

`run_subagent` is a `native` fence handled in `agent/subagent.py`. The child:

- shares the parent's runtime (policy, approvals, workspace, cancel event) and
  completer,
- gets a filtered tool surface that never contains `run_subagent`,
  `run_subagent_batch`, or `list_subagents`,
- has a fresh context, no session row, and a turn budget clamped to 12,
- returns only its final report; its transcript is discarded.

A child cannot delegate again. A child exception becomes tool text and does not
abort the parent. `run_subagent_batch` parallelizes **read-only** children;
mutations stay sequential.

### 19.6 Cancellation

Pass `cancel=runtime.cancel_event` to `AgentLoop`. A new turn calls
`reset_cancel()`. The flag is checked between calls; in-flight writes and child
processes are allowed to finish. Never kill the worker thread.

---

## 20. Permissions and approvals

`agent_permissions.json` is the single policy file. It is user data and is
gitignored; only `config/agent_permissions.example.json` is tracked.

### 20.1 Discovery order

1. Explicit path (`--permissions`, `PermissionPolicy.load(path)`).
2. `BRAHMA_AGENT_PERMISSIONS`.
3. `%LOCALAPPDATA%\BrahmaAI\config\agent_permissions.json`.
4. `config/agent_permissions.json` in the repo.
5. Built-in defaults.

Missing files are fine. A **malformed** file raises at load: a typo must fail
loudly, not at the moment an agent tries to delete something.

### 20.2 Schema

```json
{
  "version": 1,
  "default": "allow",
  "classes": {
    "read": "allow",
    "write": "allow",
    "exec": "ask",
    "network": "allow",
    "device": "allow",
    "mcp": "allow",
    "native": "allow"
  },
  "tools": {
    "run_command": "deny",
    "write_file": { "decision": "ask", "reason": "repo is read-only today" }
  },
  "paths": {
    "outside_workspace": "ask",
    "protected": "ask",
    "trusted_roots": []
  },
  "protected_globs": [".git/config", ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*.pfx"],
  "exec": {
    "deny_patterns": ["rm\\s+-[a-z]*r[a-z]*f[a-z]*\\s+/(?:\\s|$)"],
    "allow_patterns": [],
    "sandbox": {
      "prefix": [],
      "require_confined": false,
      "env_scrub": true,
      "env_keep": [],
      "max_memory_mb": 0,
      "max_processes": 0,
      "max_cpu_seconds": 0
    }
  },
  "headless": {
    "exec": "deny",
    "outside_workspace": "deny"
  }
}
```

Decisions are `allow`, `ask`, or `deny`. `tools.<name>` accepts a bare string or
`{"decision": ..., "reason": ...}`. Partial documents merge over defaults,
including the nested `exec.sandbox`.

### 20.3 Resolution order (first match wins)

1. `tools.<name>` — explicit per-tool rule. An `ask` here still becomes `deny`
   when no approver exists.
2. **Hard deny** — an `exec` command matching `exec.deny_patterns`
   (`allow_patterns` short-circuits first). This is a narrow tripwire for
   catastrophic commands, not a sandbox.
3. **Path scoping** — every path-bearing argument is resolved against the
   workspace:
   - outside the workspace → `paths.outside_workspace` (or
     `headless.outside_workspace`),
   - matching a protected glob → `paths.protected`.
4. `headless.<class>`, when no human can be asked.
5. `classes.<class>`.
6. `default`.

Finally, a `requires_approval` spec escalates `allow` → `ask` (and `deny` when
headless).

### 20.4 `ask` vs `deny`

- **`ask`** goes to the approver. The model is told "a human must approve this
  before it runs; do not claim it happened." A timeout, a missing UI, or a
  cancel is a denial.
- **`deny`** is final. The model is told "hard policy denial; do not retry."

### 20.5 Headless

`AgentRuntime.decide` computes `headless = self.headless and self.approver is
None`. The hosts differ only in what they inject:

| Host | `headless` | `approver` | Effect |
|---|---|---|---|
| GUI | `False` | HUD banner | Interactive; `exec` asks. |
| CLI on a TTY | `False` | stdin prompt | Interactive; `exec` asks. |
| CLI `--json` / CI | `True` | `None` | `exec` denied. |

The GUI approver refuses to run on the Qt main thread; it must be reached from a
worker thread (`asyncio.to_thread` in `core/tool_exec.py`, or the text path's
worker). A new caller that violates this silently turns the approval into a
denial.

### 20.6 Hot reload

`AgentRuntime.execute` calls `reload_policy()` first. The check is throttled
(`policy_check_interval`, default 1 s) and compares `st_mtime_ns`. A malformed
edit keeps the last good policy and emits `policy_reload` with `ok=False`. A
reload that turns `ask` into `deny` takes effect immediately; a remembered
`ALWAYS ALLOW` cannot widen it.

### 20.7 Session approvals

`AgentRuntime.session_allow` is in-process only. It is keyed by:

- the **exact command** for `run_command`,
- the **resolved path** for path-based asks,
- the **tool name** for other file/native asks.

It is consulted only after `evaluate()` and only when the decision was `ask`. It
never overrides a `deny` and never applies to `requires_approval`. Nothing is
written to disk; to make an approval permanent, edit the policy.

---

## 21. Command containment and `exec.sandbox`

`run_command` runs through `agent.process.run_captured`:

- `stdin` is closed, so no prompt can block an agent call.
- On timeout, the whole process tree is terminated: a Windows job object, or a
  POSIX session group, with `taskkill /F /T` and `Popen.kill()` as fallbacks.
- Normal completion does **not** kill background survivors.
- Timeout is clamped at the tool (1 s to 1800 s).

`agent/sandbox.py` is the only isolation path. It reads `exec.sandbox` and
produces the env and argv that `run_captured` uses. Do not add a second scrub,
prefix, or limit path in a tool.

| Control | Default | Behavior |
|---|---|---|
| `env_scrub` | `true` | Withholds variables whose underscore-delimited name contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `PASS`, `CREDENTIAL(S)`, `DATABASE_URL`, `MYSQL_PWD`, or `PGPASSWORD`. Boundaries matter: `TOKENIZERS_PARALLELISM` and `KEYBOARD` are kept. A withheld count is reported in a `[sandbox]` note. |
| `env_keep` | `[]` | Exact names to pass through (case-insensitive), e.g. `GH_TOKEN`. |
| `prefix` | `[]` | Trusted argv prepended to the command. The command is one argument and no shell is used; a prefix should end in `sh -c` / `cmd /c`, or use `{command}`. `{cwd}` is substituted. |
| `require_confined` | `false` | Makes `sandbox doctor` fail if confinement is not demonstrated. |
| `max_memory_mb` | `0` | Job-object memory limit (0 = unlimited). |
| `max_processes` | `0` | Job-object process count; counts the shell itself. POSIX cannot enforce it. |
| `max_cpu_seconds` | `0` | Job-object CPU limit; POSIX uses `resource.prlimit` where available. |

A limit that cannot be applied is reported in the tool output, not silently
ignored.

There is deliberately **no built-in filesystem or network sandbox**. Windows has
no portable stdlib primitive for one, and an AppContainer path fails silently
against a normal Python install. Configuring `prefix` (for example a container)
is the supported way to get real confinement. `python -m agent sandbox doctor`
probes it.

**Containment bounds lifetime, not access.** The permission policy remains the
gate on what a command may read, write, or reach.

---

## 22. Sessions, storage, and resume

Sessions are additive tables in the existing
`%LOCALAPPDATA%\BrahmaAI\config\workspace_store.sqlite3`:

```sql
agent_sessions(id, title, cwd, model, status, summary, turn_count, created_at, updated_at)
agent_messages(id, session_id, seq, role, payload_json, created_at)
agent_events(id, session_id, seq, kind, payload_json, created_at)
```

A message is stored as JSON because the payload *is* the protocol:
`tool_calls`, `tool_call_id`, and `name` must round-trip exactly or a resumed
session fails its first follow-up request.

`AgentSession.replace_messages()` is called after compaction, so the stored
transcript is the compacted one. Resume is O(summary + recent), not O(everything
ever said).

```powershell
python -m agent sessions --limit 10
python -m agent resume <session-id> "continue"
```

The CLI reopens the session, reuses its `cwd` when `--cwd` is absent, rebuilds
the system prompt, and continues appending to the same transcript. The system
prompt is never persisted; only the new tail is appended.

GUI rules:

- The first agentic turn creates a row against the runtime workspace.
- Later turns seed `[fresh system, stored history, new user]`.
- Voice turns are archived as cleaned user/assistant pairs; the live socket is
  never re-seeded from disk.

---

## 23. The event stream

Every event is a dict with `type` plus fields. The same objects go to
`on_event`, the runtime's `event_sink`, `agent_events`, and `--json` stdout.
Treat this as a public contract.

| `type` | Fields | When |
|---|---|---|
| `session_start` | `session_id`, `cwd` | Once, if a session is attached. |
| `turn_start` | `turn`, `chars` | Before each model call. |
| `assistant` | `turn`, `text`, `tool_calls`, `usage?` | After each model response. |
| `tool_call` | `name`, `call_id`, `arguments`, `repeat` | Before dispatch. |
| `tool_start` / `tool_end` | `name`, `call_id`, `args` / `chars` | Around the handler. |
| `tool_result` | `name`, `call_id`, `ok`, `chars`, `preview` | After truncation. |
| `permission` | `name`, `decision`, `rule`, `reason` | When a call was gated or served from a session approval. |
| `compaction` | `dropped`, `summary_chars`, `total_chars` | After folding. |
| `retry` | `attempt`, `delay_s`, `error` | Before a retry wait. |
| `delta` | `turn`, `kind`, `text` / `index`, `id?`, `name?`, `arguments?` | Only while streaming. |
| `stream_fallback` | `reason` | Once per run when streaming was requested but unavailable. |
| `policy_reload` | `ok`, `source`, `error` | When an edited policy was adopted or rejected. |
| `instructions` | `loaded[labels]`, `chars`, `truncated` | When a touched directory added instructions. |
| `subagent_start` | `subagent`, `task`, `tools`, `max_turns`, `template?` | Before a child loop. |
| `subagent_event` | `subagent`, `event` | A relayed child event. |
| `subagent_end` | `subagent`, `stop_reason`, `turns`, `chars` | After the child stops. |
| `error` | `error` | Loop-level fatal. |
| `stop` | `reason`, `turns`, `tool_calls` | Last event of every run. |

---

## 24. Prompt and instruction discovery

`agent/prompts.py` keeps the resident prompt short: the load-bearing rules
(read before writing, prefer edit over rewrite, verify with the project's own
command, never invent tool output, stay in the workspace, do not repeat a failing
call, finish with a plain summary), the workspace path, and a one-line
permission note.

Project instructions are appended **last** so they win:

- `AGENTS.md`, `BRAHMA.md`, and `.brahma/AGENTS.md` are read from the workspace
  and up to **three parent directories** (root-most first).
- The drive/filesystem root is never included.
- `BRAHMA_AGENT_INSTRUCTION_PARENTS=0` disables the walk.
- The combined text is capped at 8,000 characters; the **tail** is kept.
- The harness never edits these files on its own.

Subdirectory instructions are **path-driven**: after tool results are appended,
the loop collects path-bearing arguments and walks only the directories between
the workspace and the path, shallowest first. Reading
`packages/foo/app.py` loads `packages/AGENTS.md` and
`packages/foo/AGENTS.md`, and nothing else. Vendored/generated directories
(`node_modules`, `.git`, `dist`, `build`, `.venv`, `site-packages`, …) are
skipped. The walk stops after 12 levels, a file is capped at 4,000 characters,
and each run has a 6,000-character budget. Each file is loaded once per run.

The text is appended to the **system prompt** for the next request, not inserted
as a message, so the OpenAI tool protocol stays intact. Discovery is per run,
not persisted; a resumed session re-discovers as it touches paths again.

Every surface gets the same project blocks: `project_context(workspace)` is
appended by the CLI/subagent prompt builder, by `main.py`'s agentic seed, and by
the Gemini Live system instruction. Project rules still outrank host extras.

Instruction files are **trusted input**. A cloned third-party tree that ships an
`AGENTS.md` speaks to the model with project authority. Review unfamiliar trees
before letting an agent work inside them.

---

## 25. Two skill systems

| | Core Skills Library | Project skills |
|---|---|---|
| Module | `core/skills_library.py` | `agent/skills.py` |
| Tools | `search_skills`, `load_skill`, `list_skills` | `load_project_skill` |
| Roots | builtin, user, then project `.brahma/skills` (highest precedence) | `.brahma/skills` in workspace + 3 ancestors + user dir |
| Shapes | instructional **and** executable (`executor.py`) | instructional only |
| Trust | user-installed; executable skills are code | trusted input like `AGENTS.md`; `allowed-tools` advisory |

**One store, two doors, two names.** Do not unify the tool names:
`core/tool_exec.py` runs the harness branch first, so a harness `load_skill`
would shadow the library tool on voice and GUI.

Discovery roots for project skills:

1. `BRAHMA_SKILLS_DIR` (an `os.pathsep`-separated override).
2. `.brahma/skills` in the workspace, then up to three ancestors.
3. `%LOCALAPPDATA%\BrahmaAI\skills` (or `~/.config/brahma/skills`).

The nearest definition wins; a shadowed copy is a diagnostic. A malformed
`SKILL.md` is reported by `python -m agent skills` and skipped; one bad file
never disables the rest. The resident pass reads at most 8 KB per file and caps
the block at 24 skills / 4,000 characters; the loader reads the full body up to
256 KB.

---

## 26. Subagents

Specialist templates are vendored in `subagents/` (`catalog.json` plus ~600
bodies). The Obsidian vault they were ported from is only a legacy fallback and
must never be required. `BRAHMA_SUBAGENT_LIBRARY` or `config/subagents.json`
overrides the bundled tree; an empty value disables it.

Disclosure is tiered:

1. A ~1.4 KB category index in the resident prompt.
2. `list_subagents` for slugs and descriptions.
3. `run_subagent(subagent_type=…)` for exactly one body (clamped to 24 KB).

Never put template bodies in the resident prompt or add a second discovery path.
The index is machine config, so it is appended by
`build_system_prompt(include_subagents=True)`, not by `project_context`.

Runtime rules:

- A child shares the parent's runtime and completer; it cannot choose a
  different model or provider.
- The child surface never contains `run_subagent`, `run_subagent_batch`, or
  `list_subagents`.
- A child has a fresh context, no session row, and a 12-turn clamp.
- Only the final report crosses back; the transcript is discarded. Ask for
  specific findings rather than "investigate".
- `run_subagent_batch` fans out read-only children in parallel; mutations stay
  sequential.
- A child exception becomes tool text and does not abort the parent.

The voice surface advertises `list_subagents` but omits the dispatch tools: the
Gemini Live session has no text completer to build a child loop. Its prompt
carries the same library index with `dispatch_available=False`.

---

## 27. MCP: client and server

### 27.1 Client

`agent/mcp.py` is the only MCP client in the harness. It is stdlib-only and
speaks newline-delimited JSON-RPC over stdio (`initialize`, `tools/list`,
`tools/call`, plus resources/prompts). Do not reuse `core/mcp_manager.py` (SDK +
asyncio) or spawn a server from a tool.

Config is trusted input: servers are arbitrary programs the user named. Add a
field only with validation in `parse_servers`. `env` merges over the inherited
environment; a relative `cwd` resolves against the config file.

Every server tool registers as an ordinary `ToolSpec` (`tool_class="mcp"`, name
`mcp_<server>_<tool>` sanitized to `[A-Za-z0-9_]`), so calls go through
`AgentRuntime.execute` like any other tool. `MCPSession.close()` unregisters the
specs before killing the process.

Config discovery: `--mcp-config` / `BRAHMA_MCP_CONFIG` / `.brahma/mcp.json` /
`.mcp.json`.

The GUI uses one `WorkspaceMCP` (`_harness_mcp_manager()` in `main.py`), keyed by
workspace + config identity and closed in `main()`'s `finally`. Merge its tools
with `with_mcp_tools` so the first model call advertises them.

A server that latched `tools/list_changed`, lost its transport (`closed`), or
whose process is gone is refreshed at the turn boundary: `WorkspaceMCP.ensure`
calls `MCPSession.refresh`, which re-lists that server only and restarts a dead
one once. No background watcher and no per-turn retry loop. A failed re-list
drops that server's tools and reports the stderr tail once. Check `closed` as
well as `alive`: the reader thread sees EOF before `poll()` does.

### 27.2 Server

`agent/mcp_server.py` exposes Brahma outward. See §12. It is versioned `1.1.1`
and routes every tool call through `AgentRuntime.execute`.

---

## 28. CLI reference

```
python -m agent run "fix the failing test" --cwd . --provider auto
python -m agent resume <session-id> "now run the suite"
python -m agent sessions --limit 10
python -m agent tools --json
python -m agent skills [--cwd DIR] [--json]
python -m agent subagents [--json]
python -m agent mcp list [--config PATH] [--cwd DIR] [--json]
python -m agent permissions show | init [--path ...] [--force]
python -m agent sandbox doctor [--cwd DIR] [--permissions PATH]
python -m agent mcp-server --cwd DIR
```

A bare prompt is treated as `run`:

```powershell
python -m agent "add a test for parse()" --cwd .
```

### 28.1 Common flags (`run` and `resume`)

| Flag | Default | Notes |
|---|---|---|
| `--cwd` | cwd | Workspace root; containment is enforced against it. |
| `--provider` | `auto` | `auto \| local \| custom \| openrouter \| stub`. `auto` = local → custom → openrouter. |
| `--model` | provider default | |
| `--stream` | off | Render text as it arrives; falls back when the provider has no streaming transport. |
| `--max-turns` | 40 | Loop hard stop. |
| `--repeat-limit` | 3 | Identical-call threshold. |
| `--max-parallel-tools` | 4 | Concurrent tool calls when paths do not conflict; `1` disables. |
| `--retries` | 3 | Attempts per model call on a transient failure. |
| `--max-context-chars` | 120000 | Compaction threshold. |
| `--timeout` | 120 | Per-command tool timeout. |
| `--json` | off | NDJSON events on stdout, one object per line, then `{"type": "result", ...}`. Implies headless. |
| `--headless` | auto | Never prompt; non-TTY stdin implies this. |
| `--yolo` | off | Allow every class and path rule. Sandbox only. |
| `--allow` / `--deny` | — | Override classes, repeatable/comma-separated. |
| `--permissions` | discovery | Policy file path. |
| `--mcp-config` | discovery | MCP server config. |
| `--no-mcp` | off | Do not start configured MCP servers. |
| `--no-session` | off | Do not persist. |
| `--summarize` | off | Use the model for compaction summaries. |
| `--title` | prompt[:60] | Session title. |

### 28.2 Exit codes

| Code | Meaning |
|---|---|
| `0` | Completed. |
| `1` | Any non-`completed` stop (turn cap, repeat, permission denial, error). |
| `2` | No provider reachable. |
| `3` | Usage error. |
| `130` | Interrupted (second Ctrl-C, or Ctrl-C while already cancelling). |

`--provider stub` exists so the CLI and loop can be tested without a model.

### 28.3 Seeding a policy

A clone runs on built-in defaults: read/write allowed, `exec` asked (denied when
headless), outside-workspace and protected files asked, catastrophic commands
denied. No file has to exist.

```powershell
python -m agent permissions init --path "$env:LOCALAPPDATA\BrahmaAI\config\agent_permissions.json"
```

`init` refuses to overwrite an existing file; pass `--force` when you mean it.

### 28.4 CLI colour

Colour is off unless the destination is a TTY. `NO_COLOR` (any value),
`TERM=dumb`, and redirected stderr keep the exact plain prompt.
`FORCE_COLOR=1` overrides the TTY check. On Windows, `agent/term.py` enables VT
processing first; a legacy console gets plain text. The plain branch is
byte-identical to the historical prompt.

---

## 29. Security model and audit

The security posture is layered, and it is honest about what is *not* enforced:

1. **Policy** — the permission engine described in §20 is the only execution
   boundary for harness tools. Session approvals cannot widen a `deny`.
2. **Approvals** — the HUD shows the rule, reason, exact command, `cwd`, and a
   real diff before a gated call runs. `requires_approval` asks every time.
3. **Containment** — closed stdin, whole-tree timeout, env scrubbing, optional
   prefix, resource limits (§21). Lifetime, not access.
4. **Secrets** — DPAPI encryption at rest; atomic JSON writes; hashed API
   tokens.
5. **Egress** — provider kill switches gate every call before it leaves; the
   gateway defaults to loopback and paired-device auth.
6. **Supply chain** — the release manifest is Ed25519-signed and verified
   fail-closed; plugins are loaded from `plugins/` with allowlist verification.
7. **Audit** — `agent_events` and the API audit log record what ran; the CLI's
   `--json` is the machine-readable form.

What is **not** true:

- There is no built-in filesystem or network sandbox for `run_command`; the
  policy and an optional `prefix` are the barrier.
- `--yolo` is equivalent to "run me in a container". Do not use it on a machine
  you care about.
- The legacy native tool path is scoped, not universally policy-gated (the
  harness path is). Prefer harness `ToolSpec`s for anything new.
- Instruction files and project skill bodies are trusted input.
- The gateway is a device surface, not a peer-agent surface.

The security remediation history is tracked in `docs/REMEDIATION-TRACKER.md`
(`FIND-001`–`FIND-063`); the map is `ARCHITECTURE-SPEC.md` §7.

---

## 30. Gates, testing, packaging, and release signing

### 30.1 Environment

Use Python 3.11 for every gate:

```powershell
py -3.11 -m ruff check .
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
```

The full suite takes about 95 seconds and needs the offscreen Qt platform. The
fast harness subset is:

```powershell
py -3.11 -m pytest tests -q -k agent
py -3.11 -m agent run "say hello" --provider stub --json --no-session
```

`agent/` is stdlib-only and is exercised on 3.11, 3.12, and 3.13 by the
`harness-pythons` CI job with only pytest installed. That is a deliberate canary
against GUI, provider SDK, or Windows COM imports leaking into `agent/`.

### 30.2 Tests that matter

| File | Covers |
|---|---|
| `test_agent_permissions.py` | Defaults, headless escalation, path escapes, protected files, denylist, malformed policy. |
| `test_agent_code_tools.py` | The six code primitives, ambiguity, binary refusal, timeouts, runtime gating. |
| `test_agent_process.py` | `run_captured`: timeout tree-kill, closed stdin, prefix, env withholding, memory limit. |
| `test_agent_sandbox.py` | `exec.sandbox` validation and defaults. |
| `test_agent_loop.py` | Multi-turn continuation, protocol integrity, repeat stop, turn cap, denial, approval, cancellation, events. |
| `test_agent_session.py` / `test_agent_session_bridge.py` | Round-trip incl. `tool_calls`, listing, compaction rewrite, GUI seed. |
| `test_agent_cli.py` | Stub run, stdin prompt, NDJSON shape, exit codes. |
| `test_agent_parallel.py` | Path-dependency scheduling and fences. |
| `test_agent_subagent.py` / `test_agent_subagent_library.py` | Child isolation, no recursion, template disclosure. |
| `test_agent_mcp.py` | Client lifecycle, tool registration, refresh. |
| `test_confirmation_overlay.py` | HUD banner, ALWAYS ALLOW gating, diff folding. |

The `test_agent_*` files are pure Python and need neither a display nor PyQt;
only `test_confirmation_overlay.py` constructs a Qt widget (offscreen).

### 30.3 Packaging

```powershell
.\build_all.ps1
```

This writes `dist\BrahmaEvo_Setup.exe` and `dist\SHA256SUMS.txt`, along with the
standalone app bundle, uninstaller, CycloneDX SBOM, and signed release manifest.
`core/release_manager.py` verifies the Ed25519 signature fail-closed against
`PINNED_RELEASE_PUBLIC_KEY`; an unsigned or tampered manifest is rejected.

### 30.4 Definition of done for a change

1. A `ToolSpec` (not an `elif`) for a new harness tool, plus tests.
2. No bypass of `AgentRuntime.execute`.
3. OpenAI tool protocol preserved in any transcript.
4. `agent/__init__.py` still empty; `python -m agent` imports no PyQt.
5. `agent_permissions.json` is user data and is not committed.
6. CLI colour goes through `agent/term.py`.
7. The project's own gates pass — do not assert success.

---

## 31. Data paths and configuration files

User data root: `%LOCALAPPDATA%\BrahmaAI`

| Path | Contents |
|---|---|
| `config\workspace_store.sqlite3` | Conversations, messages, memories, agent sessions/events. |
| `config\identity.json` | Owner/assistant identity, behavior mode, proactive flag, custom instructions. |
| `config\api_keys.json` | DPAPI-encrypted provider keys. |
| `config\agent_permissions.json` | Optional user policy (discovered after env/explicit). |
| `config\app_settings.json` | UI and app settings (tool index, theme, startup, attention prompts, etc.). |
| `config\brahma_api.json` | OpenAI-compatible API config (checkout `config/`). |
| `config\brahma_api\tokens.json` | SHA-256 hashes of API bearer tokens. |
| `config\brahma_connect.json` | Gateway host/port/enabled/advertise (checkout `config/`). |
| `config\brahma_connect\devices.json` | Paired devices. |
| `memory\long_term.json` | Semantic facts and preferences. |
| `staged_skills\` | Skills Forge drafts awaiting review. |
| `agent_artifacts\` | Large agent outputs referenced by `agent-out://`. |
| `tool_routing.json` | Tool-description collision overrides. |
| `skills\` | User-installed skills. |

Repo-local, tracked files:

| Path | Purpose |
|---|---|
| `config/agent_permissions.example.json` | Policy template. |
| `config/brahma_api.example.json` | API config template. |
| `config/subagents.json` | Optional subagent-library override. |
| `subagents/catalog.json` | Vendored specialist index. |
| `skills/` | 12 repo project skills. |
| `skills_library/` | 3 core skills. |

An `agent_permissions.json` in the repo is gitignored; only the example is
tracked. API keys and tokens are never committed.

---

## 32. Known limitations and the roadmap

These are deliberate, not surprises. Read them before promising a capability.

### 32.1 Harness limits

- **OpenRouter tools** go through `llm_client._openai_chat_completion`; the
  adapter depends on that private transport. If it changes shape, fix
  `agent/providers.py` first.
- **GUI approvals need a worker thread.** A caller on the Qt main thread
  silently turns an approval into a denial.
- **Instruction discovery follows named paths.** A convention that only matters
  through an import the model has not mentioned is not loaded until it is.
- **Instruction files and project skills are trusted input.**
- **Project skills are instructional, not executable.** `allowed-tools` is
  advisory; the policy is the only execution boundary.
- **Streaming is wired only where a transport exists** (local, custom,
  OpenRouter, stub; not Gemini Live, which has its own delivery).
- **Path analysis is conservative, not a sandbox.** `exec`, native tools,
  approvals, and path-less writes are fences.
- **Subagents share the parent's model and are sequential** for mutations; a
  child cannot delegate again and its report is lossy by design.
- **`run_command` is `shell=True` by default.** There is no built-in filesystem
  or network sandbox; configure `prefix` for real confinement.
- **Voice turns are archived, not replayed.** A dropped socket loses the turn.
- **Windows-first.** The code is portable Python, but the denylist, paths, and
  host integration are Windows-shaped.

### 32.2 Product/roadmap status

| Item | Status |
|---|---|
| Three doors (voice, text, CLI) | **Implemented.** |
| `agent_task` autonomous executor | **Retired**; replaced by `AgentLoop` + subagents. |
| Harness primitives + native ToolSpecs | **Implemented** (84 desktop specs, 0 `elif`). |
| Permissions, approvals, containment | **Implemented.** |
| Sessions, resume, voice archival | **Implemented.** |
| OpenAI-compatible API (Phases 0–2) | **Implemented.** Phase 3 is gated. |
| Outbound MCP server (tools/prompts/resources) | **Implemented** (v1.1.1). |
| Agent & Orchestrator Bridge (ADR-018) | **Implemented**; Tier 3 is a client only. |
| Device gateway / Mobile Connect | **Implemented** (loopback, paired-device auth). |
| Peer/LAN Brahma swarm | **Implemented & Verified (S0–S5 & Artifact Lane).** BTE-1 server (`brahma_api/peer.py`), DPAPI/Ed25519 identity, SAS pairing, leases with UNKNOWN reconciliation, token binding, dual-listener SPKI-pinned TLS on 8443, content-addressed artifacts, LAN discovery, and dynamic capability routing (`core/peer/capabilities.py`, `RemoteAgentAdapter`). |
| Capability-maximization roadmap | **Analysis/proposal only.** |
| Rebrand to "Abhasa" (ADR-029) | **Planned.** Not executed. |
| AppContainer sandbox | **Rejected for now** (fails silently on a normal Python install). |
| Upstream port | **Planned**, selective. |

If a document or a demo claims a "capability score" or the Abhasa
name, check the code first. The honest current answer is: **three
doors, one policy boundary, a bridge to external agents, and a verified
core peer-federation layer (BTE-1 tasks, SAS pairing, lease reconciliation, and origin isolation)
connecting Brahma nodes as peers.**

---

## Appendix — Where to read next

1. `docs/ARCHITECTURE-SPEC.md` — the system map, status vocabulary, invariants,
   security workstream, and ranked work queues.
2. `docs/AGENTIC-HARNESS.md` — the full harness contract: loop, tools,
   permissions, sessions, events, prompts, tests, limitations.
3. `agent/runtime.py` — the boundary everything depends on.
4. `agent/loop.py` — the actual agent behavior.
5. `agent/permissions.py` — why a call was refused.
6. `agent/code_tools.py` — what the model can physically do.
7. `core/tool_index.py` — the advertised tool surface.
8. `config/agent_permissions.example.json` — the policy template.
9. `docs/SWARM-FEDERATION-INVESTIGATION.md` — what a swarm would require, and
   why it does not exist yet.
10. `docs/REMEDIATION-TRACKER.md` — the security finding ledger.

---

*This guide describes v1.1.1 at commit `f17d7c9`. When the code changes, update
this file in the same batch — do not let it drift back into aspiration.*
