# ONE-391 — Next steps and known improvements

Ordered by what unblocks the most, not by what is most interesting. Items 1–3
are the only things standing between this work and being usable; everything
below item 3 is optional.

---

## 1. Unblock what already works (yours, ~10 minutes)

### 1.1 Commit — with the guard, not without it

Nothing is committed yet. HEAD is still upstream `e0bd3c5`.

```powershell
py -3.11 scripts\precommit_check.py
```

Then, if you want the history readable rather than one 45-file commit:

```powershell
git add .gitignore config/app_settings.json
git commit -m "chore: redact credentials from the tracked settings template"

git add core/provider_router.py core/tool_index.py core/tool_bridge.py core/mcp_manager.py
git commit -m "feat: vendor-agnostic provider chain, tool index, dynamic MCP loading"

git add core/speech_stt.py core/speech_tts.py core/instruction_injector.py core/avatar_runtime.py
git commit -m "feat: pluggable speech, per-layer instruction injector, avatar seam"

git add assets/ scripts/ fetch_live2d_model.py fetch_vrm_model.py vendor_vrm_runtime.py vrm_license_report.py
git commit -m "feat: three avatar renderers, VRM runtime vendored, model installers"

git add main.py ui.py llm_client.py or_client.py
git commit -m "feat: wire providers, speech engines, avatar renderers into the UI"

git add accept_*.py tests/test_tool_index.py tests/test_tool_bridge.py tests/test_mcp_instructions.py
git commit -m "test: acceptance harnesses for tool index, providers, speech, avatar"
```

Worth fixing first, or folding in: `main.py.bak`, `ui.py.bak` and
`core/prompt - Copy.txt` are 900 KB of duplicated pre-patch source. They are
now in `.gitignore`; delete them rather than leaving them on disk untracked.

### 1.2 See the avatar work

Two things block it, both settings:

1. Your saved `enable_3d_background` is `False`, so `three_reactive` honestly
   reports *"3D background is switched off"*. Enable it in
   Settings → Providers & Resources.
2. Both avatar instruction blocks (`avatar_emotion`, `avatar_tone`) are off, so
   the model is not emitting tags yet. Enable at least one in
   Settings → Avatar.

Then **Settings → Avatar → pick VRM**. `vrm` is the one backend with every
dependency present, so it is the one to try first.

### 1.3 The two user-owned assets

| what | where |
|---|---|
| Live2D Cubism runtime (2 files) | https://www.live2d.com/en/sdk/download/web/ → `assets/avatar_live2d/vendor/` |
| VSeeFace (or any VMC host) | running on `127.0.0.1:39539` |

Neither is code. The Live2D download is behind their EULA, which is yours to
accept — that is the whole reason Live2D is the one backend that cannot be made
to work autonomously.

---

## 2. Fix the known-broken things

### 2.1 `accept_391.py` p99 latency assertion — flaky

It samples ~35 queries and takes p99, which is effectively the max. Real
measurements are p50 17–31 ms against a 50 ms ceiling, so it passes on an idle
machine and fails under contention. Three options, in order of preference:

- **Raise the sample count** so p99 means p99 (e.g. 200 queries). Costs runtime.
- **Assert on p95** rather than p99, and keep a separate looser p99 ceiling.
- **Leave it.** It has been failing intermittently for several sessions and has
  never once indicated a real regression.

Do **not** simply raise the number until it passes — that destroys the only
timing signal the suite has.

### 2.2 `config/app_settings.json` is a trap

It is git-tracked, it looks like the config, and it is **not** the file the app
reads (`config_manager` resolves to `%LOCALAPPDATA%\BrahmaAI\config`). That is
how two live credentials sat in it unnoticed across a long stretch of work.

Options:

- **Rename it** `config/app_settings.example.json` and have it be explicitly a
  template. Clearest.
- **Gitignore it** and keep a committed `.example.json`.
- **Leave it**, now that the credentials are redacted and the pre-commit check
  guards it.

The first is the right answer. It removes the ambiguity permanently rather than
relying on everyone remembering.

### 2.3 `_i18n` and 20+ other modules in `main.py`/`ui.py` are untouched

`ui.py` is ~17,000 lines and `main.py` ~6,000. The changes here are surgical, but
a 45-file diff against 23,000 lines of UI is hard to review as a whole. Splitting
the settings cards into their own module would make future changes to this area
much cheaper.

---

## 3. Real improvements worth doing

### 3.1 The tool index needs a cold-start benchmark

Everything measured so far is warm — the semantic backend loads on a background
thread and folds in when ready. Nobody has measured a genuinely cold start
(process launch → first tool query). That is the number a user experiences, and
it is the one most likely to be bad on a CPU-only machine.

### 3.2 `fastembed` is commented out

The semantic retriever (`all-MiniLM-L6-v2` via ONNX, ~3 ms/query) is the one
that would meaningfully improve paraphrase routing — "dim the monitors" hitting
`system_diagnostics` despite no shared keywords. It stays commented out because
it downloads a model at install time. If you want it, the honest place to
express that is an explicit opt-in in the Settings card, not a line in
`requirements.txt`.

### 3.3 Provider routing is first-match, not capability-aware

`chain()` is a fixed order. There is no notion of "I need tools", "I need
vision" or "I need cheap" — a request that needs vision will happily try Gemini
first even when local could answer it free. A capability filter per provider
(vision / tools / streaming / cost) would let the chain skip legs that cannot
serve the request. This is probably the single highest-value structural
improvement left in the provider work.

### 3.4 Background memory extraction should have an idle queue

Right now `memory_extraction_enabled` is a straight on/off. The resource clash
that motivated it is real: on CPU, extraction running in parallel with a chat
turn halves both. An idle queue — buffer the conversation, extract after 2–3
minutes of no user input — gets you memory *and* responsiveness instead of
choosing between them.

### 3.5 The avatar has no lip-sync verification

`osc_vmc` and `live2d` both map the audio level onto a mouth parameter, and
`three_reactive` scales the whole visual response by it. None of it has been
checked against a real microphone. Given that two previous microphone claims
turned out to be wrong only once a human ran them, this is the most likely place
for a similar surprise.

---

## 4. Things deliberately not done

Recorded so they are decisions rather than oversights.

- **Not committed.** You asked twice; it is your call and it has not been made.
- **No renderer seen drawing.** Live2D's EULA is yours; the WebEngine render
  needs your screen.
- **No CUDA work.** Project Nova's `check_cuda.py` gates optional ASR/TTS
  acceleration, not the avatar path. None of the four renderers needs it.
- **Not a VRM 1.0 model.** `cryptovoxels` is VRM 0.x. `@pixiv/three-vrm` handles
  both, but if you want VRM 1.0 (better spring bones, humanoid expressions),
  point the setting at a 1.0 model — the page needs no change.
- **`fastembed` not added as a hard dependency.** Download-at-install is the
  user's decision.
- **The `apply_live_toggle_patch.py` script was not restored.** You deleted it
  twice and said to ignore it.

---

## 5. One-line summary of risk

The code is in good shape — 110 unit tests, seven acceptance harnesses, real
protocol verification where it was possible. The risk is concentrated in the
three places no automated check can reach: **the microphone path, the WebEngine
render, and the Gemini Live audio session.** Each needs one human run before you
trust it, and each has surprised before.