# Brahma-Ai-Evo — Code Review TODO & Work Log

Working reference for the code-review improvement pass. Keep this updated as items land.

## Guardrails for this work stretch
- **Checkpoint / revert point:** git tag `checkpoint/pre-p0-review-fixes` @ `262616428b54afec35b912912b6dbf305e951b7e`
  - Revert any change: `git checkout checkpoint/pre-p0-review-fixes -- <path>` or `git reset --hard checkpoint/pre-p0-review-fixes`
- **Do not push** any commits while this stretch is in progress.
- **Verify after every change:** `py -3.11 -m py_compile <files>`, `py -3.11 -m pytest -q`, and the relevant `accept_*.py` harness.

## Status legend
- `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` independently verified by direct read

---

## P0 — correctness, small + high value

- [x] **P0-1 — Local-mode Disconnect/On Call kills the voice loop.**
  `main.py:5976-5980` awaits `_session_task_guard(...)`; that guard (`main.py:2419-2434`) does **not** absorb
  cancellation, so `except asyncio.CancelledError: raise` propagates out of `run()` and the thread's
  `asyncio.run()` (`main.py:6360`) exits. The Gemini path handles the same case at `main.py:6023-6030` via
  `_take_disconnect_request()`. **Verified [!].**
  Fix (done): inner `try/except asyncio.CancelledError` around the local-branch `_session_task_guard(...)` that
  re-raises unless `_take_disconnect_request()` returns True — mirrors the Gemini guard. Covered by
  `accept_live_toggle.py` ("Disconnect can cancel the local session too" / "shutdown CancelledError propagates").

- [x] **P0-2 — Settings writes are not atomic.**
  `ui.py:9144` did a bare `write_text` (two more sites: `ui.py:16476`, inline fallback `ui.py:11430`);
  `memory/config_manager.py:40-41` did a bare in-place `json.dump`. A crash or a concurrent writer mid-write
  corrupts the JSON (plausible cause of "settings reverted" reports).
  Fix (done): `atomic_write_json(path, data, indent=4)` in `core/user_paths.py` (serialize fully → sibling
  `mkstemp` → write+flush+fsync → `os.replace`, unlink temp on `BaseException`). Wired into all three `ui.py`
  sites and `config_manager.save_settings`. Tests: `tests/test_atomic_settings_write.py` (4).

- [x] **P0-3 — Missing/invalid Gemini key kills the async loop silently.**
  `main.py:5949-5952` built `genai.Client(api_key=_get_api_key())` **before** the loop, so it ran even in
  Local/offline mode; `_get_api_key()` (`main.py:133-135`) raised `KeyError` when the key was absent, and the
  `run()` exited with no visible reason.
  Fix (done): `client = None` + `api_key_warned = False` before the loop; the client is built lazily inside the
  Gemini branch (wrapped so failure logs once via `print` + `_startup_log`, calls
  `_report_voice_status(False, "no-api-key")`, sets `STANDBY`, `await self._pause_until(10)`, `continue`);
  `_get_api_key()` now raises one actionable `RuntimeError` (missing file / empty key) instead of `KeyError`.


---

## P1 — concurrency & lifecycle

- [x] **P1-4 — Lost-wakeup race in reconnect backoff.** `_pause_until` cleared `_control_wake`
  (`main.py:2389`) after the `_should_standby()` check at `main.py:6054`; a GUI-thread wake could be dropped.
  Fix (done): control-sequence counter. `_wake_control` bumps `_control_seq` under `_control_lock`;
  `_pause_until` clears the event, *then* samples `_control_seq` — a change that arrived during the connect
  attempt makes it return immediately (recorded in `_pause_seen`) instead of sleeping the full backoff. The
  counter is read via `getattr` so partial test harnesses keep working.
- [x] **P1-5 — Wake-word listener can double-open the mic / re-arm a disconnected call.** `main.py:2619-2623`
  nulled the handle while the old thread still blocked in `recognizer.listen` (~`main.py:2655`); a stale thread
  could call `set_live_call(True)` (`main.py:2661`).
  Fix (done): `_stop_wakeword_listener` no longer nulls `_wake_thread`/`_wake_stop` (so `_start` sees the old
  thread alive and refuses to double-arm); the worker clears its own slot in a `finally` only if it is still
  the current thread, and re-checks `stop.is_set()`/identity after the blocking `listen` so a Disconnect during
  capture cannot resurrect the session.
- [x] **P1-6 — Duplicate `_relay_phone_audio` consumer** (`main.py:5943` and `main.py:2705`); fire-and-forget
  tasks (`main.py:5936/5942/5943`) with no strong ref / done-callback.
  Fix (done): removed the session-scoped relay (it raced the long-lived one for the same phone-audio queue);
  the single long-lived relay now drops frames while `self.session is None` (no consumer → no unbounded
  queue). Added `_spawn_background(coro, name)` which keeps a strong ref in `_background_tasks` and logs any
  exception, and routed the dashboard / remote-commands / phone-audio tasks through it.
- [x] **P1-7 — MCP `_status`/`_tasks` lock inconsistency.** Written from the loop thread (`_set_state`
  `core/mcp_manager.py:776`) and read from others without a consistent lock.
  Fix (done): `_set_state` now takes `cls._lock` (RLock, so the startup call already holding it is fine);
  `status()` iterates `list(cls._status.items())` so a key added mid-iteration cannot raise.
- [x] **P1-8 — MCP shutdown race.** `mcp_manager.stop_all` (`core/mcp_manager.py:512-521`) waits 5s then
  `loop.stop()` with the task possibly still pending → possible orphaned stdio child. (Correction: normal
  cancellation *does* tear the transport down via `async with connect()` at `mcp_manager.py:595`.)
  Fix (done): `stop_all` cleared `_tasks` before scheduling `_cancel_task(name)`, so the lookup found nothing
  and nothing was ever cancelled — it now cancels the exact task objects captured in the snapshot.
- [x] **P1-9 — MCP circuit breaker.** `_supervise` (`mcp_manager.py:542-566`) retries forever capped at 30 s;
  add a max-retry → "permanently failed" state surfaced in the panel.
  Fix (done): `_supervise` counts consecutive connect failures (reset whenever the server reaches `ready`) and
  parks a never-connecting config in a `failed` state after `_max_connect_failures` (5) attempts. The panel
  already renders the raw state string (`ui.py:11728`), so `· failed` shows without a UI change.

---

## P2 — reliability & error handling

- [x] **P2-10 — `_is_gemini_limit_error` over-broad + sticky.** Fixed. The token list is narrowed to
  genuine throttle/quota signals (`429`, `resource_exhausted`, `resource has been exhausted`, `quota`,
  `rate limit`, `too many requests`, `exceeded`); `"1008"`, `"access denied"`, `"permission denied"` are
  dropped so a bad key is not mistaken for a rate limit. `_use_openrouter_first` is no longer sticky:
  `_throttled_to_openrouter()` arms a `_openrouter_until` cooldown (default 300 s) and all four read
  sites now ask `_prefer_openrouter()`, so a one-off 429 no longer hides a working Gemini for the whole
  session.
- [x] **P2-11 — MCP tool-name collisions unchecked.** Fixed in `core/mcp_manager.py` (the old
  `core/tool_bridge.py` line was stale; registration lives in `_register_server_tools`).
  `_register_server_tools` now tracks sanitized names seen in one `tools/list` response, refuses to
  shadow a name already claimed by a different server, and refuses to shadow a built-in skill —
  logging each skip instead of silently overwriting. Two tools that sanitize alike (`a.b` vs `a-b`)
  now keep the first and skip the second. Covered by `tests/test_mcp_supervise.py`.
- [x] **P2-12 — Unbounded caches.** TTS `_probe_cache` now clears at `_PROBE_CACHE_LIMIT = 16`.
  STT `_model_cache` was already bounded: it `clear()`s before inserting, so it holds at most one
  model and the previous model is released on switch (deliberate, to avoid two resident models).
  No change needed there.
- [x] **P2-13 — Cleanup-path `except Exception: pass`.** `core/mcp_manager.py` loop-shutdown,
  `stop_all` cancel/stop, `unregister_server_tools`, `unregister_all_tools`, and init-instruction reads
  now log at `logger.debug`. `core/avatar_runtime.py` gained a module logger; the three silent
  cleanups (settings read, Live2D/VRM asset scan) log at `logger.debug`.

---

## P3 — testing & CI (highest leverage)

- [x] **P3-14 — CI workflow added.** `.github/workflows/ci.yml` runs on `windows-latest`: a stdlib-only static
  gate job (`precommit_check.py --quick`, same as the local hook) plus a unit-suite job
  (`pip install -r requirements.txt` → `pytest tests -q`, `QT_QPA_PLATFORM=offscreen`). The wake-word/accept
  harnesses stay a local gate (they need a display and audio devices). **Still open (needs a decision):**
  lint/type tool choices (ruff/black/mypy?) and a `pyproject.toml`. The workflow is unexercised until the
  branch is pushed.
- [x] **P3-14b — Dependencies fully pinned.** `requirements.txt` now pins every package to the version verified on
  the ONE-391 host (`==`, 46 pins). `pyttsx3` is deliberately left unpinned: it is imported lazily by
  `core/speech_tts.py` but is not installed on the verification host (the direct-SAPI path covers that engine),
  so no known-good version existed to pin. Pins are top-level only; a full lock would be a follow-up if
  byte-for-byte reproducibility is ever needed.
- [x] **P3-15 — Unmarked `ZeroDivisionError` fixture collected by bare pytest.** `actions/test_action.py` raises
  `ZeroDivisionError` on purpose (self-heal demo) and `test_self_heal.py` re-exports its `test_action`, so a bare
  `pytest` errored. Fixed with `pytest.ini` + `testpaths = tests`: the demo files stay intact for the self-heal
  harness, and bare `pytest` now collects only the real suite (144 tests).
- [x] **P3-16 — Interactive scripts pollute root collection.** Same fix: `testpaths = tests` keeps
  `test_self_heal.py`, `test_local_brain.py`, `test_sensorium_live.py` out of pytest collection (they are not
  pytest tests and can hang).
- [x] **P3-17 — Duplicated `check()` across 7 `accept_*.py`.** Extracted `check`/`section` and the terminal
  RESULT line into `accept_util.py`; the six updated harnesses import it and still print
  `"ALL CRITERIA PASS"` (`precommit_check.py`'s scrape contract unchanged). `accept_391.py` keeps its own
  differently-shaped `check` for now.
- [x] **P3-18 — Install `precommit_check.py` as an actual git hook.** Installed `.git/hooks/pre-commit`
  (per-clone, untracked by design): fast static pass by default, full suite with `PRECOMMIT_FULL=1`.
  Verified with `git hook run pre-commit`.
- [x] **P3-19 — `test_executor_auto_heal_error_recording` reached a live Gemini endpoint (CI blocker).**
  The test mocked `create_plan` and `_call_tool` but not `analyze_error`, so `execute` (agent/executor.py:578)
  called `error_handler.analyze_error` → `_get_api_key()` → `%LOCALAPPDATA%/BrahmaAI/config/api_keys.json`.
  On CI (file absent) that raised `FileNotFoundError` → deterministic test failure; on a dev box (file present)
  it made a real network call → hang with no timeout. Fix (`2d631a0`): monkeypatch
  `agent.executor.analyze_error` (+ `generate_fix` defensively) to return an `ErrorDecision.ABORT` stub, so the
  test still exercises `record_last_error` (the line it actually asserts) but never leaves the process.
  Verified: with `LOCALAPPDATA` pointed at a fresh empty dir the test passes in 0.05s, and the full suite runs
  **149 passed** with no `--deselect`.
- [ ] **P3-20 — Flaky native crash at interpreter shutdown after a green suite (deferred).** The Qt/PyQt6 test
  process occasionally exits `-1073741819` (0xC0000005) or `-1073740940` (0xC0000409) *after* printing
  `N passed` — ~1 run in 3 at HEAD, and the two-file Qt case (`test_tool_visibility_card.py`,
  `test_spotify_settings_card.py`) can crash standalone too; `origin/main` (134 tests) exited 0 in one
  measurement. All failures are teardown-time, no assertion fails, so this is a shutdown race in the
  PyQt6/WebEngine thread teardown, not a functional regression. **Deferred as its own task** (candidate angles:
  run Qt tests in a subprocess, add an `atexit` teardown, or deselect the crash-prone pair behind a marker).
  Until fixed, treat the `N passed` line — not `$LASTEXITCODE` — as the pass signal.

---

## P4 — maintainability & hygiene

- [ ] **P4-19 — Split god objects.** `ui.py` ~16k lines; `BrahmaLive` ~3.9k lines / 122 methods
  (`main.py:2162-6068`). Seams: session state machine, audio I/O, provider router, tool executor,
  reply/IG/email flows; and settings/chat/avatar/MCP panels.
  - **Started — provider failover seam.** The Gemini→OpenRouter throttle policy
    (`_prefer_openrouter`/`_throttled_to_openrouter` + 2 attrs) is now `core.provider_failover.ProviderFailover`,
    unit-tested in `tests/test_provider_failover.py`; `BrahmaLive` keeps thin delegating wrappers (defensive
    `getattr`, so attribute-built harnesses still work). Note: `core.provider_router` is the *provider registry*
    (kill switches), a different concern — not touched. Remaining seams: session state machine, audio I/O,
    tool executor, and the `ui.py` panels.
  - **Done — tool executor seam (this commit).** `_execute_tool` (727 lines) and its two wrappers
    (`_execute_tool_via_bridge`, `_execute_tool_sync`) moved verbatim to `core/tool_exec.py::ToolExecutor`.
    `main.py` keeps three thin delegations behind a lazily-built `self._tool_exec`; the executor takes a host
    reference plus the two main.py symbols it needs (`_ig_gemini_reply`, `TOOL_DECLARATIONS`) so there is no
    circular import. Behavior-preserving: full suite 149 passed, `accept_instructions.py` and
    `accept_local_speech.py` green. `main.py` 6551 -> 5813 lines.
  - **Done — audio I/O seam (this commit).** The mic loop (`_listen_audio`, 112 lines), receive loop
    (`_receive_audio`, 100 lines), playback (`_play_audio`, 34 lines) and speech (`speak`, `speak_error`)
    moved verbatim to `core/audio_io.py::AudioIO` (~350 lines). `main.py` keeps a lazily-built `self._audio_io`
    property plus five thin delegations; the executor takes a host reference and the three module-level symbols
    it needs (`_LiveReconnect`, `_wakeword_detected`, `_update_memory_async`) plus the audio constants. The
    speak/listen helpers (`_process_local_speech`, `_fallback`/`_send`/`_speak_edge_native`/`_speak_thread`)
    are local nested functions and stay local to the moved methods. Behavior-preserving: full suite 149 passed,
    `accept_local_speech.py`, `accept_instructions.py` and `accept_avatar.py` green (the latter two retargeted
    their source guards to `core/audio_io.py`).
  - **Done — reply/IG/email seam (this commit).** `_prompt_message_reply`, `_handle_pending_reply`,
    `_rewrite_reply_text`, `_draft_and_send_reply`, `_parse_ig_reply_intent`, `_handle_ig_reply_flow` and
    `_handle_email_flow` (318 lines) moved verbatim to `core/reply_flow.py::ReplyFlow`. `main.py` keeps a
    lazily-built `self._reply_flow` property plus four thin delegations (the three methods called only from
    within the cluster lost their names and become public methods on the class). The reply/IG/email state
    flags (`_reply_mode`, `_pending_reply_event`, `_pending_attention`, `_attention_lock`, `_email_*`,
    `_ig_*`) stay on the host (`self._host.*`) because `_on_text_command` and the attention handlers also
    read and set them; the class is handed a host reference plus the three main.py symbols it needs
    (`_gemini_text_reply`, `_get_api_key`, `_ig_gemini_reply`) so there is no circular import.
    Behavior-preserving: full suite 149 passed; all seven `accept_*.py` harnesses green (none needed
    retargeting — no harness guards this cluster's source). `main.py` 5544 -> 5247 lines.
  - **Done — session state machine seam (this commit).** The Live lifecycle — `set_live_mode`, `run`,
    `_serve_live_session`, `_wakeword_loop`, `_idle_speech_loop`, `_run_local_voice_session`, `_pause_until`,
    `_connect_tool_voice` and `_build_config` (618 lines) — moved verbatim to
    `core/session_lifecycle.py::SessionLifecycle`. `main.py` keeps a lazily-built `self._session_lifecycle`
    property plus six thin delegations (`run`, `set_live_mode`, `_serve_live_session`, `_wakeword_loop`,
    `_idle_speech_loop`, `_connect_tool_voice`); the three helpers called only from within the cluster
    (`_pause_until`, `_run_local_voice_session`, `_build_config`) stay module-local with no delegation. The
    class is handed a host reference plus the fourteen main.py symbols it needs (`SEND_SAMPLE_RATE`,
    `LIVE_MODEL`, `LIVE_CONNECT_TIMEOUT`, `TOOL_DECLARATIONS`, `_load_system_prompt`,
    `_injected_instructions`, `_standby_wakeword_detected`, `_speak_daily_briefing`, `_pcm_block`,
    `_get_api_key`, `_is_gemini_limit_error`, `_is_transient_live_error`, `_live_error_reason`,
    `_startup_log`) so there is no circular import; the session/lifecycle state (`self.session`, `_live_mode`,
    `_live_suspended`, `_manual_call_active`, `_session_task`, `_wake_thread`, `_wake_stop`, …) stays on the
    host (`self._host.*`) because the Qt surface and the wake-word path also read and set it. Behavior-
    preserving: full suite 149 passed, all eight `accept_*.py` harnesses green (`accept_instructions.py`,
    `accept_local_speech.py` and `tests/test_tool_bridge.py` retargeted their source guards to
    `core/session_lifecycle.py`; `accept_live_toggle.py` retargeted too and its method-copying `Surface`
    harness now binds the new property). `main.py` 5247 -> 4668 lines.
  - **Done — attention/meeting seam (this commit).** The attention/meeting handlers — `_attention_message`,
    `_announce_attention`, `_on_external_notification`, `_start_meeting_mode`, `_stop_meeting_mode`,
    `_on_meeting_update`, `_on_meeting_state`, `_attention_matches`, `_handle_attention_response` and
    `_on_attention_action` (188 lines) — moved verbatim to `core/attention_flow.py::AttentionFlow` (a host
    reference; `threading` and the `actions.attention_monitor` symbols `speak_native`/`read_event_preview`/
    `handle_call_action` imported directly, so no injection and no circular import). `main.py` keeps a
    lazily-built `self._attention_flow` property plus six thin delegations (`_on_external_notification`,
    `_stop_meeting_mode`, `_on_meeting_update`, `_on_meeting_state`, `_handle_attention_response`,
    `_on_attention_action`); the four helpers called only from within the cluster (`_attention_message`,
    `_announce_attention`, `_start_meeting_mode`, `_attention_matches`) lost their names and become public
    methods on the class. The attention/meeting state (`_pending_attention`, `_attention_lock`, `_meeting_lock`,
    `_meeting_active`, `_meeting_event`, `_meeting_assistant`) stays on the host (`self._host.*`) because the Qt
    surface and `_on_text_command` also read and set it. Behavior-preserving: full suite 149 passed, all seven
    `accept_*.py` harnesses green (none guard this cluster's source). `main.py` 4668 -> 4509 lines.
  - **Done — realtime/dashboard seam (this commit).** The background-task spawner and the
    dashboard/driver coroutines — `_spawn_background` (25 lines), `_serve_dashboard` (11),
    `_consume_remote_commands` (10), `_relay_phone_audio` (17) and `_send_realtime` (4) —
    moved verbatim to `core/realtime_flow.py::RealtimeFlow` (a host reference; `asyncio`/
    `traceback` imported directly). These are driven from `core/session_lifecycle.py` through
    `self._host.*`, so `main.py` keeps a lazily-built `self._realtime_flow` property plus five
    thin wrappers (`_spawn_background` sync; the four coroutines `async`). Host state
    (`_background_tasks`/`_dashboard`/`session`/`out_queue`/`_phone_active`/`ui`) and host
    methods (`_on_phone_connected`/`_on_text_command`) stay on the host. Cleanest residual
    seam: no source-text harness guards (`accept_live_toggle.py` monkeypatches a `FakeLive`
    stub, not `BrahmaLive`). Full suite 149 passed, all seven `accept_*.py` harnesses green.
    `main.py` 4273 -> 4224 lines.
  - **Done — device-routing seam (this commit).** The smart-home and Brahma-Connect text-command
    handlers — `_handle_smart_home_command` (80 lines), `_extract_launch_app_name` (30 lines) and
    `_handle_brahma_connect_command` (143 lines), 253 contiguous lines — moved verbatim to
    `core/device_flow.py::DeviceFlow` (a host reference; `json`/`re`/`threading` and the
    `actions.brahma_connect` symbols `connect_list_devices`/`connect_execute` imported directly;
    `mobile_autopilot` stays a lazy in-body import, so no injection and no circular import).
    `main.py` keeps a lazily-built `self._device_flow` property plus two thin delegations
    (`_handle_smart_home_command`, `_handle_brahma_connect_command`, both called from
    `_on_text_command`); the cluster-internal `_extract_launch_app_name` lost its name and becomes a
    public method on the class. `_smart_home`/`ui`/`speak` live on the host (`self._host.*`).
    Behavior-preserving: full suite 149 passed, all seven `accept_*.py` harnesses green (none guard
    this cluster's source). `main.py` 4509 -> 4273 lines.
  - **Done — chat/workspace widget seam (this commit).** The conversation feed, the message/task/
    attachment/event/artifact cards, the history item, the task dock, the workspace sidebar and the
    inline chat workspace — 11 classes plus the two helpers `_fmt_time_stamp` and `_markdown_to_html`
    (used only by this cluster) — a 2,109-line block moved verbatim to `core/ui_chat.py` (2,169 lines
    with its header). `ui.py` imports the module, injects the three shared symbols defined outside the
    cluster (`C`, `_framed_logo`, `BASE_DIR`) through a `bind()` call, and re-exports the eleven class
    names so every existing `ui.<Class>` reference is unchanged; none of the injected symbols is used at
    class-definition time, so there is no import cycle. Behavior-preserving: full suite 149 passed, all
    seven `accept_*.py` harnesses green (`accept_live_toggle.py`'s footer guard now reads `ui.py` +
    `core/ui_chat.py`). `ui.py` 17247 -> 15153 lines.
  - **Done — connectivity/settings pane seam (this commit).** The connectivity sidebar,
    `_ModelDiscoveryBridge`, the settings hub page and the `SystemConnectivityPage` settings pane —
    4 classes, a 4,164-line block — moved verbatim to `core/ui_connectivity.py` (4,234 lines with its
    header). `ui.py` imports the module, injects the six shared symbols defined outside the cluster
    (`C`, `BASE_DIR`, `CONFIG_DIR`, `APP_SETTINGS_FILE`, `_default_app_settings`,
    `_default_discord_settings`) through a `bind()` call, and re-exports the four class names so every
    `ui.<Class>` reference is unchanged; none of the injected symbols is used at class-definition time,
    so there is no import cycle. Note the value-injection caveat: `bind()` copies the value, so a harness
    that rebinds `ui.APP_SETTINGS_FILE` at runtime must mirror it onto the moved module
    (`ui_mod._ui_connectivity.APP_SETTINGS_FILE = …`) — `accept_providers.py` and `accept_avatar.py` do.
    `accept_providers.py`, `accept_avatar.py`, `accept_instructions.py` and `accept_local_speech.py`
    retargeted their source guards to read `ui.py` + `core/ui_connectivity.py`. The moved module's
    stdlib/Qt header was rebuilt from a **scope-accurate** free-name audit (`symtable`): auditing at
    class-node granularity wrongly cancels a name that is a local in one method but a module-global read
    in another (it had silently dropped `json`, `Path`, `QApplication`, `QLineEdit`, `QMessageBox`, and
    earlier `os`/`threading`/`time`). Behavior-preserving: full suite 149 passed, all seven
    `accept_*.py` harnesses green. `ui.py` 15153 -> 11004 lines.
  - **Done — smart-devices / connect-devices seam (this commit).** `SmartDevicesSection` +
    `_ConnectDeviceCard` + `BrahmaConnectDevicesPage` - 3 classes, a 1,282-line block - moved verbatim
    to `core/ui_devices.py` (1,306 lines with its header). `ui.py` imports the module, injects the two
    shared symbols defined outside the cluster (`C`, `APP_SETTINGS_FILE`) through a `bind()` call, and
    re-exports the three class names so every `ui.<Class>` reference is unchanged; neither injected
    symbol is used at class-definition time (`QFrame`/`pyqtSignal` are, and are direct Qt imports), so
    there is no import cycle. Qt, `asyncio`/`json`, and the external `SmartHomeService`/`_DeviceTile`
    are direct imports. Behavior-preserving: full suite 149 passed, all seven `accept_*.py` harnesses
    green. `ui.py` 11004 -> 9727 lines.
  - **Done - color-palette leaf module (this commit).** `class C` (the 84-line theme palette,
    lines 92-175; self-contained - no project imports, local `colorsys`, `_listeners` class attr,
    classmethods) moved verbatim to a new leaf module `core/ui_theme.py` (90 lines); `ui.py` now reads
    `from core.ui_theme import C` and the extracted `ui_*` modules import `C` directly. This was required
    ahead of the telemetry seam because `SmallPanelCard.__init__` uses `C.WHITE` as a default-argument
    value, which Python evaluates at class-definition time - so `C` cannot be injected through `bind()`.
    Class C body verified byte-identical to `HEAD:ui.py`. Behavior-preserving: full suite 149 passed, all
    seven `accept_*.py` harnesses green. `ui.py` 9727 -> 9644 lines.
  - **Done - launcher / telemetry-wings seam (this commit).** `LauncherControlPanel` + `SmallPanelCard`
    + `BrahmaTelemetryWing` + `BrahmaResultWing` + `StatCard` + `LogWidget` - 6 classes, a 1,084-line
    block - moved verbatim to `core/ui_telemetry.py` (1,105 lines with its header). `ui.py` imports the
    module, injects the one shared symbol still defined in `ui.py` (`MessageCard`, a `ui_chat` re-export)
    through a `bind()` call, and re-exports the six class names so every `ui.<Class>` reference is
    unchanged; `MessageCard` is not used at class-definition time, and `C` (from `core.ui_theme`) plus Qt
    are direct imports. `accept_providers.py` extended its source-guard concatenation to also read
    `core/ui_telemetry.py`. Behavior-preserving: full suite 149 passed, all seven `accept_*.py` harnesses
    green. `ui.py` 9644 -> 8572 lines.
  - **Done - gesture-camera seam (this commit).** The gesture preview trio - `_camera_available`
    (probe), `_GestureRenderCanvas` (hand-skeleton canvas) and `GestureCameraPreview` (the camera
    preview widget) - a 663-line block (3 nodes, fully contiguous) moved verbatim to
    `core/ui_gesture.py` (682 lines with its header). `ui.py` imports the module, injects the two
    shared module-level constants (`CONFIG_DIR`, `MODEL_DOWNLOAD_URL`) through a `bind()` call, and
    re-exports the three names so every existing `ui.<Name>` reference is unchanged; neither constant is
    used at class-definition time, so there is no import cycle. `GestureCameraPreview` is still
    referenced by `FloatingGestureCard` and `_camera_available` by `MainWindow` - both now resolve
    through the `ui.py` re-exports. All heavy imports (`cv2`, `numpy`, `pyautogui`, `urllib.request`,
    `importlib`, `ctypes`) are method-local; `C` comes from `core.ui_theme` and `GestureTracker` /
    `estimate_gesture_state` are direct imports. Behavior-preserving: full suite 149 passed, all seven
    `accept_*.py` harnesses green. `ui.py` 8572 -> 7919 lines.
  - **Done - floating-launcher seam (this commit).** `FloatingLauncher` and `FloatingGestureCard`
    (the floating gesture-card overlay), a 494-line block (2 nodes, fully contiguous), moved verbatim
    to `core/ui_floating.py` (504 lines with its header). This block needs no `bind()`: its only
    project symbol, `GestureCameraPreview`, is a direct `from core.ui_gesture import GestureCameraPreview`
    (a leaf with no back-dependency, so no cycle). All other names are Qt (`PyQt6.QtCore`/`QtGui`/
    `QtWidgets`) direct imports. `ui.py` imports the module and re-exports the two class names, so the
    existing references in `MainWindow` (`FloatingGestureCard` at `self._floating_gesture_card`,
    `FloatingLauncher` at `self._launcher`) are unchanged. `accept_live_toggle.py` extended its
    `ui.py` source guard to also read `core/ui_floating.py` (the launcher orb-theme / status-text
    `standby` strings moved with it). Behavior-preserving: full suite 149 passed, all seven
    `accept_*.py` harnesses green. `ui.py` 7919 -> 7429 lines.
  - **Done - top-overlays seam (this commit).** `RemoteKeyOverlay`, `DailyBriefingOverlay`,
    `ConfirmationOverlay`, and `MemoryInspectorOverlay`, a 921-line contiguous block (4 nodes, no
    non-blank holes), moved verbatim to `core/ui_overlays.py` (933 lines with its header). No
    `bind()`: the only project symbol used is the `C` palette, imported directly from
    `core.ui_theme`; everything else is Qt (`PyQt6.QtCore`/`QtGui`/`QtWidgets`) or `time`.
    `ui.py` imports the module and re-exports the four class names, so the `MainWindow` references
    (`self._remote_overlay`, plus the daily-briefing/confirmation/memory-inspector instantiations and
    the `RemoteKeyOverlay | None` annotation) are unchanged. Behavior-preserving: full suite 149
    passed, all seven `accept_*.py` harnesses green. `ui.py` 7429 -> 6514 lines.
  - **Done - setup-overlay seam (this commit).** `SetupOverlay` (the first-run setup wizard), a
    1051-line single-node block, moved verbatim to `core/ui_setup.py` (1068 lines with its header).
    Its two project-global dependencies, `APP_SETTINGS_FILE` and `_OS`, are injected through
    `bind()` (both are runtime-only uses); everything else is Qt / `json` / `pyqtSignal`, plus direct
    `from core.identity import identity` and `from core.ui_theme import C`. `ui.py` imports the
    module, calls `bind(...)`, and re-exports `SetupOverlay`, so the `BrahmaUI` instantiation is
    unchanged. Behavior-preserving: full suite 149 passed, all seven `accept_*.py` harnesses green.
    `ui.py` 6514 -> 5471 lines.
  - **Done - transient-overlay seam (this commit).** `_ease_out_expo`, `_ease_in_out_sine`,
    `ScanningOverlay`, `BootSequenceOverlay`, `IncomingAlertDialog`, and `MeetingOverlay` - a
    contiguous 1053-line span - moved verbatim to `core/ui_transients.py` (1067 lines with its
    header). No globals needed injecting: everything is Qt / `math` / `random` plus direct
    `from core.ui_theme import C`. `np`/`cv2` are referenced only inside a `try/except` in
    `ScanningOverlay`; they are undefined in `ui.py` too, so they are deliberately left undeclared
    (a verbatim move keeps the same soft-fail path). `ui.py` imports the module and re-exports all six
    names. Behavior-preserving: full suite 149 passed, all seven `accept_*.py` harnesses green.
    `ui.py` 5471 -> 4426 lines.
  - **Done - command-bar seam (this commit).** `CommandBar` (1179-1385, 207 lines) moved verbatim to
    `core/ui_commandbar.py` (221 lines). Qt / `from core.ui_theme import C` direct; the single module
    global it needs, `_icon_pixmap`, is injected via `bind()` after `ui.py` defines it (used only in
    method bodies, so no def-time use). `ui.py` re-exports `CommandBar`. Full suite 149 passed, all
    seven `accept_*.py` harnesses green. `ui.py` 4426 -> 4226 lines.
  - **Done - file-drop seam (this commit).** `_file_category`, `_fmt_size`, `FileDropZone` and
    `_DropCanvas` (972-1164, 193 lines) moved verbatim to `core/ui_filedrop.py` (209 lines). Qt /
    `Path` direct; the module-level constants `_FILE_ICONS`, `_EXT_TO_CAT`, `_OS` and `qcol` stay in
    `ui.py` (used only at runtime, never at def-time) and are injected via `bind()`. `ui.py`
    re-exports the four names. Full suite 149 passed, all seven `accept_*.py` harnesses green.
    `ui.py` 4226 -> 4046 lines.
  - **Done - system-metrics seam (this commit).** `_SysMetrics` (664-829, 166 lines) moved verbatim to
    `core/ui_sysmetrics.py` (180 lines). `psutil`/`threading`/`time` direct; `_OS` and `_quiet_run`
    stay in `ui.py` and are injected via `bind()` (runtime-only use; no def-time free names). Note the
    surrounding 664-916 span is *not* contiguous-clean - `_metrics = _SysMetrics()`, `_CAM_OK_CACHE`
    and the `ui_gesture` re-export block (832-847) sit between it and `_active_net_label`/`MetricBar`,
    so only the class itself was extracted. `ui.py` 4046 -> 3888 lines. Full suite 149 passed, all
    seven `accept_*.py` harnesses green.
  - **Done - branding/icon seam (this commit).** `qcol`, `_logo_icon`, `_logo_pixmap`, `_framed_logo`,
    `_icon_pixmap`, `_attach_pulse_glow` (482-551, 70 lines) moved verbatim to `core/ui_branding.py`
    (84 lines). Qt/`C` imports direct; `LOGO_FILE`/`LOGO_ICO` stay in `ui.py` and are injected via
    `bind()` (runtime-only). `_framed_logo`/`_icon_pixmap`/`qcol` are still re-exported from `ui.py`, so
    the downstream `bind()` calls in `ui_chat`/`ui_commandbar`/etc. keep working. `ui.py`
    3888 -> 3831 lines. Full suite 149 passed, all seven `accept_*.py` harnesses green.
  - **Done - net-label/MetricBar seam (prior commit).** `_active_net_label` and `MetricBar` (635-701,
    67 lines) moved verbatim to `core/ui_widgets.py` (80 lines). `psutil`/Qt/`C` direct; `qcol` is now
    imported directly from `core.ui_branding` (no `bind()` needed). `ui.py` 3831 -> 3768 lines. Full
    suite 149 passed, all seven `accept_*.py` harnesses green.
  - **Done - startup/defaults helper seam (prior commit).** `_quiet_run`, `_quote_cmd_arg`,
    `_hidden_launch_args`, `_startup_run_value`, `_startup_registry_key`, `_current_boot_stamp`,
    `_launched_from_windows_startup`, `_default_app_settings`, `_default_discord_settings` (497-605,
    109 lines) moved verbatim to `core/ui_startup.py` (126 lines). stdlib (`Path`/`platform`/`psutil`/
    `subprocess`/`sys`/`time`) direct; `BASE_DIR`/`_OS` injected via `bind()`. `accept_providers.py`
    source-text guard widened to also read `core/ui_startup.py` (the moved `_default_app_settings`
    holds the `provider_enabled_*` defaults). `ui.py` 3768 -> 3675 lines. Full suite 149 passed, all
    seven `accept_*.py` harnesses green.
  - **Done - BackgroundWidget seam (this commit).** `BackgroundWidget` (121-471, 351 lines) moved
    verbatim to `core/ui_bg.py` (377 lines). Qt/stdlib (`C`/`Path`/`QColor`/`QPainter`/`QPixmap`/
    `QTimer`/`QUrl`/`QWidget`/`Qt`/`json`/`os`/`pyqtSignal`/`time`) direct; the handle-based
    QtWebEngine import (`QWebEngineView`/`WEB_ENGINE_AVAILABLE`) is replicated verbatim as a
    conditional try/except in the module header (not an unconditional import, so a missing
    QtWebEngine still degrades); `BASE_DIR` injected via `bind()`. `accept_avatar.py`,
    `accept_instructions.py` and `accept_providers.py` source-text guards widened to also read
    `core/ui_bg.py` (the moved class holds the avatar/emotion JS and the 3D-toggle web-view hide).
    `ui.py` 3675 -> 3331 lines. Full suite 149 passed, all seven `accept_*.py` harnesses green.
  - **Done - DeveloperModeDialog seam (this commit).** `DeveloperModeDialog` (293-376, 84 lines)
    moved to `core/ui_devmode.py` (98 lines). Qt/core imports (`C`/`QCheckBox`/`QDialog`/
    `QFileDialog`/`QFont`/`QHBoxLayout`/`QLabel`/`QLineEdit`/`QPushButton`/`QVBoxLayout`) direct;
    `BASE_DIR` injected via `bind()`. The single body change replaces
    `str(Path(__file__).resolve().parent)` with `str(BASE_DIR)` — semantically identical
    (`BASE_DIR` is that same repo root) but correct from `core/`; the class already used `BASE_DIR`
    in `_browse_folder`. Body verified identical modulo that one documented line. `ui.py`
    3331 -> 3254 lines. Full suite 149 passed, all seven `accept_*.py` harnesses green.
  - **Done - MainWindow state-appliers mixin (this commit).** Eleventh method-level split: 9
    contiguous methods (`set_voice_status`, `_check_config`, `_apply_scan_state`, `set_scanning`,
    `set_meeting_mode`, `_on_screen_capture_requested`, `capture_screen_threadsafe`,
    `_apply_meeting_state`, `_apply_task_workspace`, 135 lines) moved verbatim into
    `core/ui_state_appliers.py` (`class _StateAppliersMixin`). `API_FILE`, `MeetingOverlay`,
    `ScanningOverlay` injected via `bind()`; `json` imported directly. `accept_live_toggle.py`
    source read widened to include the new module (`def set_voice_status` check). `ui.py`
    2357 -> 2228 lines. 149 tests pass, seven harnesses green.
  - **Done - MainWindow setup-flow mixin (this commit).** Twelfth method-level split: 2
    contiguous methods (`_show_setup`, `_on_setup_done`, 55 lines) moved verbatim into
    `core/ui_setup_flow.py` (`class _SetupFlowMixin`). `API_FILE`, `CONFIG_DIR`, `SetupOverlay`
    injected via `bind()`; `json`, `os`, `traceback` imported directly. Module named
    `ui_setup_flow` (not `ui_setup`) because `core/ui_setup.py` already holds the earlier
    `SetupOverlay` seam; `gen_ui_mixin.py` now refuses to overwrite an existing module that does
    not define the target mixin. `ui.py` 2228 -> 2179 lines. 149 tests pass, seven harnesses
    green.
  - **Done - MainWindow panel-builder mixin (this commit).** Thirteenth method-level split: 3
    contiguous methods (`_build_center_panel_modern`, `_build_right_panel_modern`,
    `_build_command_row`, 287 lines) moved verbatim into `core/ui_panels.py`
    (`class _PanelsMixin`). `BrahmaResultWing`, `BrahmaTelemetryWing`, `InlineChatWorkspace`,
    `_RIGHT_W`, `_icon_pixmap` injected via `bind()`; `SettingsHubPage`,
    `SystemConnectivityPage`, `SystemConnectivitySidebar`, `SmartDevicesSection`,
    `BrahmaConnectDevicesPage` are imported directly from `core.ui_connectivity` /
    `core.ui_devices` (they are assigned in ui.py only after the bind site, so `bind()` would
    NameError). `gen_ui_mixin.py` gained a `DIRECT_IMPORTS_JSON` env hook for this case. `ui.py`
    2179 -> 1900 lines. 149 tests pass, seven harnesses green.
  - **Done - MainWindow feature-wiring mixin (this commit).** Fourteenth method-level split: 3
    contiguous methods (`set_settings_bridge`, `_set_page`, `set_brahma_connect_service`, 55
    lines) moved verbatim into `core/ui_wiring.py` (`class _WiringMixin`). No injected globals;
    `QStackedWidget` imported directly. `ui.py` 1900 -> 1846 lines. 149 tests pass, seven
    harnesses green.
  - **Done - MainWindow runtime-tick mixin (this commit).** Fifteenth method-level split: 3
    contiguous methods (`_tick_clock`, `_update_metrics`, `_browse_attachment`, 124 lines) moved
    verbatim into `core/ui_runtime.py` (`class _RuntimeMixin`). `_metrics`, `_active_net_label`,
    `_camera_available` injected via `bind()` (none is ever rebound in ui.py, and all are defined
    above the bind site, so the injection is exactly equivalent); `psutil`, `time`, `Path`,
    `QFileDialog` imported directly. `ui.py` 1846 -> 1728 lines. 149 tests pass, seven harnesses
    green.
  - **Done - MainWindow state-applier mixin (this commit).** Sixteenth method-level split: the
    single 109-line god-method `_apply_state` moved verbatim into `core/ui_apply_state.py`
    (`class _ApplyStateMixin`); no injected globals, `C` imported directly (the state badge /
    input-placeholder / chip / task-card maps, including their emoji, verified byte-identical).
    `accept_live_toggle.py`'s section-9 source guard widened to also read
    `core/ui_apply_state.py`. `ui.py` 1728 -> 1620 lines. 149 tests pass, seven harnesses green.
  - **Done - BrahmaUI app-settings mixin (this commit).** First method-level split of the
    remaining `BrahmaUI` god object: five settings-persistence methods (`_load_app_settings`,
    `_settings_file_stamp`, `_invalidate_app_settings_cache`, `_save_app_settings`,
    `_save_launcher_position`, span 798-853 = 56 lines) moved verbatim into
    `core/ui_shell_settings.py` (`class _ShellSettingsMixin`); `APP_SETTINGS_FILE` / `CONFIG_DIR`
    / `_default_app_settings` injected via `bind()` (all defined well above the bind site; values
    copied at import, so production behaviour is unchanged). `gen_ui_mixin.py` generalised to a
    `TARGET_CLASS` env var (also handles a base-less `class BrahmaUI:`) and now refuses to move
    any method that uses `super()`. `accept_instructions.py`'s settings source guard widened to
    also read `core/ui_shell_settings.py`. `ui.py` 1620 -> 1570 lines. 149 tests pass, seven
    harnesses green.
  - **Done - BrahmaUI discord-settings mixin (this commit).** Second BrahmaUI method-level split:
    four discord bot-settings methods (`_load_discord_settings`, `_save_discord_settings`,
    `_emit_discord_settings`, `_refresh_discord_card`, span 845-898 = 54 lines) moved verbatim
    into `core/ui_shell_discord.py` (`class _ShellDiscordMixin`); `CONFIG_DIR` /
    `DISCORD_SETTINGS_FILE` / `_default_discord_settings` injected via `bind()`, `C` imported from
    `core.ui_theme`. `ui.py` 1570 -> 1522 lines. 149 tests pass, seven harnesses green.
  - **Done - BrahmaUI startup/boot mixin (this commit).** Third BrahmaUI method-level split: ten
    startup-animation + boot-overlay methods (`_startup_animation_enabled`,
    `_set_startup_animation_enabled`, `_refresh_startup_animation_button`,
    `_toggle_startup_animation`, `_should_play_boot_sequence`, `_mark_boot_sequence_played`,
    `play_boot_sequence`, `boot_add_step`, `boot_set_step_status`, `boot_set_progress`; span
    852-950 = 99 lines, one comment-only gap) moved verbatim into `core/ui_shell_boot.py`
    (`class _ShellBootMixin`); `BootSequenceOverlay` / `_current_boot_stamp` injected via
    `bind()`. `ui.py` 1522 -> 1428 lines. Verification note: 148 tests pass; the single
    `test_executor_auto_heal_error_recording` was deselected because it makes a **live Gemini API
    call** (`agent/error_handler.py:analyze_error` -> `google.generativeai.generate_content`,
    reached from `agent/executor.py:execute`) that hangs on this machine's current network. This is
    pre-existing and unrelated (the test does not import `ui`); reproduced identically at the prior
    commit (`4f34b21`). Seven harnesses green.
  - **Done - BrahmaUI tray mixin (this commit).** Fourth BrahmaUI method-level split: five tray /
    launcher-activation handlers (`_build_tray_menu`, `_on_tray_activated`, `_on_minimized`,
    `_on_restored`, `_on_launcher_double_clicked`; span 858-914 = 57 lines) moved verbatim into
    `core/ui_shell_tray.py` (`class _ShellTrayMixin`, no injected names). `ui.py` 1428 -> 1372
    lines. 148 tests pass (network-dependent auto-heal test deselected, see prior note), seven
    harnesses green.
  - **Done - BrahmaUI developer-mode mixin (this commit).** Fifth BrahmaUI method-level split: two
    developer-mode helpers (`_open_developer_mode_dialog`, `_apply_developer_mode_ui`; span
    820-844 = 25 lines) moved verbatim into `core/ui_shell_devmode.py`
    (`class _ShellDevModeMixin`); `DeveloperModeDialog` injected via `bind()`. `ui.py` 1372 ->
    1351 lines. 148 tests pass (network-dependent auto-heal test deselected), seven harnesses
    green.
  - **Done - BrahmaUI sidebar/floating mixin (this commit).** Sixth BrahmaUI method-level split:
    five sidebar / command-bar / floating-icon helpers (`_toggle_command_bar`,
    `_toggle_workspace_sidebar`, `_show_workspace_sidebar`, `_close_workspace_sidebar`,
    `_show_floating_icon`; span 852-883 = 32 lines) moved verbatim into
    `core/ui_shell_sidebar.py` (`class _ShellSidebarMixin`, no injected names). `ui.py` 1351 ->
    1320 lines. 148 tests pass (network-dependent auto-heal test deselected), seven harnesses
    green. (See the environment note below: the pytest *process* now exits non-zero on Qt
    native teardown even when all tests pass — pre-existing, reproduced at the prior commit.)
  - **Done - BrahmaUI launcher/startup mixin (this commit).** Seventh BrahmaUI method-level split:
    three launcher/startup helpers (`_toggle_workspace_on_startup`,
    `_hide_launcher_with_protection`, `_show_hide_launcher_confirm`; span 936-965 = 30 lines)
    moved verbatim into `core/ui_shell_launcher.py` (`class _ShellLauncherMixin`);
    `LauncherControlPanel` injected via `bind()`. `_show_hide_launcher_confirm` builds the
    `LauncherControlPanel(...)` whose `on_setting_toggle=self._on_panel_setting_toggle` line is
    asserted by `accept_providers.py`, so its source-text `panel_start` was widened to also read
    `core/ui_shell_launcher.py`. `ui.py` 1320 -> 1294 lines. 148 tests pass (network-dependent
    auto-heal test deselected), seven harnesses green.
  - **Done - BrahmaUI control-panel mixin (this commit).** Eighth BrahmaUI method-level split:
    three control-panel helpers (`_position_control_panel`, `_show_control_panel`,
    `_handle_launcher_action`; span 941-971 = 31 lines) moved verbatim into
    `core/ui_shell_controlpanel.py` (`class _ShellControlPanelMixin`). `_position_control_panel`
    annotates its parameter `panel: LauncherControlPanel`, a def-time reference the generator
    refuses to bind-inject, so the module gets a direct `from core.ui_telemetry import
    LauncherControlPanel` instead of a `bind()` (no import cycle: `ui_telemetry` does not import
    `core.ui`). `ui.py` 1294 -> 1264 lines. 148 tests pass (network-dependent auto-heal test
    deselected), seven harnesses green.
  - **Done - BrahmaUI panel-settings mixin (this commit).** Ninth BrahmaUI method-level split:
    two panel/app helpers (`_restart_app`, `_on_panel_setting_toggle`; span 861-941 = 81 lines)
    moved verbatim into `core/ui_shell_panel_settings.py` (`class _ShellPanelSettingsMixin`);
    `BASE_DIR`, `_OS`, `_hidden_launch_args`, `_launched_from_windows_startup` injected via
    `bind()`. `_on_panel_setting_toggle` carries the `set_animation_enabled` / "no web view
    exists" strings, so `accept_instructions.py`'s `ui_src2` source-text guard was widened to
    also read `core/ui_shell_panel_settings.py` (the `accept_providers.py` `set_animation_enabled`
    / `_web_view.hide()` guards still pass via `ui_connectivity`/`ui_bg`). `ui.py` 1264 -> 1190
    lines. 148 tests pass (network-dependent auto-heal test deselected), seven harnesses green.
  - **Done - BrahmaUI actions mixin (this commit).** Tenth BrahmaUI method-level split: nine
    contiguous command/clipboard/discord handlers (`_sync_launcher_state`, `_on_clipboard_actionable`,
    `_submit_command`, `_browse_attachment`, `_toggle_mute`, `_show_update_prompt`, `_on_chat_event`,
    `_on_discord_config_changed`, `_open_app`; span 842-931 = 90 lines) moved verbatim into
    `core/ui_shell_actions.py` (`class _ShellActionsMixin`, no injected or imported names). `ui.py`
    1190 -> 1101 lines. 149 tests pass, seven harnesses green.
  - **Done - BrahmaUI delegates/forwarders mixin (this commit).** Eleventh BrahmaUI method-level
    split: the 45-def forwarding/state surface (`muted` property+setter, `set_muted`,
    `set_muted_state`, `current_file`, the `on_text_command`/`on_remote_clicked`/`on_attention_action`/
    `on_chat_event` property pairs, `set_state`, `live_mode_changed`, `live_call_toggled`,
    `set_audio_level`, `write_log`, the confirm/memory/daily-briefing/hud/call-screening overlays,
    `submit_external_command`, `notify_phone_connected`, `set_scanning`, `show_circuit_hud`,
    `set_meeting_mode`, the task-workspace helpers, `show_content`, `wait_for_api_key`,
    `start_speaking`, `stop_speaking`; span 844-1098 = 255 lines) moved verbatim into
    `core/ui_shell_delegates.py` (`class _ShellDelegatesMixin`); no injected names, `Path`/`time`
    imported directly. The generator was extended to keep `@property`/`@x.setter` decorator lines
    and to collect all defs sharing a name. `accept_live_toggle.py`'s `src` guard was widened to
    also read `core/ui_shell_delegates.py` (the `stop_speaking` body carries its `"STANDBY"` guard).
    `ui.py` 1101 -> 847 lines. 149 tests pass, seven harnesses green.
  - **Done - BrahmaUI window/facade mixin (this commit).** Twelfth (final) BrahmaUI method-level
    split: the last five directly-defined methods (`_make_app_icon`, `set_brahma_connect_service`,
    `show_main`, `set_dashboard_page`, `hide_main`; span 810-844 = 35 lines) moved verbatim into
    `core/ui_shell_window.py` (`class _ShellWindowMixin`, `bind(_logo_icon=_logo_icon)`; `Qt`/`QIcon`
    imported directly, the `QIcon` return annotation is lazy under `from __future__ import
    annotations`). `BrahmaUI` now defines **only `__init__`** on top of its twelve mixins - the same
    "constructor + mixins only" shape as `MainWindow`. `ui.py` 847 -> 816 lines. 149 tests pass,
    seven harnesses green.
  - **Done - MainWindow secondary-overlays mixin (this commit).** Tenth method-level split: 10
    contiguous methods (`_apply_circuit_overlay`, `_apply_call_screening`,
    `_take_over_screened_call`, `_hang_up_screened_call`, `_on_confirm_answered`,
    `_position_confirm_overlay`, `show_memory_inspector`, `hide_memory_inspector`,
    `_apply_memory_overlay`, `_position_memory_overlay`, 132 lines) moved verbatim into
    `core/ui_secondary_overlays.py` (`class _SecondaryOverlaysMixin`). `MemoryInspectorOverlay`
    injected via `bind()`; Qt widgets imported directly. `ui.py` 2485 -> 2357 lines. 149 tests
    pass, seven harnesses green.
  - **Done - MainWindow remote-connect mixin (this commit).** Ninth method-level split: 4
    contiguous methods (`show_app`, `_show_remote_connect`, `_position_remote_overlay`,
    `notify_phone_connected`, 50 lines) moved verbatim into `core/ui_remote.py`
    (`class _RemoteMixin`). `RemoteKeyOverlay` injected via `bind()`, `Qt` imported directly.
    `ui.py` 2531 -> 2485 lines. 149 tests pass, seven harnesses green.
  - **Done - MainWindow nav/sidebar mixin (this commit).** Eighth method-level split: 7
    contiguous methods (`_toggle_fullscreen`, `_toggle_left_sidebar`, `_toggle_right_sidebar`,
    `_apply_sidebar_state`, `_on_nav_dashboard`, `_on_nav_settings`, `_update_nav_styles`,
    78 lines) moved verbatim into `core/ui_nav.py` (`class _NavMixin`). `_LEFT_W`/`_RIGHT_W`
    injected via `bind()`, `C` imported directly. `ui.py` 2604 -> 2531 lines. 149 tests pass,
    seven harnesses green.
  - **Done - MainWindow meeting/attention mixin (this commit).** Seventh method-level split: 6
    contiguous methods (`_on_discord_status_update`, `_request_stop_meeting`,
    `_toggle_meeting_overlay`, `_position_meeting_overlay`, `_show_attention_alert`,
    `_attention_choice`, 76 lines) moved verbatim into `core/ui_meeting.py` (`class _MeetingMixin`).
    `IncomingAlertDialog` injected via `bind()`, `QApplication` imported directly. `ui.py`
    2676 -> 2604 lines. 149 tests pass, seven harnesses green.
  - **Done - MainWindow mute/send mixin (this commit).** Sixth method-level split: 6
    contiguous methods (`_toggle_mute`, `set_muted_state`, `_style_mute_btn`, `_send`,
    `submit_command`, `_on_log_text`, 114 lines) moved verbatim into `core/ui_mute_send.py`
    (`class _MuteSendMixin`). `C` (theme) and `sound_mgr` imported directly, `threading` stdlib;
    no injected globals. `ui.py` 2789 -> 2676 lines. 149 tests pass, seven harnesses green.
  - **Done - MainWindow overlay-cards mixin (this commit).** Fifth method-level split: 14
    contiguous methods (`_restart_card_hide_timer`, `_hide_command_cards`, `_apply_hud_operation`,
    `_apply_hud_deliverable`, `_apply_daily_briefing`, `_position_briefing_overlay`,
    `_hide_daily_briefing_card`, `_schedule_daily_briefing_hide`, `show_daily_briefing`,
    `hide_daily_briefing`, `schedule_daily_briefing_hide`, `show_confirm`, `hide_confirm`,
    `_apply_confirm_overlay`, 139 lines) moved verbatim into `core/ui_overlays_cards.py`
    (`class _OverlayCardsMixin`). `DailyBriefingOverlay`/`ConfirmationOverlay` injected via
    `bind()`; no new imports. `ui.py` 2923 -> 2789 lines. 149 tests pass, seven harnesses green.
  - **Done - MainWindow avatar/audio mixin (this commit).** Fourth method-level split: 4
    contiguous methods (`_attach_avatar_renderer`, `refresh_avatar_renderer`,
    `_on_audio_level_sig`, `set_audio_level`, 56 lines) moved verbatim into
    `core/ui_avatar_audio.py` (`class _AvatarAudioMixin`). Fully self-contained - no injected
    globals, no new imports (only `self.*`). `accept_avatar.py` `ui_src` widened to include the
    new module (presence/text checks). `ui.py` 2978 -> 2923 lines. 149 tests pass, seven
    harnesses green.
  - **Done - MainWindow run-at-login/API-defaults mixin (this commit).** Third method-level
    split: 5 contiguous methods (`_load_api_defaults`, `_startup_enabled`, `_set_startup_enabled`,
    `_refresh_startup_button`, `_toggle_startup`, 81 lines) moved verbatim into
    `core/ui_startup_os.py` (`class _StartupOSMixin`, third base of `MainWindow`). Bodies
    byte-identical; `json`/`platform` direct, Windows-only `winreg` reproduced under the same
    `if platform.system() == "Windows":` guard, `API_FILE`/`_startup_registry_key`/
    `_startup_run_value` injected via `bind()`. `ui.py` 3053 -> 2978 lines. 149 tests pass,
    seven harnesses green.
  - **Done - MainWindow app-settings/startup-animation mixin (this commit).** Second
    method-level split: 8 contiguous methods (`_settings_file_stamp`, `_load_app_settings`,
    `_invalidate_app_settings_cache`, `_save_app_settings`, `_startup_animation_enabled`,
    `_set_startup_animation_enabled`, `_refresh_startup_animation_button`,
    `_toggle_startup_animation`, 100 lines) moved verbatim into `core/ui_appsettings.py`
    (`class _AppSettingsMixin`, second base of `MainWindow`). Bodies byte-identical;
    `json`/`os`/`platform`/`atomic_write_json` direct, `APP_SETTINGS_FILE`/`CONFIG_DIR`/
    `_current_boot_stamp`/`_default_app_settings` injected via `bind()`. `accept_instructions.py`
    retargeted (mirror sandbox onto `_ui_appsettings.APP_SETTINGS_FILE`, add module to `_ui_src`
    source reads) - same rebind hazard as the connectivity seam. `ui.py` 3146 -> 3053 lines.
    149 tests pass, seven harnesses green.
  - **Done - MainWindow Discord mixin (previous commit).** First method-level split of the
    `MainWindow` god object (97 methods, 1945 lines). Its 7 contiguous Discord methods
    (`_load_discord_settings`/`_save_discord_settings`/`_emit_discord_settings`/
    `_refresh_discord_card`/`_save_discord_token`/`_start_discord_bot`/`_stop_discord_bot`,
    114 lines) moved **verbatim** into `core/ui_discord.py` as `class _DiscordMixin`, now a base
    of `MainWindow` (`class MainWindow(_DiscordMixin, QMainWindow)`). Method bodies verified
    byte-identical; `C`/`json`/`os` direct, `CONFIG_DIR`/`DISCORD_SETTINGS_FILE`/
    `_default_discord_settings` injected via `bind()`; signals resolve via MRO. `ui.py`
    3254 -> 3146 lines. 149 tests pass, seven harnesses green.
  - **ui.py god-object split complete.** Both god classes are now decomposed: `MainWindow`
    (97 methods / 1945 lines) is `__init__` + three lifecycle methods + `_make_window_icon` on top
    of 16 mixins, and `BrahmaUI` (952 lines) is `__init__` only on top of 12 mixins - the same
    "constructor + mixins only" shape. The irreducible remainder: the two `__init__` bodies
    (MainWindow 161 lines, BrahmaUI 95), `_RootShim` (7 lines), and the module-level bootstrap.
    `_base_dir` and `_new_setStyleSheet` are deliberately left in `ui.py`: `_base_dir` uses
    `__file__` (moving it to `core/` would change `BASE_DIR`) and `_new_setStyleSheet` is an
    order-sensitive import-time monkeypatch. `ui.py` is 816 lines (down from 17247).
  - **BrahmaLive seam split complete.** The `BrahmaLive` god object (originally ~3.9k lines / 122
    methods, per the item above) is now 72 methods / 1,701 lines after eight verbatim seam
    extractions, each reached through a lazily-built host property plus thin delegating wrappers:
    `core/provider_failover.py` (throttle policy), `core/tool_exec.py` (tool executor),
    `core/audio_io.py` (mic/receive/playback/speak), `core/reply_flow.py` (reply/IG/email),
    `core/session_lifecycle.py` (Live lifecycle), `core/attention_flow.py` (attention/meeting),
    `core/device_flow.py` (smart-home + Brahma-Connect) and `core/realtime_flow.py`
    (background/dashboard/realtime). Every extraction was verified the same way: AST-identical
    method bodies, an import check, full suite 149 passed, all seven `accept_*.py` harnesses green,
    and `scripts/precommit_check.py --quick` SAFE. The remainder is intentionally host-resident and
    **not** slated for further extraction: `_on_text_command` (658 lines) is one cohesive dispatcher
    — its 21 nested `_run_*` closures are its content, so relocating it would move, not decompose,
    the logic, and the audio/session/attention seams' own rationale depends on it staying on the
    host; `_fallback_reply` (262 lines) plus `_split_emotion_tag`/`_deliver_avatar_emotion` is coupled
    to `accept_instructions.py`'s source-slice guard (which protects a historical `NameError` in the
    reply path) and was deliberately left out of the reply seam; and the `__init__` body (109 lines)
    is irreducible. No unguarded, high-cohesion cluster remains.
- [x] **P4-20 — Import-time side effects.** Done: the duplicated Qt/env blocks in `main.py`, `ui.py` (two) and
  `core/globe_window.py` collapsed into one idempotent `core/qt_bootstrap.bootstrap()` (importing that module
  has no side effects); `core/boot_sentry` no longer rolls files back on import — `main` calls
  `boot_sentry.run()`. `tests/conftest.py` applies `bootstrap()` so the flag/attribute tests stay deterministic.
- [x] **P4-21 — Repo weight.** Vendored `three.min.js` ×3 trees; `hand_landmarker.task` 7.8 MB tracked. Done: `hand_landmarker.task` untracked + gitignored (ui.py downloads it on demand under
  `%LOCALAPPDATA%`); `three.min.js` is now committed once at `assets/web_background/` (`assets/globe/index.html`
  loads it via `../web_background/`; the Android module copies it in at build time with the `syncSharedThree`
  Gradle task, which is unverified until an Android build runs).
- [x] **P4-22 — Stale doc.** `docs/ONE-391-changes.md:329` now states the two credentials are empty strings
  (`""`) with an added `"mcpServers": {}`, matching the current `config/app_settings.json`.
- [x] **P4-23 — Portability.** `core/user_paths.get_user_data_dir` now documents that it is Windows-first
  (`%LOCALAPPDATA%\BrahmaAI`) with a home-dir fallback only so non-Windows tooling does not crash; no behavior
  change.

---

## Carried-over items from the earlier (pre-review) TODO — status

These were listed as incomplete before this pass. **None of them is done, and none is part of P0.**

- [ ] **Optional: add Gemini as an extra TTS engine for the local/modular path.** NOT done. (User note:
  "if you want to add gemini as an extra tts engine for the local mode, that is fine." Opt-in, not started.)
- [x] **Fix `QPushButton` "Could not parse stylesheet" error.** DONE. Root cause: `smart_home_page_new.py`
  `_soft_button_style()` concatenated an f-string line with a **plain (non-f) string** that ended in `}}`.
  Brace escaping (`{{` / `}}`) only applies inside f-strings, so the plain line emitted a literal `}}`,
  yielding an unbalanced sheet (`QPushButton { ... } }QPushButton:hover { ... }`) that Qt rejected with
  `Could not parse stylesheet of object QPushButton(0x...)`. Fixed by making that trailing brace a single
  `}`. Pinpointed with an offscreen harness that wraps `QWidget.setStyleSheet` + a `qInstallMessageHandler`
  (2 warnings at startup, both this button; 0 after the fix) and an AST scan for `{{`/`}}` in non-f string
  constants (4 unrelated JSON/docstring hits). Regression test: `tests/test_smart_home_styles.py`.
- [x] **Fix `QObject::setParent: Cannot set parent, new parent is in a different thread` (x9).**
  FIXED. The original hypothesis (MainWindow built on a worker thread) was false: `MainWindow` is
  built in `main()` on the main thread, and the post-split `setParent(None)` sites run on the GUI
  thread. The real cause is that `BrahmaUI` delegates and `MainWindow.submit_command` are reached
  from worker threads (mic/audio level loop, screen processor, meeting listener, daily-briefing loop,
  wake word, Discord gateway, phone gateway) and touched QWidgets directly. Every such call now
  funnels through one GUI-thread marshaller, `core/ui_thread.run_on_ui(win, fn)` ->
  `MainWindow._run_on_ui` (queued over the `_ui_call_sig` signal when off the GUI thread, inline
  otherwise). Added `_ui_call_sig` / `_on_ui_call` / `_run_on_ui` to `ui.py`; routed the
  `_ShellDelegatesMixin` direct-widget delegates (`set_audio_level`, `set_meeting_mode`,
  `set_scanning`, `show`/`hide`/`schedule_daily_briefing`, `notify_phone_connected`, `set_muted`,
  `set_muted_state`, the `muted` setter, `submit_external_command`), `_MuteSendMixin.submit_command`,
  and the offline speech-test thread in `core/ui_connectivity.py` (its `_speech_status` write)
  through it. The `on_text_command` worker thread is intentionally kept off the GUI thread (it runs
  blocking LLM/network work); its `self.ui.*` calls were already Qt-signal based, so no marshalling
  is needed there. Verified: `tests/test_ui_thread_marshalling.py` (6 tests), full suite 155 passed,
  all seven `accept_*.py` harnesses green, `scripts/precommit_check.py --quick` SAFE.
  **Follow-up fix:** the marshaller's `threading.get_ident()` landed *before* a pre-existing-but-redundant
  local `import threading` inside `MainWindow.__init__` (`ui.py`), which made `threading` a method-local name
  and raised `UnboundLocalError` at app startup (the crash seen on a real run). Removed the redundant import
  and added a subprocess startup smoke test; full suite now **156 passed**.
- [x] **Address the DPI awareness warning (qt.conf).** FIXED. `SetProcessDpiAwarenessContext() failed:
  Access is denied`. Qt's Windows platform plugin sets `PER_MONITOR_AWARE_V2` while constructing
  `QGuiApplication`; if anything already fixed the process to a *different* context first (a manifest, or a
  helper lib such as pywinauto / mss imported earlier in the startup path) Qt's own call fails ACCESS_DENIED
  and the notice is logged. `bootstrap()` now fixes the process to the same V2 context before `QApplication`
  exists (`configure_dpi_awareness()` in `core/qt_bootstrap.py`, pseudo-handle `-4`), so Qt's call becomes an
  idempotent no-op. No behaviour change (V2 is Qt's default), silent on failure, `BRAHMA_DPI_AWARENESS=off`
  opts out. Verified with `AreDpiAwarenessContextsEqual`: fix → V2, skip → UNAWARE.

---

## Work log

- 2026-10-15 — Review completed (4 parallel explorations) + 2 headline claims verified. TODO file created.
  Checkpoint tag `checkpoint/pre-p0-review-fixes`.
- 2026-10-15 — **P0-1, P0-2, P0-3 implemented.** Files: `main.py` (local-branch CancelledError guard; lazy
  Gemini client; hardened `_get_api_key`), `core/user_paths.py` (`atomic_write_json`), `ui.py` (3 save sites),
  `memory/config_manager.py`, `tests/test_atomic_settings_write.py` (new, 4 tests).
  Verification: `py_compile` clean; `pytest tests` → **139 passed**; `accept_live_toggle.py` → ALL CRITERIA
  PASS; `accept_local_speech.py` → ALL CRITERIA PASS. Not committed, not pushed.
- 2026-10-15 — **P1-4, P1-5, P1-6 implemented** (`main.py` only). Verification: `py_compile` clean;
  `pytest tests` → **139 passed**; `accept_live_toggle.py` → ALL CRITERIA PASS; `accept_local_speech.py` →
  ALL CRITERIA PASS; `accept_wakeword.py` → B/C/C2 pass (section D "STT invoked" fails only because this box
  has no input device — confirmed identical on the pre-change stash, i.e. environmental). Not pushed.
- 2026-10-15 — **P1-7, P1-8, P1-9 implemented** (`core/mcp_manager.py`; test `tests/test_mcp_supervise.py`,
  new, 3 tests). Verification: `py_compile` clean; `pytest tests` → **142 passed**; `accept_live_toggle.py` →
  ALL CRITERIA PASS. Not pushed.
- 2026-10-15 — **P1-4..P1-9 committed** locally (no push): `2703e75` (logs), `7cefdb7` (P1-4/5/6, `main.py`),
  `b871624` (P1-7/8/9, `core/mcp_manager.py` + test), `a6796ed` (logs). HEAD now 7 commits ahead of `origin/main`.
- 2026-10-15 — **P2-10, P2-11, P2-12, P2-13 implemented.** Files: `main.py` (`_is_gemini_limit_error` narrowed;
  `_prefer_openrouter`/`_throttled_to_openrouter` + `_openrouter_until` cooldown replacing the sticky flag),
  `core/mcp_manager.py` (collision detection in `_register_server_tools`; cleanup `except` → `logger.debug`),
  `core/speech_tts.py` (`_PROBE_CACHE_LIMIT`), `core/avatar_runtime.py` (module `logger`; asset-skip debug logs),
  `tests/test_mcp_supervise.py` (+2 collision tests). Verification: `py_compile` clean on all four; `pytest tests`
  → **144 passed**; `accept_live_toggle.py`, `accept_local_speech.py`, `accept_avatar.py`, `accept_providers.py` →
  ALL CRITERIA PASS. Note: P2-12's STT `_model_cache` was already bounded (clear-then-set). Not pushed.
- 2026-10-15 — **P3-15, P3-16 implemented.** Added `pytest.ini` with `testpaths = tests`. Bare `pytest` went
  from a collection error (actions/test_action.py::test_action, test_self_heal.py::test_action both hitting the
  intentional ZeroDivisionError) to **144 passed**; the demo/harness files are untouched. Remaining P3-14
  (CI/lint config) and P3-14b (dependency pinning) need a decision and were left open.
- 2026-10-15 — **P4-22, P4-23 implemented** (docs only). `docs/ONE-391-changes.md` credential note corrected to
  match `config/app_settings.json` (`""` + `mcpServers`); `core/user_paths.get_user_data_dir` docstring now
  states the Windows-first path and the non-Windows fallback. `py_compile` clean. Not pushed.
- 2026-10-16 — **Item 675 startup regression fixed + DPI warning fixed.** `ui.py`: removed the redundant local
  `import threading` inside `MainWindow.__init__` that shadowed the module-level import and made the
  marshaller's `threading.get_ident()` raise `UnboundLocalError` at startup (the real crash). Added a
  subprocess startup smoke test (`test_main_window_builds_in_subprocess`) that boots the real window offscreen
  and exercises `_run_on_ui`. `core/qt_bootstrap.py`: new `configure_dpi_awareness()` sets per-monitor-v2
  before `QApplication` (kills the `SetProcessDpiAwarenessContext() failed` notice). Verification: `py_compile`
  clean; full suite **156 passed**; all six harnesses (`accept_instructions/live_toggle/providers/wakeword/
  local_speech/avatar.py`) → ALL CRITERIA PASS; `precommit_check.py --quick` SAFE; DPI proven via
  `AreDpiAwarenessContextsEqual`. Committed locally (no push).
- 2026-10-16 — **Python-version requirement made consistent (user-decided: robust fallback chain).**
  `EVO.bat` no longer hard-pins `py -3.11`; it now prefers the repo `.venv` (created by
  `bootstrap.ps1` / `start_brahma.bat`), then probes `py -3.12`, `py -3.11`, `py -3`, `python` —
  the same style as `install_speech_deps.ps1`. Added `BRAHMA_PRINT_PY=1` to print the chosen
  interpreter and exit without launching (for verification/CI). `bootstrap.ps1` now prefers a
  supported interpreter (`py -3.12` / `-3.11`) when creating the venv, so a bare `py` default
  (e.g. 3.14) can no longer create an out-of-range venv; `install_speech_deps.ps1` gained a
  `py -3.12` probe. README already claims 3.11 | 3.12, which is now accurate. Verified:
  `BRAHMA_PRINT_PY=1 EVO.bat` → the `.venv` python when present, else `py -3.11` (3.12 absent on
  this host); both PowerShell scripts parse cleanly. Not pushed.
- 2026-10-16 — **Item 674 fixed: `QPushButton` "Could not parse stylesheet".** `smart_home_page_new.py`
  `_soft_button_style()` mixed an f-string line with a plain string ending in `}}`; escaping only works
  in f-strings, so a literal `}}` unbalanced the sheet. Fixed the trailing brace to `}`. Located with an
  offscreen harness (wrapped `QWidget.setStyleSheet` + `qInstallMessageHandler`): 2 startup warnings,
  both this button; 0 after the fix. Added `tests/test_smart_home_styles.py`. Full suite **158 passed**.
  Not pushed.
