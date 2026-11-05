# User Punchlist &amp; Feedback Ledger

All items below have been investigated, fixed in code, tested in CI, and documented in `docs/USER-GUIDE.md` and `docs/ARCHITECTURE-SPEC.md`.

- [x] **Orca ADE Orchestrator Probe Crash**: Fixed string formatting in `run_captured` (`cwd=Path.cwd()`), guarded thread exceptions in `core/agent_bridge/adapters/orchestrator.py`.
- [x] **Clipboard Monitor Feedback**: Added `tool_index.get_index().is_disabled("clipboard_processor")` check so unchecking the tool halts audio feedback.
- [x] **Skills Library in Standalone Installer**: Added `skills_library` asset bundling to `installer/BrahmaEvo.spec` and enabled multi-root discovery across `skills/` and `.brahma/skills/`.
- [x] **Voice &amp; Avatar Setup Streamlining**: Exposed Gemini Live voice selector (`Charon`, `Puck`, `Aoede`, `Fenrir`, `Kore`) in Settings Hub with zero-developer configuration.
- [x] **Behavior Mode Customization**: Added persistent `Custom Instructions` multi-line editor in Settings Hub under Identity &amp; Profile.
- [x] **Chat GUI Truncation &amp; Streaming Latency**: Removed text clamping from result cards, implemented adaptive typing animation (&lt;=350ms cap), and added instant click-to-reveal on chat bubbles.
- [x] **Auto-Heal Sentry Verification**: Validated AST verification, atomic backup, and one-click rollback in `tests/test_auto_heal_engine.py`.
- [x] **Modular External Memory Subsystem**: Built modular connectors (`Local`, `Mem0`, `Letta`) in `memory/connectors/` with DPAPI encryption and live recall test button.
- [x] **Proactive Comments Control**: Added `proactive_voice_comments_enabled` toggle in Settings Hub under Speech Engines (`core/ui_connectivity.py`, `core/audio_io.py`).
- [x] **Permission Overlay Sizing &amp; Chat Spacing**: Relaxed height constraints on `ConfirmationOverlay` in `core/ui_overlays.py` and improved spacing/margins in `core/ui_chat.py`.
- [x] **Chat File Attachment Handling**: Handled drag-and-drop file attachments in Live chat sessions without disconnecting the WebSocket audio loop (`core/ui_runtime.py`, `core/audio_io.py`).
- [x] **Memory Modules Setup Documentation**: Documented step-by-step setup for Local, Mem0, and Letta in `docs/USER-GUIDE.md` Section 6.
- [x] **Exhaustive Documentation Catch-up**: Updated `docs/USER-GUIDE.md`, `docs/ARCHITECTURE-SPEC.md`, and `docs/OPENAI-COMPATIBLE-API-DESIGN.md` covering all newly landed subsystems.

---

- when using the api to call brahma, i have notice that: 1. the local model is polled only. no matter which provider gateway is set in the apps settings, or what the fallback sequence is set to. (i understand this might be the default configuration, but i'd like to know if we can make it respect the set up chose provider, and fallback sequence if any)  
  
  
2 the sessions doesn't have access to tools like websearch, (i tried the worker api tier) here is a log of our conversations:   
  
"hi how are you

Hey! I'm doing great, thanks for asking. 😊 Just here and ready to help you with whatever you need — whether it's coding, research, browser stuff, or anything else. What's on your mind today?

get the latest news about the coup of the constitutional tribunal in poland

I don't have live web access, so I can't pull the very latest headlines or confirm whether a coup-related move involving Poland's Constitutional Tribunal just happened. My knowledge has a cutoff, and I wouldn't want to give you outdated or made-up details.

If you'd like, I can:

•Summarize what I know from my training about Poland's Constitutional Tribunal (e.g., its role, past controversies, legal challenges) up to my last update.

•Help you draft a search query or set up a reminder to check current sources.

•Assist with anything else on your screen right now.

Just let me know how you'd like to proceed!

what tools do you have?

Here's a quick rundown of the tools I can use in this workspace:

**File &amp; text operations**

•`read_file` – read a specific file (line ranges optional)

•`write_file` / `edit_file` – create or modify files

•`grep_files` – search for patterns in files

•`glob_files` – find files matching a glob pattern

**Command execution**

•`run_command` – run shell commands (with timeout controls)

•`run_subagent` – delegate a focused task to a bounded child agent (read‑only tools only)

•`run_subagent_batch` – fire off several independent read‑only subagent tasks in parallel

**Project skills**

•`load_project_skill` – load a skill's full instructions (e.g., `api-mock-engine`, `browser-automation`, `code-review`, etc.) from the `.brahma/skills` folder

**Harness / MCP**

•Built‑in `agent/[mcp.py](http://mcp.py)` for MCP server communication (via `initialize`, `tools/call`)

•Workspace tools wired through `WorkspaceMCP` and `agent/[session.py](http://session.py)`

So I can read, edit, search, run scripts, spin up subagents, load domain‑specific skills, and talk to the MCP layer — all within the workspace and respecting the permission policy. Want me to kick off any of these?"  
  


I also tried the operator api tier with similar results. where the ai insists it doesn't have access to webtools.  
  
i might need this explained, as perhaps that is just the consequence of our configurations that we set up.



- there also might be an issue with the timeout limit we set on the local machine response time, in case of some cpu compute depending on the size of the ram, the model, and the context size. the inference time is longer than our limits. is it not possible to set those timeouts to none? and would there be detrimental consequences?
- i tried to get the ai to Spin up a subagent task "look up the latest news in poland about the coup of the constitutional tribunal" (this is through the openai compatible api call to Brahma) i saw the computer do 2 turns of inference, but nothing was reported back. this might be because of our timeout limitations, or worse, because the subagent doesn't feedback the result to brahma, in order for it to respond. again i'm only speculating about the cause. (oh i also suspect it could be that permissions need to be granted, as they do in the main app, but that doesn't go through the api to the session, so it dropped the task as it was considered rejected)
- when the agent does task, there are popup windows on the gui, which is great, but they have an automatic timeout, can we either add a setting for the user to toggle the automatic count down, and set the timeout timer. sometimes its better to have all the popups visibile of what the agent did, and info it got.  
  
