# ONE-391 — Complete change record

Everything added to Brahma Evo on this issue, from the original provider/MCP
request through to the four avatar renderers. Written so someone reviewing the
diff — or someone six months from now — can tell what is here, why it is here,
what it costs, and which parts are still unverified.

**Nothing in this work has been committed.** HEAD is still the upstream
`e0bd3c5`. Everything described below lives in the working tree.

---

## 1. The one-paragraph version

Brahma Evo could only talk to three model vendors, could not load a tool list
big enough to be useful, and had one hardcoded visual. This issue added: a
vendor-agnostic provider router with kill switches, a tool-and-skill index that
keeps 173 tools down to ~24 on the wire, pluggable local speech, per-layer
optional prompt instructions, and four interchangeable avatar renderers behind
one three-method contract.

---

## 2. What was added, by area

### 2.1 Provider chain — `core/provider_router.py`

A single place that knows which providers exist and whether each may be called.

```
gemini > openrouter > local > custom
```

- **Gemini** is the only provider bound to Google's SDK. That is not a choice:
  Gemini Live is a bidirectional audio WebSocket and cannot be spoken over REST.
- **OpenRouter, Local and Custom** share one OpenAI-compatible adapter. Adding
  a vendor is a registry entry plus a URL, not a new branch in several files.
- Every link has a **kill switch** (`provider_enabled_*`), checked *before* the
  call. This is what stops a disabled OpenRouter from still being walked
  through a 22-model × 60 s free pool on a machine already busy with a local
  model.

### 2.2 Tool and skill index — `core/tool_index.py`, `core/tool_bridge.py`

The motivating problem: 173 tools (60 native + 31 Calyx + 82 Synapse) exceeded
Gemini's hard 128-tool protocol limit and the session was rejected outright.

| | before | after |
|---|---|---|
| tools in the setup frame | **173 → rejected** | **24 → accepted** |
| prompt tokens | ~9,006 | ~1,309 (**−80%**) |
| unreachable tools | — | **0** |
| query latency | n/a | p50 ~19 ms / p99 ~39 ms |

Three local retrieval backends, fused with RRF:

- `fts5` — SQLite FTS5/BM25, stdlib, always on
- `ngram` — hashed word/bigram + char-4gram TF-IDF cosine in numpy
- `embed` — `all-MiniLM-L6-v2` via ONNX, **optional**, loaded on a background thread

Two refinements over plain RRF, both from observed failures: an **exact-identifier
boost** (`synapse_browser_wait_for_url` shares most n-grams with `..._wait_for`,
and pure rank fusion put the shorter sibling first) and a **per-source cap** (15
Synapse tools all answer "control the browser", and pure MMR treats them as
distinct).

**Why 3 meta-tools and not "retrieve top-k each turn":** `BidiGenerateContentSetup`
is documented as *"sent in the first (and only in the first) message"*, and a
second `setup` frame mid-stream closes the socket with the same 1007 error. So
the advertised surface must be **small and constant**:

```
pinned core  +  search_tools / call_tool / get_skill_manual  +  top-k retrieved
```

**Adding an MCP server needs zero manual work.** `core/mcp_manager.py` reads the
standard self-description surface at connect time — `initialize.instructions`,
then `prompts/list` + `prompts/get`, then `resources/list` filtered to
usage/guide/manual URIs. Every optional call is bounded and fail-soft, because
`prompts/list` raising `method not found` must not stop boot.

### 2.3 Pluggable speech — `core/speech_stt.py`, `core/speech_tts.py`

| | engines |
|---|---|
| STT | `google` (default, unchanged) · `faster_whisper` · `disabled` |
| TTS | `edge` (default, unchanged) · `piper` · `kokoro` · `sapi` |

Defaults are untouched, so nothing changes until an engine is chosen in
Settings → Speech Engines. Gemini Live paths are untouched and asserted as such.

Offline STT was measured on real neural speech through the app's own code path:
exact word-for-word transcript, **874 ms for 4.25 s of audio (~4.9× faster than
live)**, with a one-time 1.9 s model load.

### 2.4 Instruction injector — `core/instruction_injector.py`

Blocks are registered with the **layers they apply to**; block and layer are
independently opt-in.

```
layers: live · local · fallback · memory · tool_plan
```

Nothing is injected anywhere until a block is switched on — all five layers
compose to `""` on a fresh install. A layer can also opt out entirely, so a layer
with no avatar is never charged for avatar instructions. `compose()` never
raises, because it runs on the prompt path of a live voice session where an
exception costs the user their conversation.

### 2.5 Avatar runtime — `core/avatar_runtime.py` + three pages

The seam. A backend is any object with three methods:

| method | meaning | must |
|---|---|---|
| `probe()` | usable right now? | not raise; return an **actionable** `detail` |
| `set_emotion(tag, intensity=…)` | route one tag | not raise; may return `False` to decline |
| `status()` | one line for diagnostics | not raise |

`probe()` returning a bare `False` is a design error — "no .vrm file set" tells
the user what to do, "unavailable" does not.

Four backends, all implemented:

| id | transport | state |
|---|---|---|
| `three_reactive` | webengine | works — drives the existing 3D background |
| `live2d` | webengine | built; blocked on a proprietary runtime |
| `vrm` | webengine | **fully available** |
| `osc_vmc` | osc | built; needs a VMC host running to be visible |

**Three pages, one hook contract.** `three_reactive`, `live2d` and `vrm` are
separate documents that all expose the same seven hooks (`setBrahmaState`,
`setAudioLevel`, `setPointerNorm`, `setAvatarEmotion`, `__pauseAnimation`,
`__resumeAnimation`, `__getAvatarState`). Python swaps a URL and never learns
which renderer is mounted. A test asserts all three expose all seven, so drift
fails a check rather than the avatar silently going dead.

---

## 3. External dependencies added

Nothing here is required to run the app. Everything is optional and selected in
Settings.

### Python packages

| package | for | required? |
|---|---|---|
| `faster-whisper` + `ctranslate2` + `av` | offline STT | optional |
| `piper-tts` | offline TTS | optional |
| `kokoro-onnx` + `onnxruntime` | offline TTS | optional |
| `SpeechRecognition` | On-Call wake word | optional |
| `python-osc` 1.10.2 | VMC / OSC avatar | optional |
| `fastembed` | semantic tool retrieval | **commented out of requirements.txt** |

`fastembed` is commented out rather than added: it downloads a model at install
time, and that is the user's call, not something to slip in.

### Node packages (vendored, then the node_modules deleted)

| package | version | licence |
|---|---|---|
| `three` | 0.186.1 | MIT |
| `@pixiv/three-vrm` | 3.5.5 | MIT |
| `@pixiv/three-vrm-core` | 3.5.5 | MIT |
| `@pixiv/three-vrm-materials-mtoon` | 3.5.5 | MIT |
| `@pixiv/three-vrm-materials-v0compat` | 3.5.5 | MIT |
| `@pixiv/three-vrm-springbone` | 3.5.5 | MIT |
| `@pixiv/three-vrm-node-constraint` | 3.5.5 | MIT |

Copied into `assets/avatar_vrm/vendor/` (2.4 MB) with each package's LICENSE
beside it and an import map resolving the bare specifiers. **No bundler, no
build step, no network at render time** — a local desktop app should not have a
renderer that silently shows nothing on a bad connection.

### Models (installed to `%LOCALAPPDATA%\BrahmaAI\avatar\`, never into the repo)

| model | size | licence | installed by |
|---|---|---|---|
| Live2D **Ren** | 2.4 MB | Live2D Free Material License | `fetch_live2d_model.py` |
| VRM **cryptovoxels** | 238 KB | permissive (VRoid Hub flags in-file) | `fetch_vrm_model.py` |
| VRM **seed-san** (VRM 1.0) | 10.7 MB | VRM Public License 1.0, permissive in-file | `fetch_vrm_model.py` |
| faster-whisper **base.en** | ~140 MB | MIT | `install_optional_deps.ps1` |

Both installers write a licence record, a `PROVENANCE` JSON (source repo and a
SHA-256) and the source README alongside the model. VRM names each record after
the model (`LICENSE.<name>.txt`, `PROVENANCE.<name>.json`,
`SOURCE-README.<name>.md`) so several VRM models can sit side by side without
one install overwriting another's provenance.

---

## 4. Licensing — what was actually checked

This was not assumed. `vrm_license_report.py` reads the licence **out of the
VRM binary**, because VRM carries its own permission metadata
(`licenseUrl`, or VRoid Hub's `otherLicenseUrl` flags, plus `authors`,
`copyrightInformation`, `commercialUsage`, `modification`).

| candidate | verdict |
|---|---|
| `Seed-san.vrm` (madjin/vrm-samples, 10.7 MB, VRM 1.0) | **installed** — VirtualCast, Inc.; redistribution and corporation use granted in-file, attribution required |
| `cryptovoxels.vrm` (238 KB, VRM 0.x) | **installed** — VRoid Hub flags: redistribution ✓ modification ✓ commercial ✓ credit not required |
| `VRM1_Constraint_Twist_Sample.vrm` (three-vrm, 10.7 MB, VRM 1.0) | permissive — pixiv Inc. © 2022; the file grants redistribution too |
| `Avatar_Orion.vrm` (6.1 MB) | **refused** — declares no licence at all |

The **"VRM Public License 1.0"** is a template, not a fixed licence: the
author's License Settings are written into the file (`allowRedistribution`,
`modification`, `commercialUsage`, `avatarPermission`). The report judges those
flags, not the licence name — the same name covers "do anything" and "do
nothing", so a file that cites the URL without the flags is `unknown`, never
assumed permissive.

The trap that matters is caught: **a model hosted in an MIT repository that is
not itself MIT.** `Avatar_Orion.vrm` sits in the same sample repo as the models
that *are* permissive, and `fetch_vrm_model.py` refuses it.

Live2D is different and there is no CC0 alternative — every model is either
Live2D's sample data under the Free Material License or a third-party VTuber's
restricted model. Ren is the smallest of Live2D's eight samples and the only one
that ships expression files.

**Live2D's Cubism runtime cannot be installed here at all.** It comes from the
SDK for Web after you accept Live2D's EULA, and Live2D keeps it out of the
public repo on purpose. Accepting their licence is the user's to do.

---

## 5. Bugs found by testing rather than reading

Recorded because each would have shipped looking fine.

| bug | why it mattered |
|---|---|
| `_split_emotion_tag` called as a bare name | `NameError` discarded **every** fallback reply. A substring assertion matched both the broken and fixed forms, so the test could not tell them apart. |
| `sd.RawInputStream` where `InputStream` was needed | `.tobytes()` on a raw cffi buffer; exceptions in a cffi callback are swallowed, so the mic loop ran forever doing nothing with no traceback. |
| `winsound.SND_SYNC` | does not exist in Python's `winsound`; every local voice "synthesised correctly and produced no audio". |
| Kokoro `q8f16` model | **segfaulted onnxruntime** (`0xC0000005`) — uncatchable by `try/except`, so selecting Kokoro crashed the app every time it spoke. Now load-tested in a child process. |
| `__pauseAnimation` did not exist | the 3D toggle called a hook the page never had; the render loop kept re-arming. |
| `SystemConnectivityPage` missing 3 methods | `_save_app_settings`, `_invalidate_app_settings_cache`, `_win` — in PyQt6 an unhandled exception in a signal slot calls `qFatal` and **aborts the process**. |
| Two-owner settings cache | served a stale snapshot after `provider_router` wrote the same file, so the next save wrote the bad value back. Cached value silently reverted on every save. |
| Settings cache returned bare defaults when no controller was attached | any card persisting during startup wrote the whole default set over stored choices. |
| Shared webengine sink flushed any held mood | a live2d mood could be pushed into the 3D page if you switched backends mid-hold. |
| `Join-Path` with three positional args | installs failed with a baffling "cannot find a positional parameter". **Passed the parser, failed only on a real run.** |
| `$a[1..($a.Count-1)]` on a one-element array | slices backwards; a bare `python` resolution invoked `"p"`. |

---

## 6. What is verified, and what is not

**Verified:**

- 110 unit tests pass; seven acceptance harnesses pass (`accept_391`,
  `accept_avatar` at 191 checks, `accept_instructions`, `accept_providers`,
  `accept_local_speech`, `accept_live_toggle`, `accept_wakeword`).
- A real Gemini Live handshake: 24 tools accepted, sessions opened **and
  closed** twice.
- Offline STT on real neural speech, word-for-word correct.
- OSC packets read off a live UDP socket.
- VRM and Live2D licences read out of the actual model files.
- `scripts/precommit_check.py` and `scripts/install_optional_deps.ps1` both
  executed end to end, not just parsed.

**Not verified — and I will not claim otherwise:**

- **No renderer has been seen drawing a pixel.** I cannot accept Live2D's EULA,
  and the WebEngine render needs a human screen.
- **No microphone round trip into a local model.** My last two mic claims were
  both wrong until they were run by hand.
- **`accept_391.py`'s p99 latency assertion is flaky** (~35 samples, so p99 is
  effectively the max). It measures 17–31 ms p50 against a 50 ms ceiling and
  fails under CPU contention. The threshold was deliberately left alone rather
  than loosened to make the run green.

---

## 7. Files added or changed

### New source
```
core/provider_router.py       core/tool_index.py       core/tool_bridge.py
core/mcp_manager.py           core/speech_stt.py       core/speech_tts.py
core/instruction_injector.py  core/avatar_runtime.py
assets/avatar_live2d/index.html    assets/avatar_vrm/index.html
```

### Installers and tooling
```
scripts/install_optional_deps.ps1   scripts/precommit_check.py
fetch_live2d_model.py   fetch_vrm_model.py   vendor_vrm_runtime.py
vrm_license_report.py   install_speech_deps.ps1   download_offline_voices.py
```

### Tests
```
tests/test_tool_index.py  tests/test_tool_bridge.py  tests/test_mcp_instructions.py
accept_391.py  accept_avatar.py  accept_instructions.py  accept_providers.py
accept_local_speech.py  accept_live_toggle.py  accept_wakeword.py
live_handshake_probe.py
```

### Modified
```
main.py    ui.py    llm_client.py    or_client.py    core/local_brain.py
core/dynamic_registry.py    core/prompt.txt    requirements.txt
assets/web_background/index.html
tests/test_daily_briefing.py    tests/test_screen_processor.py
config/app_settings.json    .gitignore
```

---

## 8. Before you commit

```powershell
py -3.11 scripts\precommit_check.py
```

It blocks on credentials in any committable file, on a vendored build committed
without its licence, and on a failing test suite. It also reports large files
and ignored-but-present files for review.

**Two credentials were found and scrubbed from `config/app_settings.json` during
this preparation.** That file is git-tracked and is *not* the file the app
reads — `config_manager` resolves to `%LOCALAPPDATA%\BrahmaAI\config`. That
mismatch is exactly why they sat unnoticed: it looks like the config, and it is
not. Both values are now `<REDACTED-INTO-USER-DATA-DIR>`; the live credentials
live only in the per-user file.

See `ONE-391-next-steps.md` for what to do next.