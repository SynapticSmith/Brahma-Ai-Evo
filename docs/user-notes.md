# User Punchlist & Feedback Ledger

All items below have been investigated, fixed in code, tested in CI, and documented in `docs/USER-GUIDE.md` and `docs/ARCHITECTURE-SPEC.md`.

- [x] **Orca ADE Orchestrator Probe Crash**: Fixed string formatting in `run_captured` (`cwd=Path.cwd()`), guarded thread exceptions in `core/agent_bridge/adapters/orchestrator.py`.
- [x] **Clipboard Monitor Feedback**: Added `tool_index.get_index().is_disabled("clipboard_processor")` check so unchecking the tool halts audio feedback.
- [x] **Skills Library in Standalone Installer**: Added `skills_library` asset bundling to `installer/BrahmaEvo.spec` and enabled multi-root discovery across `skills/` and `.brahma/skills/`.
- [x] **Voice & Avatar Setup Streamlining**: Exposed Gemini Live voice selector (`Charon`, `Puck`, `Aoede`, `Fenrir`, `Kore`) in Settings Hub with zero-developer configuration.
- [x] **Behavior Mode Customization**: Added persistent `Custom Instructions` multi-line editor in Settings Hub under Identity & Profile.
- [x] **Chat GUI Truncation & Streaming Latency**: Removed text clamping from result cards, implemented adaptive typing animation (<=350ms cap), and added instant click-to-reveal on chat bubbles.
- [x] **Auto-Heal Sentry Verification**: Validated AST verification, atomic backup, and one-click rollback in `tests/test_auto_heal_engine.py`.
- [x] **Modular External Memory Subsystem**: Built modular connectors (`Local`, `Mem0`, `Letta`) in `memory/connectors/` with DPAPI encryption and live recall test button.
- [x] **Proactive Comments Control**: Added `proactive_voice_comments_enabled` toggle in Settings Hub under Speech Engines (`core/ui_connectivity.py`, `core/audio_io.py`).
- [x] **Permission Overlay Sizing & Chat Spacing**: Relaxed height constraints on `ConfirmationOverlay` in `core/ui_overlays.py` and improved spacing/margins in `core/ui_chat.py`.
- [x] **Chat File Attachment Handling**: Handled drag-and-drop file attachments in Live chat sessions without disconnecting the WebSocket audio loop (`core/ui_runtime.py`, `core/audio_io.py`).
- [x] **Memory Modules Setup Documentation**: Documented step-by-step setup for Local, Mem0, and Letta in `docs/USER-GUIDE.md` Section 6.
- [x] **Exhaustive Documentation Catch-up**: Updated `docs/USER-GUIDE.md`, `docs/ARCHITECTURE-SPEC.md`, and `docs/OPENAI-COMPATIBLE-API-DESIGN.md` covering all newly landed subsystems. 