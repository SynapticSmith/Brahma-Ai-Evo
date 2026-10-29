# Brahma Evo - Version 1.0.1 Change Log

Engineering change log for the release that follows **1.0.0**. It covers every
commit landed on `main` after the last upstream baseline commit, grouped by
theme, with the verification evidence that backs each group.

- **Baseline (before this work):** `e0bd3c5` - last commit by GodxSuryaansh.
- **Range documented:** `e0bd3c5..HEAD`.
- **Commits in range:** 122 (all authored by SynapticSmith; the original 103 was
  measured at `67b18b4`, the last commit of the section-10 inventory; 115 were on
  `main` before section 14 was written - the four in-between commits are listed
  in section 14.6 - and the last seven carry sections 14, 15, 16, 17, 18, 19
  and 20).
- **HEAD at last update:** the 2026-10-20 commit carrying section 20 (previous
  markers: the 2026-10-18 commit for section 19, `b2b399c` for section 18,
  `3dc096a` for section 17, `e653c1f` for section 16, `4900107` for section 15,
  `b820292` for section 14, `664ae4b` for section 13.5).
- **Diff footprint:** recomputed at the commit carrying section 20; see that
  commit's `git show --stat`. The historical markers stand: 257 files /
  151,721 / 18,022 for section 19, 257 / 151,527 / 18,022 for section 18, and
  the original 231 / 141,610 / 17,371 before the close-out commits.
- **Product version in tree:** `1.0.0` (see section 12 for the `1.0.1` bump).
- **Source material:** this repository's `git` history plus
  `docs/CODE-REVIEW-TODO.md`, `docs/ONE-391-changes.md`,
  `docs/ONE-391-tool-index.md`, `docs/ONE-391-avatar-backends.md`, and
  `docs/ONE-391-next-steps.md`.

> Note: the `docs/ONE-391-*.md` files were written mid-flight and still say
> "Nothing is committed yet / HEAD is still `e0bd3c5`". That is stale. All work
> described here is committed; `docs/CODE-REVIEW-TODO.md` records the landed
> state.

---

## 1. Summary

1.0.1 turns a single-engine voice assistant into a provider-agnostic platform
and pays down the structural debt that blocked a clean release. Six themes:

1. **Vendor-agnostic core (ONE-391).** A provider chain
   (`gemini > openrouter > local > custom`) with per-provider kill switches; a
   tool/skill index that keeps the on-wire tool count under the Gemini Live cap
   of 128 (173 tools advertised, only ~24 sent); pluggable speech engines; and
   four interchangeable avatar renderers behind one contract.
2. **Review hardening.** The P0-P4 items in `docs/CODE-REVIEW-TODO.md` are
   complete: concurrency/lifecycle races, MCP retry and collision handling,
   precise failover, bounded caches, credential redaction, dependency pinning,
   and CI.
3. **Decomposition of god objects (P4-19).** `ui.py` and `BrahmaLive` were
   reduced to facades over 47 `core/ui_*.py` modules and named flow seams.
4. **A real gate.** Unit tests, a curated ruff lint gate, seven acceptance
   harnesses, and a Windows CI workflow now run before a commit.
5. **Provider-egress enforcement (2026-10-18, section 14).** The kill switches
   were made real: a failover reply ladder that surfaces provider errors
   instead of a canned reply, a settings-driven local model, and
   `core/llm_gateway.py` as the single egress for auxiliary LLM calls, so
   every toggle - offline mode included - governs all traffic.
6. **Agent-brief system prompt (2026-10-18, section 16).** `core/prompt.txt`
   was rewritten from a single-engine persona note into the operating brief
   for the platform: the multi-step turn loop, the full capability
   inventory, deferred-tool discovery, and the memory/safety/offline
   doctrine - with an explicit local-model context budget so the offline
   path is not silently truncated.

---

## 2. Version identifiers

Two files carry the product version; both currently read `1.0.0`:

| File | Consumed by | Note |
| --- | --- | --- |
| `version.txt` | `installer/BrahmaEvo.spec` (lines 25, 82) | `FileDescription` = "Brahma Evo AI Assistant", `InternalName` = `BrahmaEvo`, `OriginalFilename` = `BrahmaEvo.exe` |
| `version_setup.txt` | `installer/BrahmaEvo_Setup.spec` (line 50) | `FileDescription` = "Brahma Evo Installer", `InternalName` = `BrahmaEvoSetup`, `OriginalFilename` = `BrahmaEvo_Setup.exe` |

Both currently carry `filevers=(1,0,0,0)` / `prodvers=(1,0,0,0)` and
`FileVersion` / `ProductVersion` = `1.0.0`. This document describes the work
that constitutes **1.0.1**; the version files are bumped in section 12.

---

## 3. Delivery: provider-agnostic core (ONE-391)

### 3.1 Provider chain with kill switches

`e8c6173` introduces a vendor-agnostic provider chain. The active order is
`gemini > openrouter > local > custom`, each gated by its own kill switch so an
operator can force a tier off without editing call sites. Failover semantics
were tightened later: `ec4df3a` makes OpenRouter fallback happen **only** on a
real Gemini throttle rather than on any error, and `b11fed9` lifts the failover
timing out of `BrahmaLive` into a dedicated seam.

### 3.2 Tool and skill index

`923c0d0` adds a tool/skill index plus dynamic MCP loading and optional
instruction layers. The index exists because Gemini Live caps tools on the wire
at 128 and does not allow swapping them mid-session; the index keeps the public
surface at 173 tools while only ~24 are ever sent. `docs/ONE-391-tool-index.md`
documents the mapping. Later commits harden it: `851e3bd` adds per-tool access
controls and MCP config seeding, and `e0fa26f` refuses MCP tool-name collisions
instead of silently overwriting.

### 3.3 Pluggable speech engines

`9e0cbf7` introduces pluggable speech engines alongside the four avatar
renderers. `eaa1dbc` later adds **Gemini as an opt-in TTS engine**. Robustness
follow-ups: `2302b69` prevents offline speech sink re-entry, `97e3e00` keeps
the local voice loop alive across a disconnect or a missing key, and `257ce23`
bounds the TTS probe cache so it cannot grow without limit.

### 3.4 Avatar runtime: four interchangeable backends

The avatar layer exposes four backends behind one three-method contract
(`probe`, `set_emotion`, `status`) with two transports (`webengine`, `osc`).
Per `docs/ONE-391-avatar-backends.md`:

| Backend id | Runtime | Transport |
| --- | --- | --- |
| `three_reactive` | Three.js reactive rig | `webengine` |
| `live2d` | Cubism | `webengine` |
| `vrm` | VRM / glTF humanoid | `webengine` |
| `osc_vmc` | OSC / VMC motion-capture bridge | `osc` |

Landing commits: `9e0cbf7` (backends), `c73e4d1` (scriptable Cubism runtime +
portable installers), `ba57f01` (fix Live2D and VRM loading), `67507f3`
(expand emotion support), `d2b07aa` (fix avatar threading and VRM license
handling).

### 3.5 UI wiring and acceptance harnesses

`f758995` wires providers, speech, instructions, and avatar selection into the
UI. `936f301` adds the acceptance harnesses (`accept_391`, `accept_avatar`,
`accept_instructions`, `accept_providers`, `accept_local_speech`,
`accept_live_toggle`, `accept_wakeword`), the installers, and documentation.

### 3.6 Live/robustness and credential hygiene

- `2626164` - improve live reconnects and MCP config support.
- `8b54677` - make settings writes atomic.
- `6dcffd9` - redact credentials from the tracked settings template (see
  section 9, Known limitations, and section 4).

---

## 4. Review hardening (`docs/CODE-REVIEW-TODO.md`)

`bcdedac` opens the tracker and work log. Landed fixes by severity.

### 4.1 P0 - release blockers

- `6dcffd9` redacts credentials from the tracked settings template.
- `eaf8529` moves Qt and boot-sentry startup work out of import side effects.

### 4.2 P1 - concurrency and lifecycle

- `7cefdb7` fixes the reconnect wakeup race, wake-word re-arm, and background
  task lifetime (tasks could outlive the loop that owned them).
- `b871624` makes MCP status locking consistent and stops retrying servers that
  cannot come back.
- `2703e75` / `a6796ed` log the P1 lifecycle and MCP concurrency fixes.

### 4.3 P2 - failover, collisions, cache

- `ec4df3a` falls back to OpenRouter only on a real Gemini throttle.
- `e0fa26f` refuses MCP tool-name collisions and logs cleanup failures.
- `257ce23` bounds the TTS probe cache and logs skipped avatar assets.
- Summarized in `1a11871`.

### 4.4 Test hygiene and documentation truth

- `bf465d4` keeps the demo runners out of pytest collection.
- `125b20a` corrects a stale credential note and states the user-data path policy.
- `58c10ee` keeps the acceptance-gate contract in a single place.

### 4.5 Dependency, repository, and import hygiene

- `40afb3f` pins every dependency to the version the stack was verified against.
- `2734729` adds a Windows CI workflow for the static gate and unit suite.
- `ef33a02` stops tracking a 7.5 MB gesture model the app never reads.
- `a5dcd72` keeps a single committed copy of `three.min.js`.
- `eaf8529` moves Qt and boot-sentry startup work out of import side effects
  (importing a module no longer starts threads or touches Qt).

---

## 5. Decomposition: god-object splits (P4-19)

All P4-19 items are complete. `ui.py` went from a single god-object to 47
`core/ui_*.py` modules plus mixins, and `BrahmaLive` was reduced to a thin
facade over named seams.

### 5.1 BrahmaLive seams

| Commit | Extracted to |
| --- | --- |
| `b11fed9` | `core/provider_failover.py` (Gemini->OpenRouter timing) |
| `b927a44` | `core/tool_exec.py` (tool-call execution) |
| `1ab118f` | `core/audio_io.py` (mic/receive/play/speak loop) |
| `f414986` | `core/reply_flow.py` (reply/IG/email flows) |
| `b311d08` | `core/session_lifecycle.py` (Live session lifecycle) |
| `2f57113` | `core/attention_flow.py` (attention/meeting seam) |
| `d883e39` | `core/device_flow.py` (device routing seam) |
| `ae7f6d0` | `core/realtime_flow.py` (realtime/dashboard seam) |

`BrahmaLive` measured at HEAD: 72 methods, 1,606 method-lines, class span lines
2,149-3,848 (1,700 class lines) - the residual facade.

### 5.2 ui.py widget extraction (15 commits)

`058fcb4`, `994fa06`, `1d8029d`, `ecdf5bd`, `55e443b`, `de6d6d3`, `57c8ea9`,
`f18f93a`, `b1b742b`, `45103ee`, `65d66e9`, `1c6d377`, `0662a1e`, `16cf8a7`,
`b97bf00` moved chat/workspace, connectivity/settings, devices, telemetry +
theme palette, gesture preview, floating launcher, remote overlays, first-run
setup, transients, command bar, file-drop, sys-metrics, branding, widgets, and
startup helpers into `core/ui_*.py`.

### 5.3 MainWindow mixin split (16 mixins)

`465c190`, `84c413f`, `9e00ddf`, `b46d32e`, `a9c27d6`, `e6e75c5`, `031c119`,
`e2a9471`, `cfa4795`, `d91a87e`, `44d97d1`, `c399270`, `dfa1e21`, `3fe9478`,
`7eede11`, `6d77cc9` split `MainWindow` into `_DiscordMixin`,
`_AppSettingsMixin`, `_StartupOSMixin`, `_AvatarAudioMixin`,
`_OverlayCardsMixin`, `_MuteSendMixin`, `_MeetingMixin`, `_NavMixin`,
`_RemoteMixin`, `_SecondaryOverlaysMixin`, `_StateAppliersMixin`,
`_SetupFlowMixin`, `_PanelsMixin`, `_WiringMixin`, `_RuntimeMixin`,
`_ApplyStateMixin`.

### 5.4 BrahmaUI mixin split (12 mixins)

`6e582d8`, `4f34b21`, `80ae853`, `fb1d116`, `443ee84`, `f8367fb`, `118d28b`,
`b65f343`, `6791501`, `f929a99`, `a7da3ee`, `e95c9f2` split `BrahmaUI` into
`_ShellSettingsMixin`, `_ShellDiscordMixin`, `_ShellBootMixin`,
`_ShellTrayMixin`, `_ShellDevModeMixin`, `_ShellSidebarMixin`,
`_ShellLauncherMixin`, `_ShellControlPanelMixin`,
`_ShellPanelSettingsMixin`, `_ShellActionsMixin`, `_ShellDelegatesMixin`,
`_ShellWindowMixin`.

Closures: `41de452` marks the `ui.py` split complete; `2be3ab3` records the
`BrahmaLive` seam split complete; `f36b811` closes P4-19.

---

## 6. Runtime robustness fixes

| Item | Commit | Fix |
| --- | --- | --- |
| 672 | `eaa1dbc` | Add Gemini as an opt-in TTS engine |
| 674 | `7a1dac7` | QPushButton `Could not parse stylesheet` |
| 675 | `c8a19ef` | Cross-thread QWidget access via a GUI-thread marshaller |
| 675 | `8a530be` | Startup crash + DPI-awareness warning |
| P3-19 | `2d631a0` / `65d74f4` | Auto-heal network test no longer calls live Gemini (restored `_call_tool` mock) |
| P3-20 | `fe66038` | Stop initializing QtWebEngine at import time (teardown crash); crash pair 4/10 -> 0/10 |
| - | `f826338` | Make the Python-version requirement consistent (fallback chain) |

---

## 7. Tooling, tests, and CI

- **Lint gate.** `67b18b4` adds a ruff-based lint gate over a curated blocking
  ruleset (`F401`, `F841`, `F811`, `F541`, `F821`) configured in
  `pyproject.toml` (`target-version = "py311"`, `line-length = 120`) with
  `requirements-dev.txt` pinning `ruff==0.17.1`. Seven real F821 bugs and two
  F841s were fixed to reach "All checks passed!".
- **CI.** `2734729` adds `.github/workflows/ci.yml` with a Windows job for the
  static gate and unit suite; `67b18b4` adds the `lint` job. Jobs:
  `static-gate`, `lint`, `tests`.
- **Acceptance harnesses.** `936f301` + `58c10ee` - seven harnesses
  (`accept_391`, `accept_avatar`, `accept_instructions`, `accept_providers`,
  `accept_local_speech`, `accept_live_toggle`, `accept_wakeword`) all report
  `ALL CRITERIA PASS`, driven by `precommit_check.py`.

---

## 8. Verification status

Evidence captured at HEAD:

- `pytest` - **167 passed**.
- `precommit_check.py` - **SAFE TO COMMIT**, all 7 harnesses `ALL CRITERIA PASS`.
- `ruff check .` - **All checks passed!**
- `docs/CODE-REVIEW-TODO.md` - 834 lines, **all items `[x]`**.

**Carry-forward at `664ae4b` (2026-10-17).** Everything committed after the
`7c44152` verification point is **documentation-only** (`git diff --stat
7c44152..664ae4b` touches exactly two files: this log and
`docs/VERSION-1.0.1-CHANGES.md`, +912 lines, zero source). No source, test, or
CI file changed, so the evidence above carries forward to HEAD unchanged.

**Superseded figures.** The verification record past `b820292` lives in
sections 14.5 and 15.6 (221, then 240 tests); the numbers above remain the
record at the `7c44152` verification point.

One cosmetic drift was found while verifying: the tracker states `ui.py` is 830
lines; the file is actually **809** lines at HEAD (an earlier entry says 816).
Functionally irrelevant; safe to correct in place.

---

## 9. Known limitations carried into 1.0.1

Drawn from the `ONE-391-*` docs and review tracker:

- Gemini Live caps tools on the wire at 128 with no mid-session swap; the tool
  index is the mitigation, not a removal of the limit. Deferred/discovered tools
  MUST be called via `call_tool` (never directly) to avoid `1008 Requested entity
  was not found`; the system enforces this via stronger prompting and architectural
  proxy routing.
- `osc_vmc` is the least-exercised avatar backend; its transport is only
  verified where an OSC/VMC source is present.
- Avatar asset loading logs (rather than fails loudly) for skipped assets
  (`257ce23`) - missing assets degrade rather than abort.
- The `core/ui_*.py` split keeps per-file F821 ignores for the legacy dynamic
  `globals().update(deps)` pattern (for example `core/session_lifecycle.py:33`),
  because those names are injected at runtime.

---

## 10. Commit inventory (103 commits, `e0bd3c5..HEAD`)

### 10.1 ONE-391 feature delivery - 2026-10-14 (8)

```
6dcffd9 chore: redact credentials from the tracked settings template
e8c6173 feat: vendor-agnostic provider chain with per-provider kill switches
923c0d0 feat: tool and skill index, dynamic MCP loading, optional instruction layers
9e0cbf7 feat: pluggable speech engines and four interchangeable avatar renderers
f758995 feat: wire providers, speech, instructions and avatar into the UI
936f301 test: acceptance harnesses, installers and documentation
c73e4d1 Live2D: scriptable Cubism runtime + portable installers
ba57f01 Fix Live2D and VRM avatar loading
```

### 10.2 Early hardening and review setup - 2026-10-15 (8)

```
67507f3 Expand avatar emotion support
d2b07aa Fix avatar threading and VRM license handling
851e3bd Add per-tool access controls and MCP config seeding
2302b69 Prevent offline speech sink re-entry
2626164 Improve live reconnects and MCP config support
8b54677 Make settings writes atomic
97e3e00 Keep the local voice loop alive on disconnect and missing key
bcdedac Add code-review TODO and work log
```

### 10.3 P1/P2 review fixes - 2026-10-15 (10)

```
7cefdb7 Fix reconnect wakeup race, wake-word re-arm, and background task lifetime
2703e75 Log the P1 concurrency and lifecycle fixes
b871624 Make MCP status locking consistent and stop futile server retries
a6796ed Log the MCP concurrency and retry fixes
ec4df3a Only fail over to OpenRouter for a real Gemini throttle
e0fa26f Refuse MCP tool-name collisions and log cleanup failures
257ce23 Bound the TTS probe cache and log skipped avatar assets
1a11871 Log the P2 failover, collision, and cache fixes
bf465d4 Keep the demo runners out of pytest collection
125b20a Correct a stale credential note and state the user-data path policy
```

### 10.4 CI, pinning, repo weight, import safety - 2026-10-15 (6)

```
58c10ee Keep the acceptance-gate contract in one place
40afb3f Pin every dependency to the version the stack was verified against
2734729 Add a Windows CI workflow for the static gate and the unit suite
ef33a02 Stop tracking the 7.5 MB gesture model the app never reads from the repo
a5dcd72 Keep one committed copy of three.min.js instead of three
eaf8529 Move Qt and boot-sentry startup work out of import side effects
```

### 10.5 BrahmaLive seams - 2026-10-15 (5)

```
b11fed9 Extract Gemini-to-OpenRouter failover timing from BrahmaLive
b927a44 Move tool-call execution out of BrahmaLive into core.tool_exec
1ab118f Move mic/receive/play/speak audio loop out of BrahmaLive into core.audio_io
f414986 Move reply/IG/email flows out of BrahmaLive into core.reply_flow
b311d08 Move the Live session lifecycle out of BrahmaLive into core.session_lifecycle
```

### 10.6 ui.py widget extraction - 2026-10-15 (15)

```
058fcb4 Move the chat/workspace widgets out of ui.py into core.ui_chat
994fa06 Move the connectivity/settings pane out of ui.py into core.ui_connectivity
1d8029d Move the smart-devices / connect-devices panels out of ui.py into core.ui_devices
ecdf5bd Move the launcher/telemetry wings out of ui.py into core.ui_telemetry (and the C palette into core.ui_theme)
55e443b Move the hand-gesture camera preview out of ui.py into core.ui_gesture
de6d6d3 Move the floating launcher and gesture card out of ui.py into core.ui_floating
57c8ea9 Move the remote-key/briefing/confirmation/memory overlays out of ui.py into core.ui_overlays
f18f93a Move the first-run SetupOverlay out of ui.py into core.ui_setup
b1b742b Move the transient overlays out of ui.py into core.ui_transients
45103ee Move CommandBar out of ui.py into core/ui_commandbar
65d66e9 Move the file-drop widget out of ui.py into core/ui_filedrop
1c6d377 Move _SysMetrics out of ui.py into core/ui_sysmetrics
0662a1e Move branding/icon helpers out of ui.py into core/ui_branding
16cf8a7 Move _active_net_label and MetricBar out of ui.py into core/ui_widgets
b97bf00 Move startup/default-settings helpers out of ui.py into core/ui_startup
```

### 10.7 MainWindow / BrahmaUI mixin split (P4-19) - 2026-10-15..16 (31)

```
67170b5 P4-19: extract BackgroundWidget into core/ui_bg.py
a4d71fa P4-19: extract DeveloperModeDialog into core/ui_devmode.py
465c190 P4-19: split MainWindow Discord methods into core/ui_discord.py mixin
84c413f P4-19: split MainWindow app-settings/startup methods into core/ui_appsettings.py
9e00ddf P4-19: split MainWindow startup/API-defaults methods into core/ui_startup_os.py
b46d32e P4-19: split MainWindow avatar/audio methods into core/ui_avatar_audio.py mixin
a9c27d6 P4-19: split MainWindow overlay-card methods into core/ui_overlays_cards.py mixin
e6e75c5 P4-19: split MainWindow mute/send methods into core/ui_mute_send.py mixin
031c119 P4-19: split MainWindow meeting/attention methods into core/ui_meeting.py mixin
e2a9471 P4-19: split MainWindow nav/sidebar methods into core/ui_nav.py mixin
cfa4795 P4-19: split MainWindow remote-connect methods into core/ui_remote.py mixin
d91a87e P4-19: split MainWindow secondary-overlays methods into core/ui_secondary_overlays.py mixin
44d97d1 P4-19: split MainWindow state-applier methods into core/ui_state_appliers.py mixin
c399270 P4-19: split MainWindow setup-flow methods into core/ui_setup_flow.py mixin
dfa1e21 P4-19: split MainWindow panel-builder methods into core/ui_panels.py mixin
3fe9478 P4-19: split MainWindow feature-wiring methods into core/ui_wiring.py mixin
7eede11 P4-19: split MainWindow runtime-tick methods into core/ui_runtime.py mixin
6d77cc9 P4-19: split MainWindow _apply_state into core/ui_apply_state.py mixin
6e582d8 P4-19: split BrahmaUI app-settings into core/ui_shell_settings.py mixin
4f34b21 P4-19: split BrahmaUI discord-settings into core/ui_shell_discord.py mixin
80ae853 P4-19: split BrahmaUI startup/boot into core/ui_shell_boot.py mixin
fb1d116 P4-19: split BrahmaUI tray into core/ui_shell_tray.py mixin
443ee84 P4-19: split BrahmaUI developer-mode into core/ui_shell_devmode.py mixin
f8367fb P4-19: split BrahmaUI sidebar/floating into core/ui_shell_sidebar.py mixin
118d28b P4-19: split BrahmaUI launcher/startup into core/ui_shell_launcher.py mixin
b65f343 P4-19: split BrahmaUI control-panel into core/ui_shell_controlpanel.py mixin
6791501 P4-19: split BrahmaUI panel-settings into core/ui_shell_panel_settings.py mixin
f929a99 P4-19: extract BrahmaUI command/discord handlers into core/ui_shell_actions.py
a7da3ee P4-19: extract BrahmaUI forwarding/state surface into core/ui_shell_delegates.py
e95c9f2 P4-19: extract BrahmaUI window/facade methods into core/ui_shell_window.py
41de452 Docs: mark ui.py god-object split complete in P4-19 log
```

### 10.8 BrahmaLive flow seams (completion) - 2026-10-16 (4)

```
2f57113 Refactor: extract BrahmaLive attention/meeting seam into core/attention_flow.py
d883e39 Refactor: extract BrahmaLive device-routing seam into core/device_flow.py
ae7f6d0 Refactor: extract BrahmaLive realtime/dashboard seam into core/realtime_flow.py
2be3ab3 Docs: record BrahmaLive seam split complete in P4-19 log
```

### 10.9 Final fixes, docs, merges, lint gate - 2026-10-16 (16)

```
7b7e1e1 Document local agent bridge design
2d631a0 Fix: stub executor.analyze_error/generate_fix in auto-heal test to avoid live Gemini call (blocks CI)
716637d Docs: record auto-heal network-test fix (P3-19) and defer flaky Qt teardown crash (P3-20)
65d74f4 Fix: restore _call_tool mock and executor construction dropped from auto-heal test
80c36da Merge branch 'main' of https://github.com/SynapticSmith/Brahma-Ai-Evo
1af3e77 Expand agent bridge design to orchestrators
d77485a Merge branch 'main' of https://github.com/SynapticSmith/Brahma-Ai-Evo
c8a19ef Fix cross-thread QWidget access via a GUI-thread marshaller (todo 675)
8855be0 Merge branch 'main' of https://github.com/SynapticSmith/Brahma-Ai-Evo
8a530be Fix: item-675 startup crash + DPI-awareness warning
f826338 Chore: make Python-version requirement consistent (fallback chain)
7a1dac7 Fix: item-674 QPushButton "Could not parse stylesheet"
eaa1dbc Feat: item-672 add Gemini as an opt-in TTS engine
fe66038 P3-20: stop initialising QtWebEngine at import time (teardown crash)
f36b811 docs: close P4-19 (god-object split complete) - all TODO items now done
67b18b4 P3-14: add ruff lint gate (curated blocking ruleset) + CI job
```

---

## 11. Source documents

- `docs/CODE-REVIEW-TODO.md` - prioritized review tracker and work log (canonical
  landed state).
- `docs/ONE-391-changes.md` - ONE-391 feature summary.
- `docs/ONE-391-tool-index.md` - tool/skill index and Gemini 128-tool cap notes.
- `docs/ONE-391-avatar-backends.md` - four-backend avatar contract.
- `docs/ONE-391-next-steps.md` - known limitations and deferrals.
- `docs/VERSION-1.0.1-CHANGES.md` - companion narrative change record for the
  release (added by `664ae4b`; see section 13.5).

Excluded by request: `docs/agent-orchestrator-bridge.md` (forward-looking design,
not shipped behavior).

---

## 12. Release checklist (1.0.0 -> 1.0.1)

1. Bump `version.txt`: `filevers`/`prodvers` to `(1,0,1,0)`, `FileVersion`/
   `ProductVersion` to `1.0.1`.
2. Bump `version_setup.txt`: same four fields to `1.0.1`.
3. Confirm `installer/BrahmaEvo.spec` still points at `version.txt` and
   `installer/BrahmaEvo_Setup.spec` at `version_setup.txt`.
4. Run the full gate: `pytest`, `precommit_check.py`, `ruff check .`.
5. Build both installers and smoke-test the wizard
   (`installer/install_wizard.py`).
6. Tag the release; do not push without explicit authorization.

---

## 13. Addendum (post-log): upstream issue #2 - PyAudio to `sounddevice`

Added after this log was written; covers the commits that landed after the
inventory in section 10.

### 13.1 The report

Upstream issue #2 (`titechprabhasolutions/Brahma-Ai-Evo`) reports
`pip install -r requirements.txt` dying while building `pyaudio==0.2.14` with
`fatal error C1083: portaudio.h: No such file or directory`. Root cause: the
reporter's interpreter is **Python 3.14** (`cpython-314` / `cp314`). PyAudio
0.2.14 publishes Windows wheels only for cp38-cp313, so 3.14 falls back to a
source build, and that build needs PortAudio headers that are not bundled on
Windows. `README.md` already requires Python 3.11 or 3.12, so the install was
simply outside the supported interpreter window.

### 13.2 The fix (fork)

Instead of chasing a C extension, the standby wake-word path now uses the same
`sounddevice` backend the live and local-voice paths already use, which removes
PyAudio from the dependency set entirely:

- `core/session_lifecycle.py` - `_wakeword_loop` rewritten around
  `sd.InputStream(..., dtype="int16", channels=1, callback=...)` feeding a
  bounded `queue.Queue`. The energy gate shared with the local-voice loop is now
  a single module constant (`_SPEECH_ENERGY_THRESHOLD = 300.0`, used by both);
  it seals a phrase on ~0.3 s of silence or a 4 s cap. The captured block is
  wrapped as `sr.AudioData(pcm, SEND_SAMPLE_RATE, 2)` so the downstream STT path
  is unchanged. Device resolution, re-arm, and prompt stop are preserved.
- `requirements.txt` - PyAudio removed from the dependency set.
- `accept_wakeword.py` - the real-mic harness now forces that gate open for its
  capture assertion, so the check depends on the capture/STT path and not on the
  room's noise floor.
- `bootstrap.ps1` - spliced in a Python gate: it requires **3.11 or 3.12** and
  auto-downloads 3.11.8 only when no Python exists at all, so the reporter's
  3.14 case fails fast with a clear message instead of a PortAudio build error.

### 13.3 Commits

| Commit | Subject |
| --- | --- |
| `d5fe4d5` | fix(voice): capture standby wake word via sounddevice (drop PyAudio); enforce Python 3.11/3.12 in bootstrap |
| `7c44152` | test(wakeword): make the real-mic harness deterministic via a shared speech-energy gate |

Post-log commits not otherwise listed in section 10, in order: `ebc38d5` (this
log), `3bab287` (correct `ui.py` line count), `ab25f8c` (re-pin
agent-orchestrator-bridge refs), `6cd00f5` (pin pytest for the unit suite),
`d5fe4d5`, `7c44152`, `b8603b9` (add section 13 to this log), `664ae4b` (add
`docs/VERSION-1.0.1-CHANGES.md`). Full detail on the close-out tail is in
section 13.5.

### 13.4 Verification at `7c44152`

- `ruff check .` - **All checks passed!**
- `pytest` - **167 passed**, 3 warnings (~90 s, offscreen).
- `scripts/precommit_check.py` (full run) - **SAFE TO COMMIT**; all seven
  acceptance harnesses `ALL CRITERIA PASS`, including `accept_wakeword.py`
  (previously the one blocker on a silent input device).
- CI run **38999772123** on `main` - **success** (`static-gate`, `lint`,
  `tests`).

### 13.5 Close-out commits after `7c44152` (the newest tail)

The five commits that followed the last code-verification point, in order.
The first is CI-relevant; the remaining four are documentation.

| Commit | Date | Subject | What it changed |
| --- | --- | --- | --- |
| `6cd00f5` | 2026-10-16 | ci: install pytest for unit suite (pin in requirements-dev.txt) | `.github/workflows/ci.yml` (+4/-1) now installs the dev requirements before the `tests` job, and `requirements-dev.txt` (+6/-2) pins `pytest` alongside `ruff==0.17.1` - the Windows CI job previously assumed `pytest` was preinstalled on the runner image. |
| `d5fe4d5` | 2026-10-16 | fix(voice): capture standby wake word via sounddevice (drop PyAudio); enforce Python 3.11/3.12 in bootstrap | Described in section 13.2. |
| `7c44152` | 2026-10-16 | test(wakeword): make the real-mic harness deterministic via a shared speech-energy gate | Described in section 13.2; the last commit carrying source changes. |
| `b8603b9` | 2026-10-17 | docs: log the upstream issue #2 fix (PyAudio -> sounddevice) in the 1.0.1 change log | Added section 13 (this addendum: 61 lines) to this log. |
| `664ae4b` | 2026-10-17 | Document Version 1.0.1 changes | Added `docs/VERSION-1.0.1-CHANGES.md` (851 lines) - the companion narrative change record covering the full release from the original provider/MCP request through the god-object split and item fixes. |

**Net effect on this log's claims:** commit-count figures were corrected
(section header: 103 -> 111 in range) and verification evidence was carried
forward to `664ae4b` (section 8). No behavioural claim in this log changed:
the post-`7c44152` tail is `6cd00f5` (CI plumbing) plus documentation only.

---

## 14. Provider-egress enforcement: reply ladder, settings-driven local model, LLM gateway (2026-10-18)

Sections 3.1 and 13 delivered the provider kill switches and the local-model /
local-speech stack, but exercising a local-only configuration showed the
switches governed only *part* of the reply path: background tools kept dialing
Google/OpenRouter with the toggles off, and a dead provider surface produced a
canned "I'm ready, sir." instead of a real error. A six-defect diagnosis
followed; this section records the diagnosis, the fixes, and the evidence.

### 14.1 Diagnosis

| # | Defect | Symptom | Root cause |
| --- | --- | --- | --- |
| 1 | No real fallback ladder | Selecting Custom/OpenRouter did not change the reply path; one Gemini error dead-ended the turn | `_fallback_reply` knew only `gemini -> custom`, never consulted `provider_router.chain()`, and swallowed per-provider errors |
| 2 | `LocalBrain` ignored Settings | The local provider dialed a fixed endpoint/model regardless of Settings | Endpoint/model were code constants; no availability probe, no response validation, no timeouts |
| 3 | `silence_chunks` scoping | The stream callback re-entered a counter the enclosing scope re-initialised | Missing `nonlocal silence_chunks` in `session_lifecycle._callback` |
| 4 | No HTTP timeouts | A wedged server held a worker forever | `llm_client` passed `timeout=None`; local/custom calls were unbounded |
| 5 | Canned failure reply | Every provider failure was reported to the user as "I'm ready, sir." | The ladder's error branch returned a fixed string instead of the failure |
| 6 | Tool layer bypassed `provider_router` | "Local only" still sent Google/OpenRouter traffic from background threads | Each tool built its own `genai.Client` or called OpenRouter directly; the kill switches only covered the chat path |

### 14.2 Reply ladder and local model

- **Ladder (`main.py`).** `_fallback_reply` now attempts the *selected*
  provider first (the full-featured path), then the remaining enabled providers
  in `provider_router.chain()` order; offline mode keeps `local` + `custom` and
  drops cloud; an active Gemini throttle still puts OpenRouter first (the
  `ec4df3a` policy is preserved). Each attempt records its failure reason, and
  the tail replaces the canned reply with a surfaced `provider_error`, an
  `ERR:` log line, a concise spoken failure, and the `finish_task_workspace`
  error branch. New `PROVIDER_LABELS` / `_provider_id_for_name` helpers map
  Settings names to router ids.
- **Local path.** New `_local_brain_reply` feeds the local model a single
  assistant-message history and one tool-loop follow-up, with tool-result
  summaries only as a last resort. `llm_client.chat_as(provider_id, ...)`
  routes an *explicit* provider behind `provider_router.guard`, supports
  images (OpenAI `image_url` content shape, so every link speaks one format),
  and raises actionable `RuntimeError`s instead of returning empty strings.
- **`core/local_brain.py` rewritten.** `local_ai_url` / `local_ai_model` from
  Settings are the single source of truth, re-read per operation; availability
  is probed with `GET {endpoint}/models` and an Ollama `/api/tags` fallback;
  `RESPONSE_TIMEOUT=180 s`, `STREAM_IDLE_TIMEOUT=60 s`, `PROBE_TIMEOUT=3 s`;
  a response without `choices` (or carrying `error`) raises `LocalBrainError`
  whose message names the endpoint.
- **`llm_client` timeouts.** `REQUEST_TIMEOUT=(10.0, 180.0)` replaces
  `timeout=None`, with per-exception logging (`or_client` already had them).
- **`core/session_lifecycle.py`.** Outer `silence_chunks = 0` plus the
  `nonlocal` declaration in `_callback` (defect 3).

### 14.3 `core/llm_gateway.py`: one egress for auxiliary LLM calls

New module. `generate_content(contents, *, model, config, api_key, system,
temperature, max_tokens, links, purpose)` mirrors the google-genai call shape,
so a call site migrates by swapping `client.models.generate_content(...)` for
`llm_gateway.generate_content(...)`:

- **The Gemini link forwards verbatim** to the SDK when the provider is
  enabled (dict or `types.*` config passed through; explicit `system` /
  `temperature` / `max_tokens` are merged only when supplied, otherwise the
  request stays byte-identical to a direct SDK call). **A disabled provider is
  never contacted** - not even as a fallback.
- **Failover** walks the enabled chain selected-provider-first (the same rule
  as the reply ladder, so local-first configurations extract memory and parse
  intents on the local model); offline mode drops the cloud links. Every link
  failure logs `[LLM Gateway] <purpose>: <pid> failed -> <reason>`, and a
  later-link success names the link that answered - cloud egress stays
  visible.
- **`AuxResponse`** carries `.text`, `.provider`, and a one-part SDK-shaped
  `.candidates` tree, so parsers written against the SDK
  (`.candidates[0].content.parts`) work unchanged on any link.
- **`GatewayError`** (a `RuntimeError`) joins every link's reason when nothing
  answers; model-candidate loops catch it and `break` instead of retrying the
  whole chain N times.
- **`links=("gemini",)`** restricts grounded tasks (web search's
  `google_search` tool) so live search is never faked from model memory - a
  disabled Gemini makes search fall back to the existing DDG path instead.
- JSON-mode callers keep their defensive parsing (fence stripping + fallback);
  no `response_format` is forced onto OpenAI-spec links.

**Migrated onto the gateway** - all 34 remaining `models.generate_content(`
call sites across 23 files, plus memory's two unconditional OpenRouter calls
(24 files in total), leaving only the gated TTS engine and the protocol-bound
Live paths on the SDK:

| Area | Files | Notes |
| --- | --- | --- |
| Conversation path | `memory/memory_manager.py` (2), `core/reply_flow.py`, `actions/web_search.py` | extraction/relevance now follow the selected provider instead of OpenRouter every turn; search stays grounded (`links=("gemini",)`) |
| Skill / auto-repair | `core/skill_forge.py` (2), `actions/auto_heal_engine.py` | model-candidate loops and the existing unified fallbacks retained |
| Agent loop | `agent/planner.py` (2), `agent/executor.py` (3), `agent/error_handler.py` (2) | `_fallback_plan` / replan behaviour preserved |
| Document & code tools | `actions/file_processor.py`, `code_helper.py`, `dev_agent.py`, `docx_tools.py`, `office_generator.py`, `pdf_tools.py` | most rewired through each file's existing client wrapper, covering every indirect call |
| Communication / devices | `actions/call_assistant.py` (4), `meeting_assistant.py` (2), `calorie_counter.py`, `circuit_assembler.py`, `brahma_dev_agent.py`, `mobile_autopilot.py` | `if api_key:` guards removed so failover works keyless; `meeting_assistant._transcribe_audio` no longer takes a client |
| Surfaces | `discord_bot.py`, `workspace_store.py`, `main.py` (3 reply helpers: text / Instagram / clipboard) | rate-limit suppression still works - `GatewayError` embeds the SDK reason verbatim |
| TTS gate | `core/speech_tts.py` | `speak()` skips an engine whose `provider_router` id is disabled (`_ENGINE_PROVIDER` + monkeypatchable `_provider_enabled`); `_speak_gemini` / `_probe_gemini` themselves are untouched, so their unit tests still define the contract, and the engine catalogue is unchanged |

### 14.4 Boot-warning cleanup: deferred QtMultimedia

`sound_manager.py` imported QtMultimedia at module scope *and* built its
module-level `sound_mgr` singleton during app boot, so the media backend
probed codecs and printed warnings in every boot log - even with sound effects
switched off and before anything played. The `QSoundEffect` annotation now
sits behind a `TYPE_CHECKING` guard, `_init_audio` runs from
`_ensure_audio()` on first `_play()` instead of in `__init__`, the
QtMultimedia import inside `_init_audio` is function-local, and a disabled
setting loads no multimedia at all.

### 14.5 Verification

- `scripts/precommit_check.py` - **SAFE TO COMMIT**: credential scan OK,
  vendoured/model licences OK, `pytest` **221 passed** (129.78 s), and all
  seven acceptance harnesses (`accept_391`, `accept_avatar`,
  `accept_instructions`, `accept_providers`, `accept_local_speech`,
  `accept_live_toggle`, `accept_wakeword`) **ALL CRITERIA PASS**. The two
  reported warnings (the tracked `hand_landmarker.task` size, ignored local
  files) predate this section.
- `ruff check .` - **All checks passed!**
- `accept_local_speech.py` alone - **ALL CRITERIA PASS**, including its
  section D, "Gemini Live path is untouched".
- `py_compile` sweep over all 29 touched files - clean.
- Repo grep: `genai.Client(` / `models.generate_content(` remain only in
  `core/llm_gateway.py` (the intended gateway), the gated
  `core/speech_tts.py::_speak_gemini`, the two Gemini Live paths
  (`core/session_lifecycle.py`, `actions/screen_processor.py`), and the
  manual `live_handshake_probe.py` diagnostic.

### 14.6 Intervening commits (2026-10-18, not previously documented)

Four commits landed between `664ae4b` and this section:

| Commit | Subject | Footprint |
| --- | --- | --- |
| `fce9070` | Scale tool search limits with session budget | 10 files, +1,741/-22 |
| `cd4bd30` | Add JIT on-demand skills library | 18 files, +2,837/-227 |
| `9b4a877` | Add paginated tool and skill listings | 9 files, +442/-49 |
| `0183b02` | Route voice by provider and migrate Gemini SDK | 15 files, +356/-104 |

### 14.7 Deliberate scope notes

- **Gemini Live stays Gemini.** Both Live paths (voice in `session_lifecycle`,
  vision in `actions/screen_processor.py`) use `client.aio.live.connect`, a
  Gemini-only wire protocol; they are gated by key presence, and
  `accept_local_speech` section D asserts the voice path is untouched.
- **The voice "Test" button still probes Gemini directly** - it is an explicit
  user diagnostic; the reply path's `speak()` is what the provider gate
  protects.
- Several file-local `_get_api_key()` defs became unused; they were left in
  place to keep the diff minimal (no lint violation).
- Non-Gemini links ignore Gemini-only config (`google_search`,
  `response_mime_type`); those callers parse defensively either way.

### 14.8 Commit

Landed as commit **`b820292`** (subject: `fix(providers): enforce provider
toggles on every LLM egress via core/llm_gateway`), pushed to `origin/main`
the same day with CI run `39146847577` green (4 m 28 s).

---

## 15. Post-release sweep: gateway tests, egress acceptance, bounded waits, dead code (2026-10-18)

Section 14 fixed the provider-egress defects; this same-day follow-up closes
the gaps that review left behind, in five themes.

### 15.1 The gateway got tests (the one untested new module)

`core/llm_gateway.py` had landed with zero direct coverage - the 221-test
suite exercised neither its chain walk nor its response shim.
**`tests/test_llm_gateway.py` (19 tests, no network, no google.genai import)**
pins the routing contract: the `AuxResponse` SDK shape; chain ordering
(selected provider first, offline cloud-drop, disabled providers excluded even
when Settings still names them); "a disabled provider is never contacted";
failover order with per-link failure logs and the `answered by <link>` line;
`GatewayError` joining every reason; empty responses counted as failures; the
`links=("gemini",)` grounded restriction (refuses and dials nothing);
`_split_contents` / `_system_text` / `_merged_config` parsing; and
model/config delivery to the Gemini link.

The new file earned its keep immediately: `test_split_contents_flattens_text_and_image_parts`
caught a real defect - **dict-shaped inline-data parts were never unwrapped**
(`getattr(dict, "data")` returns `None`, so a JSON-side image part was
stringified into the prompt text instead of attached as an image). The gateway
now reads `data` / `mime_type` from either the attribute-style part or the
plain dict, exactly as its docstring promised.

### 15.2 The acceptance harness now proves egress honours the kill switches

`accept_providers.py` gained **section K** ("Gateway egress honours the same
switch"), driving the *real* chain computation from the *real* toggles with
the links monkeypatched to recorders: with only `local` enabled the gateway
dials exactly `['local']`; offline mode collapses the live chain to
`['local', 'custom']`; a grounded call (`links=("gemini",)`) with Gemini off
refuses with `disabled in Settings` **and dials nothing**; failover from a
failing first link reaches the next one and records it as the answering
provider; an empty chain raises instead of answering.

### 15.3 Bounded tool wait

`core/tool_exec.py` waited on loop-scheduled tool futures with
`future.result(timeout=None)` - one hung tool wedged the reply path forever.
`TOOL_EXEC_TIMEOUT_SECONDS = 300` now bounds the wait (generous, because
dev-agent codegen and transcription legitimately run for minutes); the
existing `except Exception` turns a timeout into a result string the modelcan read, and the timed-out coroutine deliberately finishes in the background
rather than being cancelled.

### 15.4 Dead code removed

The gateway migration left file-local `_get_api_key()` helpers behind in
**nine files** (`agent/error_handler.py`, `agent/executor.py`,
`actions/code_helper.py`, `actions/dev_agent.py`, `actions/docx_tools.py`,
`actions/file_processor.py`, `actions/computer_settings.py`,
`actions/meeting_assistant.py`, `actions/pdf_tools.py`) - verified
unreferenced by any module or test, and removed together with the
`API_CONFIG_PATH` constants and `get_user_data_dir` / `json` imports that only
fed them (ruff F401), plus the orphaned `_get_base_dir()` in
`computer_settings.py`. Kept deliberately: `agent/planner.py`'s copy
(imported by `mobile_autopilot`) and `main.py`'s (the gateway,
provider_router and the Live paths use it).

Also in this theme: the last unbounded HTTP call in the tree
(`download_offline_voices.py`'s `urlopen`) got `timeout=60`; every other
`requests` / `urlopen` call site was audited and carries a timeout.

### 15.5 Tree and boot hygiene

Local clutter deleted (`main.py.bak` 263 KB, `ui.py.bak` 650 KB,
`core/prompt - Copy.txt`, `ig_debug.log`, `FATAL_CRASH_RECOVERED.log`), and
the 7.6 MB `config/models/hand_landmarker.task` left the working tree: it has
not been tracked since `ef33a02`, and `ui_gesture` already downloads it on
demand (`_download_hand_landmarker_model`, 60 s timeout) with a
manual-placement message if the download fails. This clears the pre-commit
large-file advisory. The `QtMultimedia` deferral from section 14.4 was also
verified directly: an offscreen `QApplication` constructing and playing a
`QSoundEffect` prints **no** backend warnings and exits cleanly, so boot logs
stay clean and the multimedia cost is paid only when a sound actually plays.

### 15.6 Verification

- `scripts/precommit_check.py` - **SAFE TO COMMIT**: credential scan OK,
  `pytest` **240 passed**, all seven acceptance harnesses
  `ALL CRITERIA PASS` (`accept_providers.py` including the new section K).
- `ruff check .` - **All checks passed!**

### 15.7 Commit

Landed as the single commit carrying this section (subject: `chore(quality):
gateway tests, section-K egress checks, bounded tool wait, dead code removed`);
see `git log`.

---

## 16. System prompt rewritten as an agent brief; offline context budget (2026-10-18)

Sections 14-15 fixed what the system *sends* to the model; this section fixes
what the model *knows*. Exercising the rebuilt assistant showed it answering
like a chatbot - describing work instead of doing it, stopping after one tool,
asking for the date or the user's name that the context already carries. The
cause was `core/prompt.txt`: a 98-line / 6 KB persona note written for the old
single-engine build, silent on the multi-step loop, the deferred tool surface,
and the memory/safety rules the 1.0.1 platform depends on.

### 16.1 The rewrite

`core/prompt.txt` was replaced (98 lines / 6,066 bytes -> 107 lines / ~14.9 KB)
with a brief organised around how the system actually runs:

- **HOW A TURN WORKS** - a turn is a work session, not an answer: tool results
  feed the next decision, independent calls can go out together, the turn ends
  when the goal is verifiably done. It documents that the system already
  announces each tool ("Working on X... / X completed."), so the model must not
  double-narrate, and closes with a worked end-to-end example.
- **PRINCIPLES** - act don't narrate, decide don't defer, look before acting
  (the injected date/location/memory blocks exist so it stops asking), chain
  state across tools, treat tool results as ground truth, recover from errors.
- **WHAT THIS SYSTEM CAN DO** - a domain inventory (memory, vision, PC, web,
  documents, people, time/attention, devices, media/wellbeing, building and
  self-evolution, MCP + Skills Library) so the model knows the capability set
  exists before it searches for any part of it.
- **CONTEXT BLOCKS AROUND THIS TEXT** - documents the time/location/memory
  preamble and the `[ACTIVE SKILL CONTEXT]` block that
  `session_lifecycle._build_config` appends around this file, so the model uses
  them instead of duplicating or re-asking them.
- **TOOL SURFACE** / **SKILLS LIBRARY** - teach the discovery loop for the
  ONE-391 deferred surface: `search_tools` -> `call_tool`, `list_tools` for
  browsing, `get_skill_manual` for group rules, `search_skills` -> `load_skill`
  for playbooks. The `SKILLS LIBRARY` header is kept because
  `docs/skills-library-jit-on-demand-skills.md` pins it.
- **OPERATING THE TOOLS** - the hard-won routing rules from the old prompt are
  preserved verbatim in intent (preview -> organise, `word_document` vs
  `file_processor`, `browser_control` not `open_app`, device cross-routing,
  `game_updater` exclusivity, `agent_task` never for single commands,
  `confirmed='yes'` gating, stay-silent after `screen_process`), with new
  routes for the 1.0.1 tool set (`agent_task`, `skill_forge`, `auto_heal
  learn_rule`, `circuit_assembler`, the instagram_* family).
- **MEMORY DOCTRINE / SAFETY AND REVERSIBILITY / AUTONOMY / OFFLINE AND
  DEGRADED MODES / COMMUNICATION** - when to `recall_memory` vs `save_memory`
  silently, preview-before-bulk and immediate `undo`, `skill_forge` when a
  capability is missing, honest degradation when a provider is down, and the
  spoken-output rules (no markdown, bullets or raw URLs).

Unconstrained by the gate: `accept_instructions.py` asserts injector *wiring*
only, and a repo-wide grep confirmed no test or harness asserts prompt
*content*. Also removed with `git rm`: the tracked stale duplicate
`core/prompt - Copy (2).txt` (nothing references it; section 15.5 removed its
untracked sibling).

### 16.2 Offline context budget (`LOCAL_NUM_CTX`)

The rewrite more than doubled the prompt, and the offline path grew with it:
`core/prompt.txt` + the skill-context block + the tool surface now exceed
Ollama's OpenAI-compatible default `num_ctx` (2048-4096 tokens), which
truncates *silently* - the 3B fallback model would lose its operating
instructions before the user's turn, i.e. exactly the chatbot regression the
rewrite exists to fix. `core/local_brain.py` now sends
`options: {"num_ctx": 8192}` on **both** local paths (`chat_complete` and
`generate_chat_stream`): 8192 covers the ~3.7K-token prompt with headroom for
skill context, tools and a real answer; the bundled 3B models (qwen2.5,
llama3.2) support 32K natively; non-Ollama OpenAI-compatible servers ignore
the unknown `options` field.

### 16.3 `LocalBrain` constructor pin honoured

Writing the regression tests exposed a latent contract violation: the
constructor docstring promised "an explicit constructor argument pins the
value for this instance", but `reload_settings()` only used the pin to *block*
Settings - `_default_model` stayed at the Ollama default, so
`LocalBrain(default_model=...)` silently ignored its argument. The pinned
values are now applied. No production caller passes arguments (the singleton
wiring is unchanged); the fix makes the documented contract true.

### 16.4 `tests/test_local_brain.py` (7 tests, no network)

Pins the offline endpoint contract: `options.num_ctx` present on the
non-streaming payload (plain turn **and** tool-bearing turn), the budget
provably covers `core/prompt.txt`, the pinned model/endpoint reach the payload,
an `error` body or missing `choices` raises `LocalBrainError` instead of an
empty success, and Gemini-style `OBJECT`/`STRING` schema types are lowercased
to JSON-Schema casing.

### 16.5 Verification

- `scripts/precommit_check.py` - **SAFE TO COMMIT**: credential scan OK,
  `pytest` **247 passed**, all seven acceptance harnesses
  `ALL CRITERIA PASS`.
- `ruff check .` - **All checks passed!**

### 16.6 Commit

Landed as the single commit carrying this section (subject: `feat(prompt):
rewrite system prompt as multi-step agent with full capability map`); see
`git log`.

---

## 17. Hardware-aware timeout patience for the local model (2026-10-18)

Running the app on slow CPU hardware exposed the next defect: turns were cut
off mid-inference instead of being allowed to finish. The local reply path is
non-streaming, so its single socket budget covers **prompt prefill of the
~4K-token system context plus full decode** - and on a CPU-resident 3B model
(~10-20 tok/s prefill) that legitimately takes several minutes, against a
GPU-era budget of 180 s.

### 17.1 Diagnosis

| Clock | Value | Slow-CPU failure |
| --- | --- | --- |
| `chat_complete` (`RESPONSE_TIMEOUT`) - the main local turn and its follow-up | 180 s, whole body | Aborts mid-prefill: prefill alone can need 200-400 s before one response byte exists |
| Gateway local link (`llm_client.chat_as("local", ...)` -> `REQUEST_TIMEOUT`) | 180 s read | Memory extraction / intent parsing dies at the same point |
| `generate_chat_stream` (first recv) | 60 s inter-chunk | urllib's per-recv timeout also bounds time-to-first-token, i.e. prefill - latent (no callers today) but a trap |

Two naive fixes were considered and rejected on the record: **`timeout=None`**
reintroduces the section-14 defect class ("a wedged server held a worker
forever") - no failover, no surfaced error, accumulating stuck worker threads
- and a **flat raise** taxes GPU-resident machines with the same slow failure
detection they do not need. The chosen fix is patience exactly where the
hardware requires it, finite in every configuration.

### 17.2 Timeout profiles (`core/local_brain.py`)

`TIMEOUT_PROFILES` - `fast` (180 s, the historical behaviour and the better
failure bound) and `slow` (600 s, covering CPU prefill plus a full answer) -
each with a `response` (non-streaming, whole body) and `first_token`
(streaming, prefill) budget. `active_profile()` resolves which applies: an
explicit `local_timeout_profile` Settings value (`auto`/`fast`/`slow`,
re-read with every operation like `local_ai_url`) wins; `auto` (the default)
asks the runtime where the model actually lives; anything inconclusive keeps
`fast`, so an error never invents patience.

### 17.3 CPU-residency detection

Ollama's `GET /api/ps` reports each loaded model's `size_vram`;
`size_vram == 0` means the model is RAM-resident, i.e. CPU inference.
`_cpu_resident_from_ps` parses defensively (tag-variant prefix matching in
either direction, a single loaded model treated as unambiguous, unknown
shapes returning *unknown* rather than a guess); `_model_is_cpu_resident`
caches the verdict for 60 s per endpoint+model so a tool loop's follow-up
calls do not re-probe, derives the admin root from the configured `/v1`
endpoint, and treats every failure (non-Ollama server, probe timeout) as
"not CPU" -> fast defaults.

### 17.4 Two-stage stream watchdog

`_open_stream` now opens with the profile's `first_token` budget (prefill
included), and `generate_chat_stream` tightens the socket to
`STREAM_IDLE_TIMEOUT` (60 s, unchanged) after the first byte proves the
server is generating. The switch reaches through `http.client`'s response
object to the socket - a CPython implementation detail - so it is
defensively probed: on any shape failure the larger (still bounded)
first-token budget stays in place. `chat_complete` also now calls
`reload_settings()` at the top, honouring the module's documented
re-read-per-operation contract for the profile.

### 17.5 The gateway's local link borrows the same budget

`llm_client._local_read_timeout()` resolves
`(REQUEST_TIMEOUT[0], local_brain.response_timeout())` for the `local`
provider - one budget, one source of truth - and `_openai_chat_completion`
takes the (connect, read) tuple as a parameter (default `REQUEST_TIMEOUT`,
so custom/cloud links are unchanged). The timeout log line now reports the
budget actually used.

### 17.6 Tests

`tests/test_local_brain.py` grew to **18 tests** (11 new, no network): slow
budget for a RAM-resident model; fast for VRAM-resident; probe failure keeps
fast; Settings `slow`/`fast` overrides beat auto-detection and unknown values
fall back to auto; the `/api/ps` parser's full truth table; the 60 s probe
cache; the stream's first-token open budget; the post-first-byte tightening
(and its defensive fallback); and the gateway's borrowed budget with its
own fallback.

### 17.7 Verification

- `scripts/precommit_check.py` - **SAFE TO COMMIT**: credential scan OK,
  `pytest` **258 passed**, all seven acceptance harnesses
  `ALL CRITERIA PASS`.
- `ruff check .` - **All checks passed!**

### 17.8 Commit

Landed as the single commit carrying this section (subject: `fix(local):
hardware-aware timeout profiles so slow CPUs are not cut off mid-inference`);
see `git log`.

---

## 18. Tool-less local models no longer kill the turn (2026-10-18)

Field report: with only the local provider enabled and `gemma2:2b` selected,
every turn died with

> `HTTP 400 Bad Request {"error":{"message":"registry.ollama.ai/library/gemma2:2b does not support tools"}}`

followed by "No reply from any provider". Root cause: Gemma 2 (and other
models such as phi2) ships **no tool-calling template**, so Ollama rejects
any `/v1/chat/completions` request that carries a `tools` array - and the
local reply path always advertises the bounded tool surface, so the model
never got to answer anything.

### 18.1 Learn-once tool-less fallback (`core/local_brain.py`)

`chat_complete` now absorbs **exactly** the tools rejection and nothing
else: on a `LocalBrainError` whose message contains Ollama's (and
OpenAI-compatible servers') "does not support tools", it marks the
endpoint+model pair tool-less, retries the same turn once **without** tools,
and logs `[Local Brain] model '<m>' does not support tool calling; retrying
this turn without tools`. The turn then answers normally - the model keeps
its conversational ability, it simply cannot call tools. Every later turn
for that model skips the doomed attempt entirely (no wasted round-trip, no
400 per message), and `generate_chat_stream` honours the same memory. The
verdict is keyed per endpoint+model, so selecting a tool-capable model
(qwen2.5:3b, llama3.2:3b) next restores the full tool surface; every
*unrelated* 400 keeps propagating to the reply ladder with no silent retry.

### 18.2 Tests

Five new tests in `tests/test_local_brain.py` (23 in the file): the 400 is
absorbed and the retry carries no tools; the memory sticks for later turns;
the verdict is per model, not global; an unrelated 400 raises with exactly
one attempt; the stream path skips tools for a remembered model.

### 18.3 Verification

- `scripts/precommit_check.py` - **SAFE TO COMMIT**: credential scan OK,
  `pytest` **263 passed**, all seven acceptance harnesses
  `ALL CRITERIA PASS`.
- `ruff check .` - **All checks passed!**

### 18.4 Commit

Landed as the single commit carrying this section (subject: `fix(local):
degrade gracefully when the local model has no tool template`); see
`git log`.

---

## 19. Auto timeout profile: cold-start gap closed; inconclusive defaults to slow (2026-10-18)

Field report: with `qwen3.5:2b` on the local provider, turns *still* died at
180 s - both paths, verbatim:

> `ERROR:llm_client:[LLM Client] Local AI timed out after 180s reading http://localhost:11434/v1/chat/completions ... (read timeout=180.0)`
> `[BRAHMA EVO] Local model failed: http://localhost:11434/v1/chat/completions: timed out after 180s`

i.e. `active_profile()` resolved to `fast` on hardware that needs the
patient budget. Two defects in the section-17 auto-detection:

1. **Cold start (deterministic).** Ollama loads a model *lazily on the
   first request*, so before the first turn `GET /api/ps` does not contain
   the target model -> the verdict is inconclusive -> section 17 resolved
   inconclusive to `fast`. The one turn that most needs the 600 s budget
   (load + cold prefill) was guaranteed to get 180 s, on every process
   start, on exactly the slow hardware the profiles were built for.
2. **Partial offload counted as fast.** `_cpu_resident_from_ps` returned
   "fast" for *any* `size_vram > 0`, but whatever layers still sit on the
   CPU gate prompt prefill - a small GPU with a partly offloaded model is
   slow, not fast.

### 19.1 `fast` must be earned; `slow` is the safe default (`core/local_brain.py`)

- `_cpu_resident_from_ps` -> **`_ps_load_speed`**, a three-way verdict:
  `"fast"` only when the load is (nearly) fully VRAM-resident
  (`size_vram >= 0.9 * size`), `"slow"` on zero VRAM *or* partial offload,
  `None` when the body cannot say (model not loaded yet, non-Ollama shape,
  sizes missing or unparseable - a VRAM figure without a total could be a
  partial offload and never earns `fast`).
- `_model_is_cpu_resident` -> **`_probe_load_speed`**. Conclusive verdicts
  are cached for `PS_CACHE_TTL` as before, and probe *failures* are now
  cached too (a non-Ollama server will not repair itself mid-session, so
  stop re-failing on every call) - but an inconclusive verdict is
  deliberately **not** cached: "the model is not loaded yet" resolves
  itself the moment this very turn loads it, and pinning it for 60 s would
  keep the wrong verdict for the turns that follow.
- `DEFAULT_TIMEOUT_PROFILE` **`"fast"` -> `"slow"`**; `active_profile()`
  = explicit `local_timeout_profile` Settings value > probe verdict > slow.
  Rationale: for a *local* model, patience over fast failure - a refused
  connection still surfaces instantly (it never touches a timeout), a
  genuinely hung request stays bounded at 600 s (section 14's no-hang
  property intact, both budgets finite), and `fast` is a verdict to earn,
  never an assumption from missing evidence.
- `llm_client._local_read_timeout` and the streaming first-token budget
  inherit the corrected resolution unchanged - both of the reported 180 s
  failures now resolve to 600 s on this hardware, while a fully
  VRAM-resident model still gets 180 s as the tight failure bound.

### 19.2 Tests (`tests/test_local_brain.py`, 25 in the file)

Updated: the parsing test now pins the three-way verdict (full offload =
fast, partial = slow, missing/unknown sizes = unknown); the probe-failure
test flipped to the new contract (slow *and* the failure is cached - one
probe only); the stream-budget test patches the renamed seam. New:
`test_partial_offload_gets_slow_budgets` (end-to-end through
`chat_complete`: 2 GB of 8 GB offloaded -> 600 s) and
`test_cold_start_resolves_to_slow_and_reprobes` - the field regression:
empty `/api/ps` -> 600 s response *and* first-token budgets, with the
inconclusive verdict re-probed (never pinned) on every resolution.

### 19.3 Verification

- `scripts/precommit_check.py` - **SAFE TO COMMIT**: credential scan OK,
  `pytest` **265 passed**, all seven acceptance harnesses
  `ALL CRITERIA PASS`.
- `ruff check .` - **All checks passed!**

### 19.4 Commit

Landed as the single commit carrying this section (subject: `fix(local):
resolve the auto timeout profile to slow until fast is proven`); see
`git log`.

## 20. Post-release (2026-10-20): the two-tier prompt, JIT awareness, and the custom endpoint

The model was struggling with tool use, and weaker on the custom endpoint
than on the local one. Both symptoms had the same root: the prompt was one
14.9 KB document sent in full every turn, and the custom endpoint - unlike
the local brain and Gemini Live - was never given the prompt *or* any tools
at all.

### 20.1 The split (`core/prompt.txt`, `core/prompt_parts.py`)

`core/prompt.txt` is now two tiers in one file, separated by an exact
`--- MANUAL ---` marker line. The loader (`core.prompt_parts`) returns one
string per tier, so every consumer keeps a single read path:

- **Core** (resident every turn, 9.9 KB): identity, HOW A TURN WORKS,
  PRINCIPLES, WHAT THIS SYSTEM CAN DO (the capability inventory), HOW TO USE
  YOUR CAPABILITIES (the discovery rules), context blocks, memory doctrine,
  safety, autonomy, offline, communication.
- **Manual** (on demand, 6.3 KB): the `TOOL SURFACE` and `SKILLS LIBRARY`
  discovery detail plus `OPERATING THE TOOLS (intent -> tool)`, the
  per-domain routing table.

The routing table was the largest single block and the most lookup-shaped
content in the file, so it is the right thing to hold back; the capability
inventory and the "you have discovery tools, use them before claiming a
capability is missing" rules are behaviour and stay resident, because the
system prompt is what teaches the model it *has* tools at all. Splitting the
file this way also keeps the `SKILLS LIBRARY` header that
`docs/skills-library-jit-on-demand-skills.md` pins, now in the manual half
where the loader still finds it.

`prompt_tier` (Settings) chooses the tier: `lite` (default) sends the core
only, `full` restores the old combined prompt, `auto` prefers the full prompt
except on a loopback endpoint. A prompt file predating the split has no
marker and loads as a single core, so an older file still boots.

### 20.2 The manual as a tool (`get_tool_surface`)

The manual half is served by a new always-advertised discovery meta-tool,
`get_tool_surface` (`core/tool_index.py`, `core/tool_bridge.py`). It reads the
manual out of `core/prompt.txt` through `core.prompt_parts` (the single source
of truth), takes an optional `section` substring filter, and like its peers it
fails soft - a missing file or a miss returns a readable line, never an
exception. Being a meta-tool means it is advertised on every path that has a
tool surface (Live, local, custom) and never counted against the per-session
tool budget beyond its one always-on slot.

`META_TOOLS` therefore grew from seven to eight entries. Every place that
derived from `len(META_TOOLS)` self-corrected; the places that had hardcoded
the old arithmetic did not, and were fixed: the Settings hint's fallback
count and its worked examples (`core/ui_connectivity.py`), two test
expectations, and four stale "seven" comments.

### 20.3 The custom endpoint gets the same agentic turn (`llm_client.py`, `main.py`)

The reply ladder's `custom` branch called `chat_as("custom", ...)` with a
two-line system prompt and no `tools` argument, and `chat_as` had no way to
carry one - so a custom OpenAI-compatible endpoint could chat but never act.
It now runs the same turn the local brain gets:

- `llm_client` grew `chat_as_completion` (the full assistant message, tool
  calls included, with `tools` riding the request) while `chat_as` becomes a
  thin text wrapper over it, so existing callers are unchanged. Its
  `full_messages` argument carries a complete conversation verbatim, which is
  the follow-up shape of a tool loop: the array already holds the system
  prompt, the assistant tool-call turn and the tool results, so re-wrapping it
  would send two system messages and an empty user turn - something
  OpenAI-compatible servers commonly reject. `tests/test_llm_client_completion.py`
  pins that contract (verbatim send, single system message, no empty user
  turn, no alias into the caller's list).
- `main._agentic_seed_messages` builds the system message once for every
  OpenAI-compatible path: tiered prompt, the critical directive, the
  on-demand-manual pointer, and the JIT skill context.
- `main._custom_brain_turn` advertises the same bounded, relevance-ranked
  tool surface the local path builds (`local_brain.resolve_tools`) and
  finishes through the shared `_finish_tool_turn`.
- A custom endpoint that rejects the tools array is remembered as tool-less
  and degrades to a plain reply instead of failing the turn - the same
  lesson `core/local_brain.py` learned for Gemma 2, applied to the custom
  link.

### 20.4 Verification

- `pytest` - **331 passed** (11 new in `tests/test_prompt_parts.py` pinning
  the marker contract, the resident/manual split, tier selection and the
  `get_tool_surface` handler, plus 6 new in
  `tests/test_llm_client_completion.py` pinning the completion-message
  contract).
- `accept_local_speech.py` - new section F, `ALL CRITERIA PASS`.
- `accept_391.py` - `ALL CRITERIA PASS` (advertised frame still 24 of 173).
- `accept_instructions.py` - `ALL CRITERIA PASS`.
- `scripts/precommit_check.py` - **SAFE TO COMMIT**: credential scan OK,
  `pytest` 331 passed, all seven acceptance harnesses `ALL CRITERIA PASS`.
- `ruff check .` - **All checks passed!**

### 20.5 Three follow-on defects found and fixed while verifying

Running the gate surfaced three real defects, two of which predated this
change set and were fixed here because they blocked the required green gate.

**A. Ranking was not order-stable for equal scores** (`core/tool_index.py`).
`ToolIndex.search` fused backend results and sorted them with
`hits.sort(key=lambda h: h.score, reverse=True)` - no secondary key. Two hits
with the same Reciprocal-Rank-Fusion score therefore kept whatever order the
backends happened to emit them in, so two builds of the *same* intent could
differ. Pinned by the pre-existing assertion `assert a == b` in
`tests/test_tool_bridge.py::test_payload_is_deterministic_for_the_same_intent`.
Fixed with a deterministic tie-break: `key=lambda h: (-h.score, h.name.lower())`.

**B. The determinism test raced the semantic warm-up.** The same test also
compared two builds taken microseconds apart, while `ToolIndex.rebuild`
deliberately warms the embedding backend on a *daemon thread*
(`_EmbedBackend.schedule_build`) so a Live handshake never blocks on the model.
When the warm-up landed between the two builds, the fused backend set grew and
the ordering changed - by design, not by defect. The test now waits for the
backend set to settle (the same 90 s poll pattern
`tests/test_tool_index.py` already uses for the semantic backend) before it
asserts determinism, so it tests the promise ("stable once settled") rather
than the warm-up race.

**C. `accept_wakeword.py` section A failed with no usable capture device.**
The section drives the REAL microphone. On this machine PortAudio could not
open *any* capture endpoint on *any* host API (`-9999 Unanticipated host
error`, `MME error 1`), which was verified to be environmental: the harness
fails identically on the commit before `b402f21` and on a detached worktree of
`986dc71`, and a bare `sounddevice.InputStream(...)` open fails outside the
repo too. Section A now probes first (`_capture_device_usable`) and, when no
device can be opened, prints `[SKIP] no usable capture device` and returns
without recording a failure - it still fails hard if a device opens but the
recognition pipeline never fires. Sections B/C/C2 (scripted capture) continue
to exercise the real detection logic with no hardware.

**D. Four `ruff` errors from `b402f21`.** The prior commit left one unused
import and one dead local in `core/ui_voice_library.py`, an unused `json`
import in `tests/test_voice_catalog.py`, and an unused `speech_tts` import in
`tests/test_voice_library_ui.py`. All four were fixed (the dead local `label`
was recomputed per queue entry below it, so removing the binding is behaviour
-preserving). With them cleared the repository is `ruff`-clean.

