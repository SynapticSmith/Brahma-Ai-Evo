# GitHub Actions Remediation Backlog

**Document Role:** Post-remediation CI/CD defect ledger for the "broken Actions" reports  
**Status:** `PHASE A APPLIED — NOT YET PUSHED`  
**Execution gate:** Cleared; `docs/REMEDIATION-TRACKER.md` is `COMPLETE`. Phase A is applied in
the working tree and passes locally. Phases B and C are still unapplied and need a decision.  
**Investigated:** 2026-11-08  
**Evidence sources:** live GitHub Actions API, `.github/workflows/*.yml`, `tests/test_api_chat.py`,
`brahma_api/*`, `agent/providers.py`, and [`docs/scan-reports/WP-11-tests-packaging-and-ci.md`](scan-reports/WP-11-tests-packaging-and-ci.md)  
**Related:** [`docs/REMEDIATION-TRACKER.md`](REMEDIATION-TRACKER.md),
[`docs/SYSTEM-ANALYSIS-MASTER-REPORT.md`](SYSTEM-ANALYSIS-MASTER-REPORT.md)

---

## 0. TL;DR

There are **two different things** being called "broken Actions", and they are not the same problem.

| # | Where | What GitHub shows | Is it a code failure? | Who can fix it |
|---|---|---|---|---|
| 1 | `SynapticSmith/Brahma-Ai-Evo` (fork / local `origin`) | `CI` is red on every push to `main` since **2026-11-04** | **Yes.** Exactly one test fails: `test_auth_can_be_disabled_on_purpose` (`503 == 200`). 1,521 pass. | Repo owner (you) |
| 2 | `titechprabhasolutions/Brahma-Ai-Evo` (upstream) | Nine red **"Windows Python checks"** runs at `0s` | **No.** They never executed a single step. `conclusion=action_required` is GitHub's fork-PR approval gate. | An upstream maintainer with write access |
| 3 | Both remotes | No failing runs today, but latent defects | **Latent.** `build-all-platforms.yml` cannot work on macOS/Linux; tag pushes trigger two builds. | Repo owner / maintainer |

**Bottom line:** the visible red CI is a **one-line test hermeticity bug**, not a product regression.
The remediation tracker does **not** cover it, so it will still be red after Batch R-4 unless fixed.
The packaging workflow defects will only surface the first time a `v*` tag is pushed.

---

## 1. What GitHub is actually reporting

### 1.1 Fork CI — a real, reproducible failure

Workflow: `CI` (`.github/workflows/ci.yml`) on `SynapticSmith/Brahma-Ai-Evo`, branch `main`.

| Evidence | Value |
|---|---|
| Last green run | `41699335035` — `2026-11-03T19:01:27Z` — `b188305` |
| First red run | `41748636493` — `2026-11-04T00:40:56Z` — `f43314c` |
| Latest red run | `42467687447` — `2026-11-08T09:33:30Z` — `3263685` |
| Result | `1 failed, 1521 passed, 5 skipped in 356.34s` |

Every red run in that window fails the **same** test. Job-by-job, the latest run is:

| Job | Result |
|---|---|
| Static gate (secrets / licences / large files) | ✅ success |
| Ruff lint | ✅ success |
| Harness on Python 3.11 | ✅ success |
| Harness on Python 3.12 | ✅ success |
| Harness on Python 3.13 | ✅ success |
| **Unit suite** | ❌ **failure — step `Run pytest`** |

Only the full unit suite fails. The stdlib harness matrix and the static gate are healthy.
The regression starts at commit `f43314c` ("feat(api): OpenAI-compatible port (Phase 2: The Surface) …"),
which is the commit that added the API test surface.

### 1.2 Upstream Actions — an approval gate, not a test failure

`gh run list` on `titechprabhasolutions/Brahma-Ai-Evo` returns **9** runs, all:

- workflow: `Windows Python checks` (`.github/workflows/windows-checks.yml`)
- event: `pull_request`, branch `feature/gesture-hud-embedded-browser`
- `status: completed`, `conclusion: action_required`
- duration `0s`, **`jobs_count = 0`**, `run_started_at == updated_at`

The workflow file is **not on `main`** — it is added by PR #6 from the fork
`AnmolISHere007/Brahma-Ai-Evo`. GitHub's API confirms the PR author has
`author_association = NONE` and `head.repo.fork = true`. GitHub therefore requires a
maintainer to approve the workflow before any step runs. That is the entire meaning of
`action_required` here.

Two practical consequences:

1. **Nobody can diagnose a "failure" because nothing ran.** There are no logs or steps.
2. **You cannot approve it.** Your permission on upstream is `READ`; approval needs write
   access (or a maintainer changing the fork-PR approval policy).

PR #5 (`feature/ultron-gesture-browser-hud`) has no workflow file, so it produced no runs.

### 1.3 Workflow inventory drift (important)

The two remotes do **not** contain the same workflows:

| Workflow | Upstream `main` | Fork `main` (local `origin/main`) |
|---|---|---|
| `ci.yml` (CI) | ❌ absent | ✅ present |
| `build.yml` (Build) | ❌ absent | ✅ present |
| `build-all-platforms.yml` | ✅ present | ✅ present |
| `windows-checks.yml` | ❌ only in a PR branch | ❌ absent |

So upstream currently has **no push/PR test gate at all** — only a fictional multi-platform
build workflow. If the upstream repo is meant to be the canonical one, the CI definition has
to be contributed there; fixing the fork alone will not protect upstream.

---

## 2. Root cause of the red CI: a non-hermetic test

### 2.1 The failure

```
FAILED tests/test_api_chat.py::test_auth_can_be_disabled_on_purpose - assert 503 == 200
E       assert 503 == 200
E        +  where 503 = <Response [503 Service Unavailable]>.status_code
tests\test_api_chat.py:195: AssertionError

ERROR llm_client:llm_client.py:87 [LLM Client] Failed to load settings:
      [Errno 2] No such file or directory:
      'C:\Users\runneradmin\AppData\Local\BrahmaAI\config\app_settings.json'
```

The test is at [`tests/test_api_chat.py:191-196`](../tests/test_api_chat.py):

```python
def test_auth_can_be_disabled_on_purpose(tmp_path):
    config = make_config(tmp_path, auth_required=False)
    client = build_client(config)
    response = client.post("/v1/chat/completions", json=BODY)
    assert response.status_code == 200
    assert response.json()["brahma"]["lane"] == "agent"
```

### 2.2 Why it returns 503 on a runner

1. `auth_required=False` makes the server use the synthetic anonymous principal
   ([`brahma_api/server.py:76-88`](../brahma_api/server.py)); that principal has **no
   `provider` pin**.
2. The runner therefore falls back to `config.default_provider`, whose default is
   **`"auto"`** ([`brahma_api/config.py:133`](../brahma_api/config.py)).
3. `AgentRunner` builds the completer with that provider
   ([`brahma_api/runner.py:172-183`](../brahma_api/runner.py)).
4. `resolve_chain("auto")` asks Settings for a chain. With no `app_settings.json` on the
   runner, Settings has no preference, so it falls back to
   `_reachable_auto_order()` ([`agent/providers.py:121-135`](../agent/providers.py#L121-L135)):
   - no live local OpenAI-compatible server → no `local`
   - no custom URL → no `custom`
   - no OpenRouter key → no `openrouter`
5. The chain is empty, so `resolve_chain` raises `ProviderUnavailable`
   ([`agent/providers.py:184-191`](../agent/providers.py#L184-L191)).
6. `runner.run` re-raises it ([`brahma_api/runner.py:180-183`](../brahma_api/runner.py)),
   and the server maps it to **HTTP 503 `provider_unavailable`**
   ([`brahma_api/server.py:652`](../brahma_api/server.py)).

The request never reaches the assertion about the response body; it is a provider
configuration failure, not an auth or protocol failure.

### 2.3 Why it passes locally

On this workstation two host-specific facts satisfy `auto`:

- `%LOCALAPPDATA%\BrahmaAI\config\app_settings.json` exists and sets
  `default_ai_provider = "Local"`, `provider_enabled_local = true`
  ([`llm_client.py:18`](../llm_client.py), [`llm_client.py:73-87`](../llm_client.py)).
- An Ollama-compatible server is actually listening on `127.0.0.1:11434`, so the local
  provider answers.

Locally, with the real settings present, the test passes. With `LOCALAPPDATA` pointed at a
fresh empty directory — but Ollama still running — it **also** passes, because
`local_brain.is_available()` finds the live server. That second case proves the test is
coupled to the machine, not just to the settings file.

### 2.4 Why the rest of the suite does not catch it

`tests/api_support.py` already has the right pattern: `mint()` pins `provider="stub"`
([`tests/api_support.py:43`](../tests/api_support.py)), and `_client()` uses it. Every other
API test routes through `_client()` and therefore never probes the host. This one test calls
`build_client()` directly, so it inherits `default_provider="auto"`.

The harness matrix (`ci.yml` `harness-pythons`) does not collect `tests/test_api_chat.py`, so
it stays green. Only the full `Unit suite` job sees the failure.

### 2.5 Applied fix (one line, no product code)

```diff
 def test_auth_can_be_disabled_on_purpose(tmp_path):
-    config = make_config(tmp_path, auth_required=False)
+    config = make_config(tmp_path, auth_required=False, default_provider="stub")
     client = build_client(config)
     response = client.post("/v1/chat/completions", json=BODY)
     assert response.status_code == 200
     assert response.json()["brahma"]["lane"] == "agent"
```

Why this is safe and preserves intent:

- `resolve_chain("stub")` returns a single-element chain immediately
  ([`agent/providers.py:181-182`](../agent/providers.py)); it never reads settings and never
  probes `localhost`.
- The test's actual subject is *"auth can be disabled"*, i.e. the request reaches the runner
  and returns a normal 200. The stub still exercises the whole auth → resolve → runner → loop
  → response path; only the model backend is made deterministic.
- `make_config` forwards extra keywords straight into the dataclass
  ([`tests/api_support.py:20-31`](../tests/api_support.py)), so no fixture change is needed.
- Verified on this machine against an **empty `LOCALAPPDATA`**:
  `status=200 lane=agent stub=True`.

**Applied companion test** (deterministic, no host dependency) — `tests/test_agent_providers.py`.
It pins the contract the failing test accidentally depended on, using the real helpers instead of
patching the two functions the branch is made of:

```python
def test_auto_with_nothing_reachable_is_unavailable(monkeypatch):
    import core.local_brain as local_brain

    monkeypatch.setattr(providers, "_load_app_settings", lambda: {})
    monkeypatch.setattr(local_brain.local_brain, "is_available", lambda: False)
    monkeypatch.setattr(providers, "_has_custom", lambda: False)
    monkeypatch.setattr(providers, "_has_openrouter", lambda: False)
    monkeypatch.delenv("BRAHMA_AGENT_PROVIDER", raising=False)

    with pytest.raises(providers.ProviderUnavailable):
        providers.resolve_chain("auto")
```

---

## 3. Latent packaging-workflow defects (not yet red, but wrong)

These did not produce failures in the visible history because no `v*` tag has been pushed
recently. They will fail (or produce a broken release) the first time they run.

### 3.1 `build-all-platforms.yml` advertises macOS/Linux for a Windows-only app

- `build-macos` (`macos-14`) and `build-linux` (`ubuntu-22.04`) both run
  `pip install -r requirements.txt`.
- `requirements.txt` contains Windows-only packages, including `comtypes`, `pycaw`,
  `win10toast`, and `pywinauto`. Dependency installation fails before any build starts.
- Even a successful install cannot run the app: `win32com`, `comtypes`, `pycaw`,
  `win10toast`, and `pywinauto` are imported by `installer/`, `core/`, and `actions/`.
- The `--add-data` list in both jobs omits `agent`, `subagents`, `skills`,
  `skills_library`, `memory`, `brahma_connect`, and `plugins`, so even a built binary would
  be incomplete.
- Both jobs use Python **3.12** and unpinned `pyinstaller`, diverging from the verified
  3.11 + `requirements-build.txt` toolchain.
- This duplicates `WP-11-F04` ("Fictional macOS and Linux Build Workflows"), but the
  remediation tracker does **not** carry that finding, so it will not be auto-fixed.

**Recommended outcome:** delete `build-all-platforms.yml`, or reduce it to a single
Windows job that mirrors `build.yml`. If the Windows job is kept, adopt
`requirements-build.txt`, `python -m PyInstaller`, Python 3.11, and the full `datas` set.

### 3.2 `build.yml` and `build-all-platforms.yml` both trigger on `v*`

Both workflows declare:

```yaml
on:
  push:
    tags: ['v*']
```

A single tag push therefore starts two independent PyInstaller builds of the same source,
with different interpreters, different PyInstaller pinning, and different artifact contents.
That is wasted runner time and a release-provenance ambiguity.

**Recommended outcome:** exactly one workflow owns tags. If `build.yml` is canonical, remove
`build-all-platforms.yml` (preferred) or remove its tag trigger. Add a `concurrency:` group
so a re-tag cancels the superseded run.

### 3.3 Inconsistent uninstaller packaging

- `build-all-platforms.yml` builds `installer/Uninstall.spec` and copies
  `dist/Uninstall.exe` into `dist/BrahmaEvo/`.
- `build.yml` does not build `Uninstall.spec`; it relies on `BrahmaEvo.spec` bundling
  `installer/uninstall.cmd` as a data file.

No Python code references `Uninstall.exe`, so this is not a proven runtime break — but the
two workflows ship different layouts. Decide which is intended and make both match (or keep
only one workflow).

### 3.4 Signing silently skips when the secret is absent

`build.yml` guards the signing step with `if: env.RELEASE_SIGN_KEY != ''` and sets that env
from `secrets.RELEASE_SIGN_KEY` on the same step. If the secret is unset, the step is
skipped and the release is produced unsigned; the upload uses `if-no-files-found: warn`, so
the missing `.sig` does not fail the job. This matches the workflow's own "honest limits"
comment, but it means a stable release can be published with no signature and no red X.

**Recommended outcome:** decide the policy. Either require the secret for `stable` and fail
loudly, or emit an explicit `signing: skipped` marker into `release.json`/the run summary so
the omission is visible.

### 3.5 `harness-pythons` uses a hardcoded test file list

`ci.yml` runs an explicit list of `tests/test_agent_*.py` plus 11 named security files. It is
green today, but a new portable security test will not be collected until someone edits the
workflow. Consider a pytest marker (e.g. `-m portable`) or a committed list file instead of
a 12-path command line, so the gate and the suite cannot drift.

---

## 4. Ordered fix plan (after Batch R-4)

### Phase A — make CI green without touching product code (highest value, lowest risk)

1. [x] Applied the one-line `default_provider="stub"` change in `tests/test_api_chat.py`.
2. [x] Added the `resolve_chain("auto")` unit test that pins the 503 contract explicitly
   (`tests/test_agent_providers.py::test_auto_with_nothing_reachable_is_unavailable`).
3. [ ] Re-run the full suite on 3.11 and confirm `1 failed → 0 failed`. Locally verified with the
   available 3.12: `tests/test_api_chat.py` 15 passed, `-k "api"` 120 passed, `-k "agent or
   provider"` 584 passed, `ruff check .` clean. The authoritative confirmation is the CI run after
   pushing.

Expected result: `Unit suite` goes green; no other CI job changes.

### Phase B — packaging workflow cleanup

4. Remove `build-all-platforms.yml` (or reduce it to Windows-only).
5. Make exactly one workflow own `v*` tags; add a `concurrency:` group.
6. Align `Uninstall.exe` packaging between the remaining workflow and `BrahmaEvo_Setup.spec`.
7. Decide the signing policy and make an unsigned stable release visible.
8. Run one `workflow_dispatch` build on `main` to validate the packaging path before the
   next real tag. Do not assume `build.yml` is healthy just because it has not run.

### Phase C — repo topology

9. Decide whether `SynapticSmith/Brahma-Ai-Evo` or `titechprabhasolutions/Brahma-Ai-Evo`
   is canonical.
10. If upstream is canonical, open a PR that adds `ci.yml` and `build.yml` there (upstream
    currently has no test gate).
11. Ask an upstream maintainer to approve the pending fork-PR runs, or leave them alone
    and stop treating `action_required` as a failure.

---

## 5. Verification

Run these after Phase A, from the repo root. The project standard is Python 3.11; this
investigation used the available 3.12 interpreter because `py -3.11` is not on this shell's
PATH, which does not affect the test logic.

```powershell
# 1. The exact failing test
python -m pytest tests/test_api_chat.py::test_auth_can_be_disabled_on_purpose -q

# 2. The full suite the CI job runs
$env:QT_QPA_PLATFORM = "offscreen"
$env:NO_COLOR = "1"
python -m pytest tests -q
```

To reproduce the *clean-runner* condition on a machine that has a live local model server,
point the local URL at a closed port in a temporary settings file, or simply run the test in
a fresh container/runner. The decisive condition is: **no `app_settings.json`, no provider
keys, and nothing listening on `127.0.0.1:11434`.**

CI-side confirmation after the fix:

- `CI / Unit suite` should report `0 failed` and keep the other five jobs green.
- `CI / Harness on Python 3.11/3.12/3.13` should be unchanged (they never collected this file).
- The upstream `Windows Python checks` runs will continue to show `action_required` until a
  maintainer approves them; that is expected and unrelated to this fix.

---

## 6. Overlap with the active remediation tracker

| This backlog item | Covered by `REMEDIATION-TRACKER.md`? | Action |
|---|---|---|
| `test_api_chat.py` hermeticity / red `Unit suite` | **No** | Do it in Phase A; it will not be fixed by Batch R-1 … R-4 |
| `build-all-platforms.yml` macOS/Linux fiction | **No** (only `WP-11-F04`, not carried as a FIND) | Phase B |
| Duplicate `v*` tag triggers | **No** | Phase B |
| `Uninstall.exe` / signing policy drift | **No** | Phase B |
| Upstream vs fork workflow drift | **No** | Phase C |
| `installer/*.spec` hardcoded paths, frozen subagent discovery | **Yes** — `FIND-014`, `FIND-015`, `FIND-038` | Do not duplicate; verify after Wave 6 |
| Dashboard test coverage | **Yes** — `FIND-039` | Do not duplicate |
| Documentation drift | **Yes** — `FIND-040` | Add a link to this file during that pass |

The Wave 1 items (`workspace_store` lazy init, SQLite connection lifecycle, parallel dispatch,
etc.) do **not** touch `tests/test_api_chat.py` or `brahma_api/runner.py`. Expect the fork CI
to remain red after Wave 1 until Phase A above is applied.

---

## 7. Honest limits of this investigation

- The full suite was not re-run end-to-end during this investigation (the working tree is
  mid-remediation). The failure is confirmed from the CI log and reproduced at the single-test
  level; the other 1,521 results are taken from the runner's report.
- `build.yml` and `build-all-platforms.yml` have no recent runs in the visible history, so the
  packaging defects are static analysis, not observed build failures. Treat them as
  high-confidence, not proven-until-run.
- The macOS/Linux failure is inferred from Windows-only dependencies and imports. It was not
  executed on those runners.
- Upstream approval cannot be performed from this account (`viewerPermission: READ`); a
  maintainer action is required.
- The local `.brahma/mcp.json` credential warning from `scripts/precommit_check.py --quick` is
  a **local, uncommitted, gitignored** file. The CI `static-gate` job passed on every recent
  run, so it is not part of this failure and should not be "fixed" by editing CI.
