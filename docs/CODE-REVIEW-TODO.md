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
  - Remaining seams: session state machine and the `ui.py` panels.
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
- [ ] **Fix `QPushButton` "Could not parse stylesheet" error.** NOT done. Cause not yet located.
- [ ] **Fix `QObject::setParent: Cannot set parent, new parent is in a different thread` (×9).** NOT done.
  Related root causes surfaced by the review: `ui.py` builds `MainWindow` on a worker thread and moves
  widgets across threads — `setParent(None)` at `ui.py:12626`, `6922`, `6934`, `11667`, plus
  `threading.Thread(target=self.on_text_command…)` at `ui.py:9730` (+`11026`, `12604`, `1678`, `16954`).
- [ ] **Address the DPI awareness warning (qt.conf).** NOT done. `SetProcessDpiAwarenessContext() failed:
  Access is denied` — likely related to P4-20 (import-time Qt env / QApplication timing).

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
