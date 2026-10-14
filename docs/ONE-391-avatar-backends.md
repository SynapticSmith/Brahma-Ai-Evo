# ONE-391 — Avatar backends

Four directions were chosen at once and the reply path needed one place to send
an emotion tag. `core/avatar_runtime.py` is that place; this document records the
contract and the state of each renderer.

## The contract

A backend is any object exposing three methods. Nothing else is required, and
core never imports a backend by name.

| method | meaning | must |
|---|---|---|
| `probe()` | usable right now? | not raise; return an **actionable** `detail` |
| `set_emotion(tag, intensity=…)` | route one tag | not raise; may return `False` to decline |
| `status()` | one line for diagnostics | not raise |

`probe()` returning a bare `False` is a design error: "no .vrm file set" tells
the user what to do, "unavailable" does not.

## Transports

The real axis is **where the pixels are made**, not which art style:

- `transport="webengine"` — drawn in the existing `QWebEngineView` background
- `transport="osc"` — driven externally over UDP (VMC, what Project Nova does)

This matters because forcing the OSC route through the WebEngine transport would
have been the wrong assumption for `osc_vmc` from the start.

## State

| id | kind | transport | status |
|---|---|---|---|
| `three_reactive` | `3d_reactive` | webengine | **implemented** |
| `live2d` | `live2d` | webengine | **implemented**; model installed, runtime outstanding |
| `vrm` | `vrm` | webengine | **implemented and fully available** |
| `osc_vmc` | `osc_vmc` | osc | **implemented**; needs a host running to see it |

Selection is stored **separately** from availability. A chosen-but-missing
backend stays chosen so the card can say what is missing rather than silently
falling back to something else.

---

## `three_reactive` (implemented)

No new asset: the app already renders a reactive WebGL background and already
feeds it a live audio level. An emotion is a **mood applied on top of that
look**, not a second renderer.

### Composition, and the rule that makes it safe

`setBrahmaState` (LISTENING / THINKING / …) and `setAvatarEmotion` are two
independent inputs to one visual result. The emotion therefore never writes the
state palette — `currentColor*` stays the pure state look, and the emotion is
blended into separate `renderColor*` immediately before materials are updated:

```js
renderColorHot.copy(currentColorHot).lerp(avatarHot, mw * 0.85);
```

If the emotion wrote `targetColorHot` directly, it would fight the state
controller and whichever ran last would win. `accept_avatar.py` asserts there is
exactly one writer of `targetColorHot` so that cannot regress.

### Every factor is a multiplier around 1.0

`energy`, `wobble`, `bloom`, `audio` all sit at `1.00` for `NEUTRAL`. So:

- intensity `0.0` → `moodWeight` 0 → an exact pass-through of the pre-avatar look
- avatar switched off → the background is pixel-identical to before

Both are asserted, because "turning it off changes nothing" is the property that
makes the feature safe to ship.

### Audio-reactive intensity

The emotion scales how hard the voice drives the visuals; it never replaces the
level:

```js
const reactiveAudio = Math.min(1.0, currentAudioLevel * avatarAudioMult);
```

`SORROW` damps it (0.70), `ANGER` and `SURPRISE` amplify it (1.60 / 1.80). This
is the "audio-reactive intensity" half of the requested feature.

### Mood decay

`EMOTION_HOLD_SECONDS = 7.0`, then a slow fade back to `NEUTRAL`. Asymmetric
blend (0.18 in, 0.05 out) so a mood arrives fast and leaves slowly. The slot is
**last mood wins, not a queue** — an emotion is a mood, not an event, so a
renderer that is never attached cannot grow memory without bound.

### Delivery channel

`core` cannot import a widget, so the UI offers a sink and the core fills it:

```
core.avatar_runtime.set_webengine_sink(fn)   # MainWindow, weakref to itself
fn(tag, intensity) -> bool
```

If the page is not loaded yet — or the sink raises, or declines — the mood is
**held** and flushed on `loadFinished`. Without that, the first reply's emotion
would be dropped on every launch.

`delivered: True` in the audit record means the tag reached the renderer
*channel*, not that pixels moved; `last_reason` carries the caveat
(`"renderer raised: …; held for the renderer"`). A backend that returns `False`
explicitly is reported as `delivered: False`.

The tag is interpolated with `json.dumps`, not string concatenation — it is
model-produced text going into a JS source string.

### Cost

Two `THREE.Color` lerps per frame, inside the loop that already exists. No new
shaders, no new meshes, no new GL context.

---

## `live2d` (implemented)

### Why a separate page, not a mode flag

`assets/web_background/index.html` already owns a WebGL context, a three.js
scene and a render loop. A Cubism model needs its own canvas, core and update
cycle. Running both in one document means two GL contexts contending for the
same element plus a pile of "are we in 3D or 2D" branches in shared code.

Instead, both documents expose the **same seven hooks**:

```
setBrahmaState   setAudioLevel   setPointerNorm   setAvatarEmotion
__pauseAnimation __resumeAnimation __getAvatarState
```

So switching renderer is a **URL swap** (`active_page_path()`), and nothing
downstream changes when the page does. The UI never asks "which backend am I
rendering" — it asks for a path. There is a check asserting both pages expose
all seven.

### The two halves fail independently

This is why the probe is staged:

| half | source | can it be automated |
|---|---|---|
| model (`*.model3.json` + `.moc3` + textures) | ordinary files | yes — `fetch_live2d_model.py` |
| runtime (`live2dcubismcore.min.js`, `framework.js`) | Cubism SDK for Web | **no** — Live2D's EULA |

The runtime files are deliberately *not* in the public sample repo (only
`Core/CHANGELOG.md`, `Core/LICENSE.md`, `Core/RedistributableFiles.txt` are), so
there is nothing to mirror. Accepting Live2D's EULA is the user's to do.

The probe therefore names both halves at once:

```
Ren: 893 KB moc, 5 expressions, 73 parameters - but the Cubism runtime is
missing (live2dcubismcore.min.js, framework.js); copy both files into
assets/avatar_live2d/vendor/ from live2d.com
```

### Model scanning without the runtime

A `.model3.json` is plain JSON, so `scan_live2d_model()` inspects expressions,
parameter count, motion groups and the `.moc3` size with no SDK present. That is
only useful *because* the runtime may be absent — a probe that could answer
"installed or not" would say nothing on a machine where the model is present and
the runtime is not, which is precisely the state this machine is in.

It also refuses a `model3.json` that does not reference a `.moc3`, which is a
real failure mode: the file looks like a model and is not one.

### Emotion mapping: expressions first, parameters for the rest

Ren ships five expressions, and their contents decide the mapping rather than
their filenames:

| emotion | mechanism | why |
|---|---|---|
| JOY, SMILE | `exp_02` | eyes closed, smiling eyes, upturned mouth |
| FUN | `exp_03` | narrowed eyes, no smile |
| SORROW | `exp_04` | brows down, mouth form down |
| SURPRISE | `exp_05` | brows raised |
| ANGER, THINKING | parameters | no honest expression exists |
| NEUTRAL | neither | the reset |

Expressions are additive (`Blend: "Add"`) and touch a subset of parameters, so
an emotion with no matching expression degrades to parameter nudges rather than
breaking. Parameters resolve through the framework's own `Id` table and return
`null` when the model does not own them, so one emotion table cannot break
another model.

Lip sync rides the existing audio feed into `ParamMouthOpenY` — Ren declares a
`LipSync` group containing it, which the harness asserts.

---

## `osc_vmc` (implemented)

Project Nova's exact route: `/VMC/Ext/*` over UDP OSC to VSeeFace on
`127.0.0.1:39539`. `python-osc` is installed.

### Two things this transport must think about that a page does not

**UDP never fails.** Sending to `127.0.0.1:39539` with nothing listening
succeeds silently, forever. So `available` here means **armed**, never
connected, and the probe says so in words: *"armed at 127.0.0.1:39539 — 0 sent,
no reply yet (is VSeeFace running?)"*. The Settings card carries a **Test**
button that reports the truth it actually has: *"sent… UDP gives no receipt, so
this proves it did not throw, not that VSeeFace heard it."*

This is off by default. A feature that claims to be driving another application
and is actually talking to nobody is worse than one that is plainly off.

**It is not a page.** The other two renderers are documents inside the
QWebEngineView. This one owns a **sender thread** that flushes on change and
keepalives once a second.

### Why the keepalive matters

A VMC host that stops hearing `/VMC/Ext/T` returns the avatar to rest. Silence
is not neutral, so a constant zero timestamp would look like a stalled stream
and get the mood dropped. The stamp is a monotonic `[frame, seconds]` pair,
32-bit wrapped because that is the wire format.

### Blend shapes are positional, so the order is part of the protocol

`/VMC/Ext/Blend/Val` is a flat float array — index *n* means *n*-th shape in the
host's order. `BLEND_ORDER` is therefore a wire format, not a preference, and a
test asserts it is fixed and unique.

```
Joy  Angry  Blink  Blink_L  Blink_R  BrowDown  BrowUp  A  S  M  F
```

Those are the shapes MMD/VRoid-derived hosts universally accept. Emotions are
mapped onto them, and the harness checks each one is *distinguishable* — eight
distinct poses — and that e.g. `SORROW` does not also raise `Angry`, because a
mapping where every emotion looks like every other emotion passes every
structural test and does nothing.

`NEUTRAL` is the rest pose; intensity 0 collapses to it exactly.

### Verified on the wire, not mocked

The harness binds a real UDP socket, points the backend at it, and reads the
packets:

```
/VMC/Ext/Blend/Val   /VMC/Ext/Blend/Apply   /VMC/Ext/T  x4
```

Six packets over 2.5 s: one mood, one apply, four keepalives. That is the
sender thread genuinely running.

---

## `vrm` (implemented, and fully available)

**The first backend that actually has everything it needs on a fresh machine.**

### Licensing: verified from the binary, not a README

This is the sharp contrast with Live2D. VRM carries its own permission metadata
*inside the file* — `licenseUrl`, or VRoid Hub's `otherLicenseUrl` flags, plus
`authors`, `copyrightInformation`, `commercialUsage`, `modification`. So "check
the licence" can mean asking the file rather than trusting a README, which is
what `vrm_license_report.py` and `fetch_vrm_model.py` do.

Checked on three candidates:

| model | verdict |
|---|---|
| `VRM1_Constraint_Twist_Sample.vrm` (three-vrm, 10.7 MB) | **refused** — authors pixiv Inc. © 2022, `commercialUsage = corporation` |
| `Avatar_Orion.vrm` (6.1 MB) | **refused** — declares no licence at all |
| `cryptovoxels.vrm` (238 KB) | **installed** — `redistribution=allow, modification=allow, corporate_commercial_use=allow, personal_commercial_use=profit, credit=unnecessary` |

`fetch_vrm_model.py` **refuses** anything it cannot read as permissive. That is
the gate that matters: a model hosted in an MIT repository is not thereby MIT,
and the `three-vrm` sample is exactly that trap — MIT repo, non-permissive
avatar.

The model is also the smallest by a factor of forty, which matters on the CPU-
constrained machine this runs on.

### The runtime is MIT, so it is vendored

Everything — `three@0.186.1`, `@pixiv/three-vrm@3.5.5` and its four
sub-packages — is MIT and mirrorable, which is exactly why this backend can
reach a working state where `live2d` cannot.

`vendor_vrm_runtime.py` copies the ESM builds into `assets/avatar_vrm/vendor/`
(2.4 MB) with each package's LICENSE beside it and an import map to resolve the
bare specifiers. **No bundler, no build step, and no network at render time** —
a local desktop app should not have a renderer that silently shows nothing on a
bad connection. A check asserts the page contains no CDN URL.

### Staged probe, same shape as Live2D

```
cryptovoxels.vrm (232 KB, VRM 0.x, licence: permissive (VRoid Hub flags))
```

The scanner reads the GLB JSON chunk directly (eleven lines, no dependency) and
reports the declared licence in the probe, so the licence travels into the
Settings card rather than living only in a file on disk.

It also refuses the obvious failure modes: a file with a `.vrm` extension that
is not a GLB, and a truncated one. Both are covered by tests.

### Emotions

Standard VRM expressions (`happy`, `sad`, `angry`, `relaxed`, `surprised`) plus
`aa` for lip sync from the existing audio feed, and an idle blink so the model
is not a statue between replies. The page reads which expressions the *loaded
model* actually has and intersects the table with reality, so a model lacking
`surprised` skips it instead of throwing.

---

## Still to do

- **Cubism runtime** — `live2dcubismcore.min.js` and `framework.js` from
  https://www.live2d.com/en/sdk/download/web/ into `assets/avatar_live2d/vendor/`.
- **VSeeFace** — a VMC host, for `osc_vmc` to have anything to drive.

Both are the user's to supply and neither is code. **All four renderers are
implemented**; three of the four have everything they need to render on this
machine the moment they are switched on, and `live2d` is the only one waiting
on a licence click.

CUDA is not required by any of these: Project Nova's `check_cuda.py` gates
optional ASR/TTS acceleration, not the avatar path.

## Model licensing — checked, not assumed

Asked for a CC0/CC-BY model: **none exists.** Every Live2D model in circulation
is either Live2D's own sample data under the *Free Material License*, or a
third-party VTuber's model its creator has restricted.

The Free Material License is the closest thing to permissive that exists.
Live2D's own wording (https://www.live2d.com/en/learn/sample/simple-model) says
a General User or Small-Scale Enterprise (annual sales under 10 million JPY) may
use the sample materials for creative activities, commercial and non-commercial
alike. That is a permissive grant; it is simply not an open-source licence.

So `fetch_live2d_model.py` installs the licence text next to the model rather
than dropping files bare, and records `PROVENANCE.json` with source repo, branch
and a SHA-256 per file.

**Ren** was chosen over Haru, Hiyori, Mao, Mark, Natori, Rice and Wanko: it is
the smallest (2.4 MB) and the only one that ships expression files, which is
what emotion-driven Live2D actually needs.

## Tests

`accept_avatar.py` — 191 checks.

- Sections A–J: the seam contract. Registry opens without editing core,
  actionable probe reasons, tag normalisation and refusal, a raising backend
  cannot take a reply down, selection survives missing assets.
- Section K: `three_reactive` renders. Channel delivery, buffering, a raising
  renderer, a declining renderer, profile-table invariants parsed out of the
  page, offscreen build of the Settings card.
- Section L: `live2d`, against the **real installed model** where present.
- Section M: `osc_vmc` against a **real UDP socket** — the emitted packets are
  read off the wire, not asserted against a mock.
- Section N: `vrm` against the **real installed model**, plus licence
  provenance, the vendored runtime, and the shared hook contract on all three
  pages.