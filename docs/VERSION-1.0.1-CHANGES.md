# Version 1.0.1 — Changes

Everything that landed in this release, from the original provider/MCP request
through the code-review pass, the UI god-object decomposition and the final
per-item fixes. Written so someone reviewing the diff — or someone six months
from now — can tell what is here, why it is here, what it costs, and which parts
are still unverified.

**Scope of this document:** the `main` branch of `Brahma-Ai-Evo`, from the last
upstream commit `e0bd3c5` ("fix: unify app settings path, resolve UI provider
switching, fix briefing speech, and restore Charon voice", 2026-10-01) through
`eaa1dbc` ("Feat: item-672 add Gemini as an opt-in TTS engine", 2026-10-16).

**Companion documents referenced by this record** (all in `docs/`):

| document | what it covers |
|---|---|
| `ONE-391-changes.md` | The original feature drop: providers, tool index, speech, instructions, avatars |
| `ONE-391-tool-index.md` | The tool & skill index engine design and measurements |
| `ONE-391-avatar-backends.md` | The four avatar renderers and their contract |
| `ONE-391-next-steps.md` | Remaining follow-ups and deliberately-deferred work |
| `CODE-REVIEW-TODO.md` | The P0–P4 review pass, work log and per-item verification |

(`agent-orchestrator-bridge.md` is deliberately not referenced here.)

---

## 1. The one-paragraph version

Brahma Evo could only talk to three model vendors, could not load a tool list
big enough to be useful, and had one hardcoded visual. This release added a
vendor-agnostic provider router with per-provider kill switches, a tool-and-skill
index that keeps 173 tools down to 24 on the wire, pluggable local speech plus a
Gemini TTS engine, per-layer optional prompt instructions, and four
interchangeable avatar renderers behind one three-method contract — then spent
two days hardening it (concurrency, error handling, CI) and decomposing three
god objects (`ui.py`, `BrahmaUI`/`MainWindow`, `BrahmaLive`) into ~50 seam
modules without changing behaviour.

---

## 2. History at a glance

| | |
|---|---|
| Branch | `main` |
| Starting point | `e0bd3c5` (upstream, 2026-10-01) |
| End point | `eaa1dbc` (2026-10-16 17:42:01 -0500) |
| Commits in this release | **100** (2026-10-14 17:46 → 2026-10-16 17:42) |
| Test suite | 110 → **167 passed** |
| `ui.py` | **17,247 → 816 lines** |
| `main.py` | **~6,551 → ~4,224 lines** |
| `BrahmaLive` | **~3,900 lines / 122 methods → 1,701 lines / 72 methods** |

Work landed in four overlapping waves:

1. **Feature drop (Oct 14)** — provider chain, tool index, speech, instructions,
   avatar renderers, wiring, acceptance harnesses.
2. **Code-review pass (Oct 15)** — P0 correctness, P1 concurrency, P2
   reliability, P3 testing/CI, P4 maintainability; tracked in
   `CODE-REVIEW-TODO.md`.
3. **God-object decomposition (Oct 15–16)** — P4-19: `ui.py` panels and
   `MainWindow`/`BrahmaUI` mixins, plus the eight `BrahmaLive` seams.
4. **Item fixes (Oct 16)** — startup regression, DPI warning, stylesheet parse
   error, Python-version consistency, Gemini TTS, lint gate, lazy WebEngine.

---

## 3. Provider layer — `core/provider_router.py`

A single place that knows which providers exist and whether each may be called.

```
gemini > openrouter > local > custom
```

- **Gemini** is the only provider bound to Google's SDK. That is not a choice:
  Gemini Live is a bidirectional audio WebSocket and cannot be spoken over REST.
- **OpenRouter, Local and Custom** share one OpenAI-compatible adapter. Adding
  a vendor is a registry entry plus a URL, not a new branch in several files.
- Every link has a **kill switch** (`provider_enabled_*`), checked *before* the
  call. This is what stops a disabled OpenRouter from still being walked through
  a 22-model free pool on a machine already busy with a local model.

**Failover policy was extracted and made testable.** The Gemini→OpenRouter
throttle policy (`_prefer_openrouter` / `_throttled_to_openrouter` plus two
attributes) moved to `core/provider_failover.py::ProviderFailover`, unit-tested
in `tests/test_provider_failover.py`; `BrahmaLive` keeps thin defensive wrappers.
`core.provider_router` remains the *provider registry* (kill switches), a
different concern.

**Throttle detection was narrowed.** `_is_gemini_limit_error` originally matched
too broadly and was sticky. The token list is now only genuine throttle/quota
signals (`429`, `resource_exhausted`, `resource has been exhausted`, `quota`,
`rate limit`, `too many requests`, `exceeded`); `1008`, `access denied` and
`permission denied` are dropped so a bad key is not mistaken for a rate limit.
The sticky `_use_openrouter_first` flag was replaced by an `_openrouter_until`
cooldown (default 300 s) consulted through `_prefer_openrouter()` at all four
read sites, so a one-off 429 no longer hides a working Gemini for the whole
session. Failover now only trips on a *real* Gemini throttle.

---

## 4. Tool & skill index — `core/tool_index.py`, `core/tool_bridge.py`

### The problem

| Source | Tools |
|---|---|
| Native `TOOL_DECLARATIONS` | 60 |
| Calyx (`calyx-mcp`) | 31 |
| Synapse (`synapse-mcp`) | 82 |
| **Total** | **173** |

Gemini Live accepts at most **128** function declarations per session, so the
handshake was rejected outright:

```
1007 Request contains an invalid argument
```

### The constraint that decided the design

**Gemini Live cannot swap tools mid-session.** `BidiGenerateContentSetup` is
documented as *"sent in the first (and only in the first) message"*, and a
second `setup` frame mid-stream closes the socket with the same error. So
"retrieve the right k tools each turn and re-send them" is not available on this
transport. The advertised surface must be **small and constant**, with everything
else reachable *through* a tool rather than *as* a tool.

### Design

```
  TOOL_DECLARATIONS ─┐                      ┌── resolve(intent, k)
  DynamicRegistry   ─┼─► ToolIndex ────────►│
  MCP servers       ─┘        │             └── pinned core
                    hybrid retrieval           + search_tools
                    (RRF fusion)               + call_tool
                                               + get_skill_manual
                                        ► bounded declarations per session
```

**Advertised surface:** pinned core tools + `search_tools` / `call_tool` /
`get_skill_manual` + top-k retrieved tools, bounded by `tool_index_budget`.

### Retrieval — three local scorers, fused

| Backend | Basis | Dependency | Latency |
|---|---|---|---|
| `fts5` | SQLite FTS5 / BM25 | stdlib (always on) | ~1 ms |
| `ngram` | hashed word-1/2-gram + char-4-gram TF-IDF cosine | numpy | ~5 ms |
| `embed` | `all-MiniLM-L6-v2` via ONNX | optional `fastembed` | ~3 ms |

Fusion is Reciprocal Rank Fusion (k=60), with two deliberate refinements from
observed failures:

- **Exact-identifier boost.** RRF cannot express "the user said the tool's name,
  so it wins". `synapse_browser_wait_for_url` shares most n-grams with
  `synapse_browser_wait_for`; without the boost the shorter sibling ranks first.
- **Per-source cap.** Fifteen Synapse tools all answer "control the browser".
  Pure MMR treats them as distinct and fills every slot from one server.

Multi-clause requests are scored per clause and fused.

### Measured results

| Metric | Before | After |
|---|---|---|
| Declarations on the setup frame | 173 (rejected) | **24** (accepted) |
| Setup-frame tokens | ~9,000 | **~1,770** (↓80%) |
| Tools unreachable | — | **0** |
| Query latency (p50 / p99) | n/a | **7 ms / 12 ms** (also recorded 19/39 ms under different load) |
| Semantic index build, 173 tools | — | ~0.9 s (background, cached) |

The model is loaded and indexed **off the request path** — `rebuild()` returns in
~200 ms and the semantic backend folds in when ready, so a Live handshake is
never blocked.

### MCP integration needs no manual work

`core/mcp_manager.py` reads the standard MCP self-description surface at connect
time: `initialize.instructions` → `prompts/list` + `prompts/get` →
`resources/list` filtered to usage/guide/manual/readme URIs. Anything found
becomes a skill manual, appended to the system prompt **only on sessions where a
tool from that server is advertised**. Every optional call is bounded and
fail-soft — `prompts/list` raising `method not found` must not stop boot.

### Bugs found by exercising it, not reading it

1. **AB-BA lock-order deadlock** — `rebuild()` held the index lock and took the
   model lock; the worker held the model lock and took the index lock. The
   process hung on the *second* rebuild. Fixed with a dedicated flag lock and an
   explicit ordering rule.
2. **Self-deadlock on a non-reentrant lock** — `build()` called `load()` while
   holding its own plain `Lock`, so the semantic backend silently never
   activated. Caught only by measuring real query latency; fixed with an
   `RLock`.
3. **Torn read across a catalog swap** — the embedding matrix could be replaced
   mid-query while MCP servers re-indexed, raising `KeyError`. Fixed with a
   consistent snapshot that returns `None` rather than mismatched rows.

### Configuration

```json
"tool_index_enabled": true,
"tool_index_budget": 24
```

`%LOCALAPPDATA%\BrahmaAI\tool_routing.json` holds user routing overrides — the
fix path for **description collision** (thin hand-written descriptions such as
`web_search` → *"Searches the web for any information"* route nothing).

### Rejected alternatives

- Local-LLM supervisor router — minutes per turn on CPU-only hardware.
- Calyx as the retrieval backend — a core routing dependency on an external
  executable.
- Per-turn tool re-send — not supported by the Live transport.
- Deleting built-in tools to fit under 128 — re-breaks on the next server.

---

## 5. Speech — `core/speech_stt.py`, `core/speech_tts.py`

| | engines |
|---|---|
| STT | `google` (default, unchanged) → `faster_whisper` → `disabled` |
| TTS | `edge` (default, unchanged) → `piper` → `kokoro` → `sapi` → **`gemini`** (added) |

Defaults are untouched, so nothing changes until an engine is chosen in
Settings → Speech Engines. Gemini Live paths are untouched and asserted as such.

**Offline STT** was measured on real neural speech through the app's own code
path: exact word-for-word transcript, **874 ms for 4.25 s of audio (~4.9× faster
than live)**, with a one-time 1.9 s model load.

### Gemini as an opt-in TTS engine (item 672, final commit `eaa1dbc`)

`core/speech_tts.py` gains a `gemini` engine (label "Google Gemini (cloud)"):

- `_gemini_api_key()` reads `config/api_keys.json` with env fallback
  `GEMINI_API_KEY` / `GOOGLE_API_KEY`.
- `_probe_gemini()` requires the key **and** the installed `google-genai` SDK,
  and makes **no network call**.
- `_speak_gemini()` calls
  `generate_content(model="gemini-2.5-flash-preview-tts",
  response_modalities=["AUDIO"], speech_config=SpeechConfig(voice_config=
  VoiceConfig(prebuilt_voice_config=PrebuiltVoiceConfig(voice_name=…))))`,
  converts the returned 24 kHz PCM to a WAV via `write_wave()`, and plays it on
  the shared player.
- `local: False` — offline mode routes it to SAPI like `edge`, and it is
  deliberately **outside** `_FALLBACK_CHAIN`.
- The Voice setting is treated as a Gemini voice name only when it is **not** a
  path (so a leftover piper file cannot be sent as a voice).
- Settings lists it automatically (the card is built from `available_engines()`).
- Request shape verified against google-genai 2.19.0.
- Tests: `tests/test_speech_tts_gemini.py` (7 tests).

### Speech bugs found by real execution

- `sd.RawInputStream` where `InputStream` was needed — `.tobytes()` on a raw cffi
  buffer; exceptions in a cffi callback are swallowed, so the mic loop ran
  forever doing nothing with no traceback.
- `winsound.SND_SYNC` does not exist in Python's `winsound` — every local voice
  "synthesised correctly and produced no audio".
- Kokoro `q8f16` model **segfaulted onnxruntime** (`0xC0000005`) — uncatchable by
  `try/except`; selecting Kokoro crashed the app every time it spoke. Now
  load-tested in a child process.

---

## 6. Instruction injector — `core/instruction_injector.py`

Blocks are registered with the **layers they apply to**; block and layer are
independently opt-in.

```
layers: live → local → fallback → memory → tool_plan
```

Nothing is injected anywhere until a block is switched on — all five layers
compose to `""` on a fresh install. A layer can also opt out entirely, so a layer
with no avatar is never charged for avatar instructions. `compose()` never
raises, because it runs on the prompt path of a live voice session where an
exception costs the user their conversation.

---

## 7. Avatar renderers — `core/avatar_runtime.py` + three pages

### The contract

A backend is any object with three methods; `core` never imports a backend by
name:

| method | meaning | must |
|---|---|---|
| `probe()` | usable right now? | not raise; return an **actionable** `detail` |
| `set_emotion(tag, intensity=…)` | route one tag | not raise; may return `False` to decline |
| `status()` | one line for diagnostics | not raise |

`probe()` returning a bare `False` is a design error — *"no .vrm file set"* tells
the user what to do, *"unavailable"* does not.

The real axis is **where the pixels are made**: `transport="webengine"` (drawn in
the existing `QWebEngineView` background) vs `transport="osc"` (driven externally
over UDP). Selection is stored **separately** from availability, so a
chosen-but-missing backend stays chosen and the card can say what is missing.

### The four backends

| id | transport | state |
|---|---|---|
| `three_reactive` | webengine | **implemented** — drives the existing 3D background |
| `live2d` | webengine | **implemented**; model installed, Cubism runtime outstanding (user EULA) |
| `vrm` | webengine | **implemented and fully available** |
| `osc_vmc` | osc | **implemented**; needs a VMC host running to be visible |

**Three pages, one hook contract.** `three_reactive`, `live2d` and `vrm` are
separate documents exposing the same seven hooks (`setBrahmaState`,
`setAudioLevel`, `setPointerNorm`, `setAvatarEmotion`, `__pauseAnimation`,
`__resumeAnimation`, `__getAvatarState`). Switching renderer is a **URL swap**
(`active_page_path()`); a test asserts all three expose all seven, so drift fails
a check rather than the avatar silently going dead.

### `three_reactive`

No new asset: the app already renders a reactive WebGL background. An emotion is
a **mood applied on top of that look**. `setBrahmaState` and `setAvatarEmotion`
are two independent inputs — the emotion never writes the state palette;
`currentColor*` stays the pure state look and the emotion blends into separate
`renderColor*` immediately before materials update:

```js
renderColorHot.copy(currentColorHot).lerp(avatarHot, mw * 0.85);
```

Every factor (`energy`, `wobble`, `bloom`, `audio`) is a multiplier around 1.0,
so intensity 0.0 → exact pass-through and switching the avatar off leaves the
background **pixel-identical** — both asserted. Audio-reactive intensity scales
the level (`SORROW` 0.70, `ANGER` 1.60, `SURPRISE` 1.80) rather than replacing
it. Mood decay: `EMOTION_HOLD_SECONDS = 7.0`, asymmetric blend (0.18 in, 0.05
out), last-mood-wins (not a queue).

Delivery: `core.avatar_runtime.set_webengine_sink(fn)`; a mood arriving before
`loadFinished` is **held** and flushed on load. The tag is interpolated with
`json.dumps`, not string concatenation (it is model-produced text going into a
JS source string).

**Both reply paths deliver** — `_fallback_reply` (local/offline/OpenRouter) and
`_receive_audio` (Gemini Live voice, the default mode, which previously dropped
the tag) — through one helper, `BrahmaLive._deliver_avatar_emotion`.

**The push is marshalled to the GUI thread.** `runJavaScript` is GUI-thread-only;
pushing inline crashed the app the moment a tag arrived. `BackgroundWidget`
emits a queued `_emotion_sig` and the actual call runs on the GUI thread.

### `live2d`

A separate page rather than a mode flag, because `assets/web_background/index.html`
already owns a WebGL context and a Cubism model needs its own canvas/core/update
cycle. Two GL contexts on one element would contend.

Two halves fail independently — hence the staged probe:

| half | source | automatable |
|---|---|---|
| model (`*.model3.json` + `.moc3` + textures) | ordinary files | yes — `fetch_live2d_model.py` |
| runtime (`live2dcubismcore.min.js`, `framework.js`) | Cubism SDK for Web | **no** — Live2D's EULA |

`scan_live2d_model()` inspects expressions, parameter count, motion groups and
`.moc3` size **with no SDK present** (a `.model3.json` is plain JSON), and
refuses one that does not reference a `.moc3`.

The `file:` fetch bridge must resolve relative URLs against the document *before*
testing the scheme — otherwise Cubism shader loads fell through to native
`fetch`, sources came back empty, programs failed to link, and every frame logged
`WebGL: INVALID_OPERATION: drawElements: no valid shader program in use` while
nothing was drawn. Visible only in the real app.

Emotion mapping uses expressions first (`exp_02`→JOY/SMILE, `exp_03`→FUN,
`exp_04`→SORROW, `exp_05`→SURPRISE) and parameter nudges for the rest
(ANGER, THINKING, CALM, EXCITED, CONFUSED, LOVE, TIRED). Lip sync rides the
existing audio feed into `ParamMouthOpenY`.

### `osc_vmc`

Project Nova's exact route: `/VMC/Ext/*` over UDP OSC to VSeeFace on
`127.0.0.1:39539`.

- **UDP never fails**, so `available` here means *armed*, never connected. The
  probe says so in words: *"armed at 127.0.0.1:39539 — 0 sent, no reply yet (is
  VSeeFace running?)"*. Off by default.
- It owns a **sender thread** flushing on change, with a 1 Hz keepalive — a VMC
  host that stops hearing `/VMC/Ext/T` returns the avatar to rest, so silence is
  not neutral. The stamp is a monotonic `[frame, seconds]` pair, 32-bit wrapped.
- `BLEND_ORDER` (`Joy Angry Blink Blink_L Blink_R BrowDown BrowUp A S M F`) is a
  **wire format**, not a preference — blend shapes are positional. A test asserts
  it is fixed and unique, that each emotion is *distinguishable* (eight distinct
  poses), and that e.g. `SORROW` does not also raise `Angry`.
- **Verified on the wire, not mocked**: the harness binds a real UDP socket and
  reads six packets over 2.5 s (one mood, one apply, four keepalives).

### `vrm`

**The first backend that actually has everything it needs on a fresh machine.**

**Licensing: verified from the binary, not a README.** VRM carries its own
permission metadata inside the file. `vrm_license_report.py` reads
`allowRedistribution` / `modification` / `commercialUsage` / `avatarPermission`
— the verdict comes from the flags, never from the licence name (the "VRM Public
License 1.0" is a template covering both "do anything" and "do nothing").

| model | verdict |
|---|---|
| `Seed-san.vrm` (10.7 MB, VRM 1.0) | **installed** — VirtualCast, Inc.; redistribution + commercial use granted, attribution required |
| `cryptovoxels.vrm` (238 KB, VRM 0.x) | **installed** — VRoid Hub flags allow redistribution/modification/commercial, credit not required |
| `VRM1_Constraint_Twist_Sample.vrm` (10.7 MB) | permissive — pixiv Inc. © 2022 |
| `Avatar_Orion.vrm` (6.1 MB) | **refused** — declares no licence at all |

The trap that matters: **a model hosted in an MIT repository is not itself MIT.**
`Avatar_Orion.vrm` sits in the same sample repo as models that *are* permissive;
`fetch_vrm_model.py` refuses it.

**The runtime is MIT, so it is vendored:** `three@0.186.1`,
`@pixiv/three-vrm@3.5.5` + four sub-packages → `assets/avatar_vrm/vendor/`
(2.4 MB) with each LICENSE beside it and an import map. **No bundler, no build
step, no network at render time** — a check asserts the page contains no CDN URL.

**Emotions:** standard VRM faces (`happy`, `sad`, `angry`, `relaxed`,
`surprised`) plus `aa` for lip sync and an idle blink. Because a VRM can
legitimately have **no blend shapes at all** (`cryptovoxels.vrm`'s
`expressionManager.expressions` is empty), every emotion also drives a
model-agnostic channel: a procedural pose on a wrapper node (pitch/yaw/roll about
the feet plus a vertical bob) and a scene-light tint. The pose lives on the
wrapper, **not** the humanoid bones, because `three-vrm`'s `lookAt` overwrites
the head bone every `update()`. The page intersects the expression table with
reality, so a model lacking `surprised` skips it instead of throwing.

### Live2D licensing

No CC0/CC-BY model exists — every Live2D model is either Live2D's sample data
under the *Free Material License* or a restricted third-party VTuber model. The
Free Material License permits General Users and Small-Scale Enterprises
(annual sales under 10M JPY) to use the samples commercially and
non-commercially: a permissive grant, simply not an open-source licence. **Ren**
was chosen over Haru/Hiyori/Mao/Mark/Natori/Rice/Wanko: smallest (2.4 MB) and
the only one shipping expression files. The **Cubism runtime cannot be installed
autonomously** — it comes from the SDK for Web after accepting Live2D's EULA.

### Avatar bugs found by testing rather than reading

- `_split_emotion_tag` called as a bare name — `NameError` discarded **every**
  fallback reply; a substring assertion matched both broken and fixed forms.
- `__pauseAnimation` did not exist — the 3D toggle called a hook the page never
  had; the render loop kept re-arming.
- A shared webengine sink flushed any held mood — a live2d mood could be pushed
  into the 3D page if you switched backends mid-hold.

---

## 8. Code-review pass (P0–P4)

Tracked and verified in `CODE-REVIEW-TODO.md`. Checkpoint tag:
`checkpoint/pre-p0-review-fixes` @ `2626164`.

### P0 — correctness

- **P0-1 Local-mode Disconnect/On Call killed the voice loop.** The local-branch
  `_session_task_guard` did not absorb cancellation, so `CancelledError`
  propagated out of `run()` and the thread's `asyncio.run()` exited. Fixed with
  an inner `try/except asyncio.CancelledError` mirroring the Gemini guard.
- **P0-2 Settings writes were not atomic.** Three `ui.py` sites plus
  `config_manager.save_settings` did bare writes; a crash or concurrent writer
  corrupted the JSON (plausible cause of "settings reverted" reports). Fixed with
  `atomic_write_json()` in `core/user_paths.py` (serialize → sibling `mkstemp` →
  write+flush+fsync → `os.replace`, unlink temp on `BaseException`). Tests:
  `tests/test_atomic_settings_write.py`.
- **P0-3 Missing/invalid Gemini key killed the async loop silently.**
  `genai.Client(api_key=_get_api_key())` was built *before* the loop, so it ran
  in Local/offline mode and raised `KeyError`. Fixed: client built lazily inside
  the Gemini branch with one actionable `RuntimeError`, voice status reported,
  STANDBY set, 10 s pause, continue.

### P1 — concurrency & lifecycle

- **P1-4 Lost-wakeup race in reconnect backoff** — fixed with a control-sequence
  counter: `_pause_until` clears the event, then samples `_control_seq`, so a
  wake arriving during the connect attempt returns immediately.
- **P1-5 Wake-word double-open / stale re-arm** — `_stop_wakeword_listener` no
  longer nulls the handles; the worker clears its own slot in a `finally` only
  if still current, and re-checks `stop`/identity after the blocking `listen`.
- **P1-6 Duplicate `_relay_phone_audio` consumer** — session-scoped relay removed
  (it raced the long-lived one); new `_spawn_background(coro, name)` keeps a
  strong ref in `_background_tasks` and logs exceptions.
- **P1-7 MCP `_status`/`_tasks` lock inconsistency** — `_set_state` now takes
  `cls._lock` (RLock); `status()` iterates a materialised list copy.
- **P1-8 MCP shutdown race** — `stop_all` had cleared `_tasks` *before*
  scheduling cancellation so the lookup found nothing; it now cancels the exact
  task objects captured in the snapshot.
- **P1-9 MCP circuit breaker** — `_supervise` counts consecutive connect failures
  (reset on `ready`) and parks a never-connecting config in `failed` after 5
  attempts, surfaced in the panel.

### P2 — reliability & error handling

- **P2-10** throttle detection narrowed + cooldown (see §3).
- **P2-11 MCP tool-name collisions** — `_register_server_tools` tracks sanitized
  names per `tools/list`, refuses to shadow a name claimed by another server or
  a built-in skill, logging each skip. Two tools that sanitize alike (`a.b` vs
  `a-b`) keep the first. Tests: `tests/test_mcp_supervise.py`.
- **P2-12 Unbounded caches** — TTS `_probe_cache` clears at
  `_PROBE_CACHE_LIMIT = 16`; STT `_model_cache` was already bounded
  (clear-then-set, at most one resident model).
- **P2-13 Cleanup-path `except Exception: pass`** — all sites now log at
  `logger.debug` (`mcp_manager`, `avatar_runtime`).

### P3 — testing & CI

- **P3-14 CI workflow** — `.github/workflows/ci.yml` on `windows-latest`:
  stdlib-only static gate (`precommit_check.py --quick`), unit suite
  (`QT_QPA_PLATFORM=offscreen`), and a **lint job** (ruff). Curated blocking
  ruleset in `pyproject.toml` (`F401`, `F841`, `F811`, `F541`, `F821`;
  `F821` per-file-ignored for `core/ui_*.py` mixins and
  `core/session_lifecycle.py`, whose names are injected at runtime via
  `globals().update(deps)`). `requirements-dev.txt` pins `ruff==0.17.1`.
- **P3-14b Dependencies fully pinned** — `requirements.txt` pins every package to
  the version verified on this host (46 `==` pins). `pyttsx3` deliberately left
  unpinned (not installed on the verification host; no known-good version to
  pin). Top-level pins only.
- **P3-15/P3-16 pytest collection** — `pytest.ini` with `testpaths = tests`, so
  demo files (`actions/test_action.py`, `test_self_heal.py`,
  `test_local_brain.py`, `test_sensorium_live.py`) no longer pollute collection.
- **P3-17 Duplicated `check()` across 7 harnesses** — extracted `accept_util.py`;
  the six updated harnesses still print `ALL CRITERIA PASS`
  (`precommit_check.py`'s scrape contract unchanged).
- **P3-18 `precommit_check.py` installed as a git hook** —
  `.git/hooks/pre-commit` (per-clone, untracked by design); fast static pass by
  default, full suite with `PRECOMMIT_FULL=1`.
- **P3-19 auto-heal test reached a live Gemini endpoint (CI blocker)** — the test
  mocked `create_plan` and `_call_tool` but not `analyze_error`, so `execute`
  called `_get_api_key()` → real network call / `FileNotFoundError` on CI. Fixed
  (`2d631a0`, corrected by `65d74f4`) by monkeypatching
  `agent.executor.analyze_error` (+ `generate_fix`) to an
  `ErrorDecision.ABORT` stub; still exercises `record_last_error`.
- **P3-20 Flaky native crash at interpreter shutdown** — UI modules started
  QtWebEngine at **import** time (`core/ui_bg.py`, `core/circuit_hud.py`,
  `core/globe_window.py`, a dead eager import in `ui.py`); Chromium's global
  threads raced Qt teardown at interpreter finalize. Fixed with
  `importlib.util.find_spec` probes and lazy import at first real use. Measured:
  crash-prone pair **4/10 → 0/10**; full suite **~1-in-3 → 0/5**; new
  `tests/test_webengine_lazy_import.py`.

### P4 — maintainability & hygiene

- **P4-19 Split god objects — COMPLETE** (see §9).
- **P4-20 Import-time side effects** — duplicated Qt/env blocks in `main.py`,
  `ui.py` (×2) and `core/globe_window.py` collapsed into one idempotent
  `core/qt_bootstrap.bootstrap()`; `core/boot_sentry` no longer rolls files back
  on import (`main` calls `boot_sentry.run()`); `tests/conftest.py` applies
  `bootstrap()`.
- **P4-21 Repo weight** — `hand_landmarker.task` (7.8 MB) untracked + gitignored
  (downloaded on demand under `%LOCALAPPDATA%`); `three.min.js` committed once at
  `assets/web_background/` (globe loads it via `../web_background/`; the Android
  module copies it at build time via the `syncSharedThree` Gradle task,
  unverified until an Android build runs).
- **P4-22 Stale doc** — `ONE-391-changes.md` credential note corrected to match
  `config/app_settings.json` (`""` + `"mcpServers": {}`).
- **P4-23 Portability** — `core/user_paths.get_user_data_dir` documents
  Windows-first (`%LOCALAPPDATA%\BrahmaAI`) with a home-dir fallback so
  non-Windows tooling does not crash.

---

## 9. God-object decomposition (P4-19)

The single largest structural change in the release. Every extraction was
**verbatim** and verified the same way: AST-identical method bodies, an import
check, full suite green, all seven/eight `accept_*.py` harnesses green, and
`scripts/precommit_check.py --quick` SAFE.

### 9.1 `BrahmaLive` — eight seam extractions

Each reached through a lazily-built host property plus thin delegating wrappers
(defensive `getattr` where harnesses build attributes):

| seam module | contents | size |
|---|---|---|
| `core/provider_failover.py` | Gemini→OpenRouter throttle policy | small |
| `core/tool_exec.py` | `_execute_tool` + 2 wrappers | 727 lines |
| `core/audio_io.py` | mic / receive / playback / speak | ~350 lines |
| `core/reply_flow.py` | reply / IG / email flows | 318 lines |
| `core/session_lifecycle.py` | Live lifecycle (run, serve, wakeword, idle, local voice, pause, connect, build_config) | 618 lines |
| `core/attention_flow.py` | attention / meeting handlers | 188 lines |
| `core/device_flow.py` | smart-home + Brahma-Connect commands | 253 lines |
| `core/realtime_flow.py` | background spawner + dashboard/driver coroutines | 67 lines |

`main.py` shrank 6551 → **4224 lines** along the way. Classes take a host
reference plus the exact main.py symbols they need (no circular import); shared
state flags stay on the host because the Qt surface and other paths also read
them.

**Deliberately not extracted:** `_on_text_command` (658 lines) is one cohesive
dispatcher — its 21 nested `_run_*` closures *are* its content; `_fallback_reply`
(262 lines) is coupled to `accept_instructions.py`'s source-slice guard
(protecting a historical `NameError` in the reply path); the `__init__` body
(109 lines) is irreducible.

### 9.2 `ui.py` — class-level seams (17,247 → 816 lines)

Moved verbatim into leaf modules, with `bind()` injecting only symbols defined
outside the cluster and re-exports keeping every `ui.<Class>` reference
unchanged:

`ui_chat` (2,109 lines, 11 classes) · `ui_connectivity` (4,164, 4 classes) ·
`ui_devices` (1,282) · `ui_theme` (class `C` palette — required as a leaf because
default-argument values evaluate at class-definition time) · `ui_telemetry`
(1,084, 6 classes) · `ui_gesture` (663) · `ui_floating` (494) · `ui_overlays`
(921) · `ui_setup` (1,051) · `ui_transients` (1,053) · `ui_commandbar` (207) ·
`ui_filedrop` (193) · `ui_sysmetrics` (166) · `ui_branding` (70) · `ui_widgets`
(67) · `ui_startup` (109) · `ui_bg` (351) · `ui_devmode` (84).

A scope-accurate free-name audit (`symtable`) rebuilt each moved module's import
header — auditing at class-node granularity wrongly cancels a name that is a
local in one method but a module-global read in another (it had silently dropped
`json`, `Path`, `QApplication`, `QLineEdit`, `QMessageBox`, and earlier
`os`/`threading`/`time`).

### 9.3 `MainWindow` — 16 mixins (97 methods / 1,945 lines → `__init__` + 6 methods / 235 lines)

`ui_discord` · `ui_appsettings` · `ui_startup_os` · `ui_avatar_audio` ·
`ui_overlays_cards` · `ui_mute_send` · `ui_meeting` · `ui_nav` · `ui_remote` ·
`ui_secondary_overlays` · `ui_state_appliers` · `ui_setup_flow` · `ui_panels` ·
`ui_wiring` · `ui_runtime` · `ui_apply_state`.

### 9.4 `BrahmaUI` — 12 mixins (`__init__`-only / 95 lines)

`ui_shell_settings` · `ui_shell_discord` · `ui_shell_boot` · `ui_shell_tray` ·
`ui_shell_devmode` · `ui_shell_sidebar` · `ui_shell_launcher` ·
`ui_shell_controlpanel` · `ui_shell_panel_settings` · `ui_shell_actions` ·
`ui_shell_delegates` (45-def forwarding/state surface) · `ui_shell_window`.

Both god classes are now **"constructor + mixins only"**. The irreducible
remainder: the two `__init__` bodies, `_RootShim`, and the module-level
bootstrap. `_base_dir` and `_new_setStyleSheet` are deliberately left in `ui.py`
(`__file__` semantics; order-sensitive import-time monkeypatch).

A `gen_ui_mixin.py` generator drove the method-level splits, gaining
capabilities as cases demanded: `TARGET_CLASS` env var, refusal to move methods
using `super()`, refusal to overwrite an existing module lacking the target
mixin, decorator/property collection, shared-name collection, and a
`DIRECT_IMPORTS_JSON` env hook for direct imports where `bind()` would NameError.

---

## 10. Runtime fixes (item-numbered)

- **item 675 — startup crash + cross-thread QWidget access.** The original
  hypothesis (MainWindow built on a worker thread) was false: the real cause is
  that `BrahmaUI` delegates and `MainWindow.submit_command` are reached from
  worker threads (mic/audio loop, screen processor, meeting listener,
  daily-briefing loop, wake word, Discord gateway, phone gateway) and touched
  QWidgets directly. Every such call now funnels through one GUI-thread
  marshaller, `core/ui_thread.run_on_ui(win, fn)` → `MainWindow._run_on_ui`
  (queued over the `_ui_call_sig` signal when off the GUI thread, inline
  otherwise). **Follow-up fix:** the marshaller's `threading.get_ident()` landed
  before a pre-existing redundant local `import threading` inside
  `MainWindow.__init__`, making `threading` a method-local name and raising
  `UnboundLocalError` at startup — removed, plus a subprocess startup smoke
  test. Tests: `tests/test_ui_thread_marshalling.py` (6 tests).
- **DPI awareness warning.** `SetProcessDpiAwarenessContext() failed: Access is
  denied` — Qt sets `PER_MONITOR_AWARE_V2` while constructing `QGuiApplication`,
  but an earlier helper (manifest / pywinauto / mss) had already fixed a
  *different* context. `bootstrap()` now fixes the process to the same V2 context
  before `QApplication` exists (`configure_dpi_awareness()`, pseudo-handle `-4`),
  so Qt's call becomes idempotent. Silent on failure;
  `BRAHMA_DPI_AWARENESS=off` opts out. Verified with
  `AreDpiAwarenessContextsEqual`.
- **item 674 — `QPushButton` "Could not parse stylesheet".**
  `smart_home_page_new._soft_button_style()` concatenated an f-string line with
  a **plain (non-f) string** ending in `}}`. Brace escaping only applies inside
  f-strings, so the plain line emitted a literal `}}`, yielding an unbalanced
  sheet (`QPushButton { … } }QPushButton:hover { … }`). Fixed to a single `}`.
  Located with an offscreen harness wrapping `QWidget.setStyleSheet` +
  `qInstallMessageHandler` (2 startup warnings → 0) and an AST scan for
  `{{`/`}}` in non-f string constants. Test: `tests/test_smart_home_styles.py`.
- **Python-version requirement made consistent (fallback chain).** `EVO.bat` no
  longer hard-pins `py -3.11`; it prefers the repo `.venv`, then probes
  `py -3.12` → `py -3.11` → `py -3` → `python`. `BRAHMA_PRINT_PY=1` prints the
  chosen interpreter and exits (for verification/CI). `bootstrap.ps1` prefers a
  supported interpreter when creating the venv so a bare `py` default (e.g.
  3.14) cannot create an out-of-range venv; `install_speech_deps.ps1` gained a
  `py -3.12` probe.
- **Live session / MCP / speech reliability** — see §8 P0–P2.
- **Reconnect wakeup race, wake-word re-arm, background task lifetime** — see §8
  P1-4..P1-9.

---

## 11. Security & credentials

- **Credentials redacted from the tracked settings template** (commit
  `6dcffd9`). Two live credentials had sat unnoticed in
  `config/app_settings.json` because that file is git-tracked and is **not** the
  file the app reads — `config_manager` resolves to
  `%LOCALAPPDATA%\BrahmaAI\config`. Both values are now empty strings (`""`),
  alongside an added `"mcpServers": {}`.
- **`scripts/precommit_check.py`** blocks on credentials in any committable
  file, on a vendored build committed without its licence, and on a failing test
  suite; it also reports large files and ignored-but-present files. Installed as
  a real git hook (`.git/hooks/pre-commit`).
- **Licence provenance is enforced, not assumed** — see §7 (VRM flags read from
  the binary; Live2D licence text installed beside the model with a
  `PROVENANCE.json` containing source repo, branch and per-file SHA-256).

---

## 12. Verification status

### Verified

- **167 unit tests** pass (from 110 at the start of the review pass).
- Seven/eight acceptance harnesses pass: `accept_391`, `accept_avatar` (219
  checks), `accept_instructions`, `accept_providers`, `accept_local_speech`,
  `accept_live_toggle`, `accept_wakeword`.
- A real Gemini Live handshake: 24 tools accepted, sessions opened **and
  closed** twice.
- Offline STT on real neural speech, word-for-word correct.
- OSC packets read off a **live UDP socket**, not a mock.
- VRM and Live2D licences read out of the actual model files.
- `precommit_check.py` and `install_optional_deps.ps1` executed end to end.
- `ruff check .` → **All checks passed!**
- Lazy-WebEngine fix measured: crash-prone pair 4/10 → 0/10; full suite
  ~1-in-3 → 0/5.
- DPI fix proven via `AreDpiAwarenessContextsEqual`.

### Not verified — and not claimed

- **No renderer has been seen drawing a pixel.** The Live2D EULA is the user's
  to accept, and the WebEngine render needs a human screen.
- **No microphone round trip into a local model.**
- **`accept_391.py`'s p99 latency assertion is flaky** (~35 samples, so p99 is
  effectively the max). It measures 17–31 ms p50 against a 50 ms ceiling and
  fails under CPU contention. The threshold was deliberately left alone rather
  than loosened to make the run green.
- **Risk concentration:** the microphone path, the WebEngine render, and the
  Gemini Live audio session — each needs one human run before it is trusted.

---

## 13. Known issues & next steps

From `ONE-391-next-steps.md` and `CODE-REVIEW-TODO.md`:

1. **`config/app_settings.json` is still a trap** — it looks like the config and
   is not what the app reads. Recommended: rename to
   `config/app_settings.example.json` (or gitignore it with a committed
   `.example.json`).
2. **The tool index has no cold-start benchmark** — everything measured is warm;
   process launch + first tool query is the number a user experiences.
3. **`fastembed` remains commented out of `requirements.txt`** — it downloads a
   model at install time; the honest place for that is an explicit Settings
   opt-in, not a line in requirements.
4. **Provider routing is first-match, not capability-aware** — `chain()` is a
   fixed order with no notion of "I need tools" / "I need vision" / "I need
   cheap". A capability filter per provider is the highest-value structural
   improvement left.
5. **Background memory extraction should have an idle queue** — currently a
   straight on/off; buffering and extracting after 2–3 idle minutes would get
   memory *and* responsiveness.
6. **No lip-sync verification against a real microphone.**
7. **Two user-owned assets remain:** the Live2D Cubism runtime (EULA click,
   `assets/avatar_live2d/vendor/`) and a VMC host (VSeeFace on
   `127.0.0.1:39539`).
8. **`fastembed` cold start / semantic backend** and **Android
   `syncSharedThree` task** are unverified until their respective builds run.

---

## 14. Files added or changed

### New source
```
core/provider_router.py       core/tool_index.py        core/tool_bridge.py
core/mcp_manager.py           core/speech_stt.py        core/speech_tts.py
core/instruction_injector.py  core/avatar_runtime.py    core/provider_failover.py
core/tool_exec.py             core/audio_io.py          core/reply_flow.py
core/session_lifecycle.py     core/attention_flow.py    core/device_flow.py
core/realtime_flow.py         core/ui_thread.py         core/qt_bootstrap.py
core/ui_*.py (18 leaf modules) core/ui_shell_*.py (12 mixins)
core/ui_*_*.py (16 MainWindow mixins)
assets/avatar_live2d/index.html    assets/avatar_vrm/index.html
assets/avatar_vrm/vendor/   (three + @pixiv/three-vrm, MIT, 2.4 MB)
```

### Installers and tooling
```
scripts/install_optional_deps.ps1   scripts/precommit_check.py
fetch_live2d_model.py   fetch_vrm_model.py   vendor_vrm_runtime.py
vrm_license_report.py   install_speech_deps.ps1   download_offline_voices.py
bootstrap.ps1   EVO.bat   gen_ui_mixin.py
```

### Tests
```
tests/test_tool_index.py        tests/test_tool_bridge.py
tests/test_mcp_instructions.py  tests/test_mcp_supervise.py
tests/test_atomic_settings_write.py   tests/test_provider_failover.py
tests/test_speech_tts_gemini.py tests/test_smart_home_styles.py
tests/test_ui_thread_marshalling.py   tests/test_webengine_lazy_import.py
accept_util.py
accept_391.py  accept_avatar.py  accept_instructions.py  accept_providers.py
accept_local_speech.py  accept_live_toggle.py  accept_wakeword.py
live_handshake_probe.py
```

### Configuration / CI
```
.github/workflows/ci.yml   pyproject.toml   pytest.ini
requirements.txt (46 pins) requirements-dev.txt (ruff==0.17.1)
.gitignore   config/app_settings.json
```

### Modified
```
main.py    ui.py    llm_client.py    or_client.py    core/local_brain.py
core/dynamic_registry.py    core/prompt.txt    core/config_manager.py
core/user_paths.py    core/boot_sentry    core/globe_window.py
core/circuit_hud.py    smart_home_page_new.py
actions/attention_monitor.py   actions/auto_heal_engine.py
actions/browser_control.py     actions/meeting_assistant.py
features/tests_my_internet_speed.py   features/make_stickman_dance_when.py
assets/web_background/index.html
tests/test_daily_briefing.py   tests/test_screen_processor.py
```

---

## 15. Risk summary

The code is in good shape — 167 unit tests, seven acceptance harnesses, real
protocol verification where it was possible, a green lint gate and a CI workflow.
The risk is concentrated in the three places no automated check can reach: **the
microphone path, the WebEngine render, and the Gemini Live audio session.** Each
needs one human run before it is trusted, and each has surprised before.

---

## 16. Post-release (2026-10-18): the kill switches made real

The release shipped the provider chain, but a local-only configuration still
sent Google/OpenRouter traffic from background threads, and a dead provider
surface answered the user with a canned "I'm ready, sir." A six-defect
diagnosis (log section 14.1) fixed the whole class at once:

* the reply ladder attempts the **selected** provider first, then the
  remaining enabled chain, and surfaces the real provider error instead of the
  canned reply;
* `core/local_brain.py` was rewritten around the Settings endpoint/model with
  availability probes, bounded timeouts and response validation;
* **`core/llm_gateway.py`** became the single egress for auxiliary LLM calls
  (~34 call sites across 24 files): a disabled provider is never contacted,
  failover follows the chain selected-first, `AuxResponse` keeps SDK-shaped
  parsers working, `GatewayError` joins every link's reason, and grounded
  tasks stay Gemini-only so live search is never faked from memory;
* the Gemini TTS engine is gated behind the same kill switch in `speak()`;
* `llm_client` gained bounded request timeouts (10 s connect / 180 s read) and
  an explicit-provider `chat_as()`; and
* `sound_manager` defers QtMultimedia to the first played sound, so boot logs
  carry no media-backend probing.

A same-day follow-up sweep (log section 15) closed what review left behind:
`tests/test_llm_gateway.py` (19 network-free tests) pins the routing contract
and immediately caught a real bug (dict-shaped inline-data parts were
stringified into the prompt instead of attached as images);
`accept_providers.py` gained section K proving egress honours the kill
switches end-to-end; the tool-bridge wait is bounded
(`TOOL_EXEC_TIMEOUT_SECONDS = 300`) so a hung tool cannot wedge a reply; nine
dead `_get_api_key()` helpers - and the constants/imports that only fed them -
are gone; the last unbounded `urlopen()` got a timeout; and the repo-local
backup/log clutter plus the 7.6 MB gesture model (downloaded on demand by
`ui_gesture`) left the tree.

Verified: `precommit_check.py` SAFE TO COMMIT - pytest **240 passed**, all
seven acceptance harnesses `ALL CRITERIA PASS`, `ruff` clean. Per-item detail
and evidence live in `docs/VERSION-1.0.1-LOG.md` sections 14-15.

## 17. Post-release (2026-10-18): the system prompt rewritten as an agent brief

The platform work landed, but the assistant still behaved like a chatbot -
describing work instead of doing it, stopping after one tool, asking for facts
its context already carried. The cause was `core/prompt.txt`, a 98-line
persona note written for the old single-engine build. It has been replaced
(107 lines, ~15 KB) with a brief built around how the system actually runs:

* **HOW A TURN WORKS** - a turn is a work session: tool results feed the next
  decision, independent calls go out together, the turn ends when the goal is
  verifiably done; the system's own "Working on X..." announcements are
  documented so the model does not double-narrate;
* **WHAT THIS SYSTEM CAN DO** - a full capability inventory (memory, vision,
  PC, web, documents, people, time/attention, devices, media/wellbeing,
  self-evolution, MCP + Skills Library);
* **TOOL SURFACE / SKILLS LIBRARY** - the discovery loop for the deferred
  tool set (`search_tools` -> `call_tool`, `list_tools`, `get_skill_manual`,
  `search_skills` -> `load_skill`); the `SKILLS LIBRARY` header referenced by
  `docs/skills-library-jit-on-demand-skills.md` is preserved;
* **OPERATING THE TOOLS** - every hard-won routing rule from the old prompt
  kept (preview -> organise, `word_document` vs `file_processor`,
  `browser_control` not `open_app`, device cross-routing, `game_updater`
  exclusivity, `confirmed='yes'` gating) plus routes for the 1.0.1 tool set;
* **MEMORY / SAFETY / OFFLINE / COMMUNICATION** - recall-vs-save doctrine,
  preview-before-bulk and `undo`, honest degradation offline, and the
  spoken-output style (no markdown, bullets or raw URLs).

Two supporting fixes came with it. The prompt more than doubled, which would
have overflowed Ollama's default `num_ctx` (2048-4096 tokens) and silently
clipped the offline 3B model's instructions, so `core/local_brain.py` now
sends an explicit `options: {"num_ctx": 8192}` on both local paths. And the
regression tests exposed that `LocalBrain(default_model=...)` ignored its own
argument - the pin now wins, as the docstring always promised. The stale
tracked duplicate `core/prompt - Copy (2).txt` was removed.

Verified: `precommit_check.py` SAFE TO COMMIT - pytest **247 passed**, all
seven acceptance harnesses `ALL CRITERIA PASS`, `ruff` clean. Per-item detail
and evidence live in `docs/VERSION-1.0.1-LOG.md` section 16.

## 18. Post-release (2026-10-18): slow CPUs are no longer cut off mid-inference

Exercising the app on slow CPU hardware showed local turns being aborted
mid-generation: the local reply path is non-streaming, so its socket budget
covers prompt prefill of the ~4K-token system context *plus* full decode -
several minutes on a CPU-resident 3B model, against a GPU-era 180 s budget.
Raising the constant flat would tax GPU machines with slow failure detection,
and `timeout=None` would reintroduce the section-14 "wedged server holds a
worker forever" defect. Instead the budget is now hardware-aware:

* **`TIMEOUT_PROFILES`** in `core/local_brain.py` - `fast` (180 s) and `slow`
  (600 s), each with a non-streaming `response` and a streaming `first_token`
  budget; both stay finite so failover and surfaced errors survive;
* **auto-detection** - Settings' `local_timeout_profile` (`auto` default /
  `fast` / `slow`) wins when explicit; `auto` asks Ollama's `GET /api/ps`
  where the model is resident (`size_vram == 0` => CPU => slow), cached 60 s
  per endpoint+model, with every inconclusive outcome falling back to fast;
* **two-stage stream watchdog** - streams open with the patient first-token
  budget (prefill) and tighten to the 60 s inter-chunk budget after the
  first byte, so a stalled stream still fails fast;
* **one budget, one source of truth** - the gateway's local link
  (`llm_client.chat_as("local", ...)`) borrows `local_brain.response_timeout()`
  instead of a second conflicting constant, so memory extraction and intent
  parsing get the same patience as the reply path.

Verified: `precommit_check.py` SAFE TO COMMIT - pytest **258 passed**, all
seven acceptance harnesses `ALL CRITERIA PASS`, `ruff` clean. Per-item detail
and evidence live in `docs/VERSION-1.0.1-LOG.md` section 17.

---

*See `ONE-391-changes.md`, `ONE-391-tool-index.md`, `ONE-391-avatar-backends.md`,
`ONE-391-next-steps.md` and `CODE-REVIEW-TODO.md` for the deep detail behind
each section above.*
