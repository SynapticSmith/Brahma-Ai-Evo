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
- [ ] **P1-7 — MCP `_status`/`_tasks` lock inconsistency.** Written from the loop thread (`_set_state`
  `core/mcp_manager.py:776`) and read from others without a consistent lock.
- [ ] **P1-8 — MCP shutdown race.** `mcp_manager.stop_all` (`core/mcp_manager.py:512-521`) waits 5s then
  `loop.stop()` with the task possibly still pending → possible orphaned stdio child. (Correction: normal
  cancellation *does* tear the transport down via `async with connect()` at `mcp_manager.py:595`.)
- [ ] **P1-9 — MCP circuit breaker.** `_supervise` (`mcp_manager.py:542-566`) retries forever capped at 30 s;
  add a max-retry → "permanently failed" state surfaced in the panel.

---

## P2 — reliability & error handling

- [ ] **P2-10 — `_is_gemini_limit_error` over-broad + sticky.** `main.py:492-504` matches
  `"exceeded"/"1008"/"permission denied"`; `_use_openrouter_first` latches for the process lifetime
  (`main.py:6047-6048`, never reset after init).
- [ ] **P2-11 — MCP tool-name collisions unchecked.** `core/tool_bridge.py:751-769` sanitizes but never
  detects a clash with native/skill/other-MCP names.
- [ ] **P2-12 — Unbounded caches.** TTS `_probe_cache` (`core/speech_tts.py:214-220`), STT `_model_cache`
  (`core/speech_stt.py:177`) never evict on engine switch; koko probe can spawn a child tree under a 90s
  timeout.
- [ ] **P2-13 — Cleanup-path `except Exception: pass`.** e.g. `core/mcp_manager.py:509-521`,
  `core/avatar_runtime.py:167-178` — downgrade to `logger.debug`.

---

## P3 — testing & CI (highest leverage)

- [ ] **P3-14 — No CI / no lint-type config.** No `.github/`, no `pyproject.toml`/`pytest.ini`; `.git/hooks`
  empty; gate is manual `scripts/precommit_check.py`. Add a CI workflow (pytest + accept harnesses).
- [ ] **P3-14b — Dependencies fully unpinned.** `requirements.txt` (0 `==` in 69 lines).
- [ ] **P3-15 — Unmarked `ZeroDivisionError` fixtures.** `actions/test_action.py:9-10`; `test_self_heal.py:28`
  imports it, so bare `pytest` double-collects. Mark `xfail` or fix.
- [ ] **P3-16 — Interactive scripts pollute root collection.** `test_local_brain.py`, `test_sensorium_live.py`,
  `test_self_heal.py` are not pytest tests and can hang.
- [ ] **P3-17 — Duplicated `check()` across 7 `accept_*.py`.** Extract a helper (or port to pytest);
  `precommit_check.py` scrapes stdout for `"ALL CRITERIA PASS"`.
- [ ] **P3-18 — Install `precommit_check.py` as an actual git hook.**

---

## P4 — maintainability & hygiene

- [ ] **P4-19 — Split god objects.** `ui.py` ~16k lines; `BrahmaLive` ~3.9k lines / 122 methods
  (`main.py:2162-6068`). Seams: session state machine, audio I/O, provider router, tool executor; and
  settings/chat/avatar/MCP panels.
- [ ] **P4-20 — Import-time side effects.** `main.py:6-19` mutates Qt/env; `import core.boot_sentry` (`main.py:28`)
  can roll back files before the UI; `ui.py:19-22` sets WebEngine flags; `QApplication` constructed inside
  `BrahmaUI.__init__`.
- [ ] **P4-21 — Repo weight.** Vendored `three.min.js` ×3 trees; `hand_landmarker.task` 7.8 MB tracked.
- [ ] **P4-22 — Stale doc.** `docs/ONE-391-changes.md:329` says `<REDACTED-INTO-USER-DATA-DIR>`; template now
  uses `""` + `mcpServers`.
- [ ] **P4-23 — Portability.** `core/user_paths.py:4-8` hardcodes `%LOCALAPPDATA%` (fine if Windows-only is
  intended — state it).

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
