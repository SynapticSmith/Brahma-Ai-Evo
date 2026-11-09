# Rebrand Execution Plan — Brahma → Abhasa

| Field | Value |
|---|---|
| **Status** | Execution plan / handoff document. Not an ADR. |
| **Input** | [`docs/REBRAND-PROPOSITION.md`](REBRAND-PROPOSITION.md) (2026-11-06) |
| **Prepared** | 2026-11-09 |
| **Target** | Assistant `Abhasa`, product `Abhasa Evo` |
| **Audience** | The agent or engineer who will execute the rename |
| **Companion ADR** | `docs/ADR-029-REBRAND-TO-ABHASA.md` (must be written first). Numbered 029 because ADR-022 is the OpenAI-compatible port (`ARCHITECTURE-SPEC.md` §11.2) and ADR-023-028 are claimed by `docs/SWARM-FEDERATION-INVESTIGATION.md` §13. |

> This plan is deliberately more conservative than the proposition. The
> proposition says what the name should be. This document says how to move a
> live, packaged, multi-surface product onto it without breaking installs,
> stored secrets, paired phones, external API clients, or the test suite.

> **ADR numbering note.** `REBRAND-PROPOSITION.md` D4 still names
> `ADR-022-REBRAND-TO-ABHASA.md`. That number is stale: ADR-022 is the
> OpenAI-compatible port (`ARCHITECTURE-SPEC.md` §11.2) and ADR-023-028 are
> claimed by `docs/SWARM-FEDERATION-INVESTIGATION.md` §13. The proposition is an
> input document and is left as written; this plan supersedes the filename with
> **ADR-029**. Every reference in this plan already uses 029.

---

## 0. How to use this document

1. Read §1–§5 before touching code. The decision gates in §4 are blocking.
2. Execute phases in order. Each phase has a **Definition of Done** and a
   **Rollback** note.
3. Do not rename everything in one commit. The sequence in §11 is the intended
   PR breakdown.
4. Run the gates in §10 after every phase, not only at the end.
5. When a task is ambiguous, prefer **dual-read + deprecation warning** over a
   hard cut. This is a one-release compatibility window, not a flag day.

---

## 1. Executive summary

The proposition recommends:

- Assistant name: **Abhasa**
- Product name: **Abhasa Evo**
- Fallbacks: **Pratibimba**, then **Dipa**
- One dual-read release, then cleanup
- `TRADEMARK.md` and `LICENSE` amended **before** the name ships

A verified scan of the current tree (2026-11-09) finds:

| Metric | Count |
|---|---|
| Tracked files containing `brahma` (case-insensitive) | **445** |
| Total matches | **4,403** |
| Test files containing `brahma` | **70** |
| Live code directories with brand strings | `core/`, `actions/`, `agent/`, `main.py`, `ui.py`, `brahma_api/`, `brahma_connect/`, `dashboard/`, `installer/`, `features/`, `memory/` |
| Docs with brand strings | 48 files (12 scan reports + ~36 live/historical records) |
| Actual `BRAHMA_*` environment variables read by code | **24** |
| Android files with brand strings | 24 |
| Installer/build/CI files with brand strings | 10 installer files + `build_all.ps1` + 3 workflows |

**The hard parts are not the strings.** They are:

1. **Legal ordering** — the current trademark notice forbids the rename until it
   is amended (§4, gate G1).
2. **Stored state** — `%LOCALAPPDATA%\BrahmaAI`, `config/brahma_api/tokens.json`,
   DPAPI-encrypted secrets, dashboard device tokens, TLS certs, and the
   `~/.brahma/sessions.db` CLI database must survive.
3. **External contracts** — the OpenAI-compatible API model IDs and routes, the
   `X-Brahma-*` headers, the `_BRAHMA._tcp.local.` mDNS service, and the Android
   `applicationId`.
4. **Packaging identity** — exe name, installer name, registry key, shortcuts,
   signing key env vars, CI artifact names.
5. **The test suite** — 70 files assert brand strings, paths, model IDs, and
   package names.

**Recommended shape:** introduce one stdlib-only branding seam
(`core/branding.py`), make every surface read from it, dual-read legacy values
for one release, and rename packages/repos/Android IDs last.

---

## 2. Current-state inventory (verified)

### 2.1 Code and assets

| Surface | Current value | Files (approx.) | Notes |
|---|---|---|---|
| Assistant default name | `Brahma` | `core/identity.py` | `IdentityService` defaults + getters |
| Application default name | `Brahma Evo` | `core/identity.py`, `ui.py` | Window title, tray, display name |
| System prompt fallbacks | `You are Brahma Evo…` | `main.py:251,354,380`, `core/prompt.txt:1` | Three fallbacks + resident prompt |
| Agent base prompt | `You are Brahma…` | `agent/prompts.py:141` | Headless harness |
| Wake words | `brahma evo`, `hey brahma evo`, `hi/hello brahma evo`; standby `brahma` | `main.py:652-681` | Also bare `hey/hi/hello` in-session |
| GUI classes | `BrahmaUI`, `BrahmaLive` | 29 + 43 files | Internal; may lag |
| UI component classes | `BrahmaTelemetryWing`, `BrahmaResultWing`, `BrahmaConnectDevicesPage` | `ui.py`, `core/ui_*.py` | Internal |
| Logo assets | `assets/Brahma_Lite_Logo.{png,ico}`, `assets/brahma_evo_logo.{png,ico}` | 13 references | Needs a new mark |
| User data root | `%LOCALAPPDATA%\BrahmaAI` | 76 files | Central seam is `core/user_paths.get_user_data_dir()` |
| Startup log root | `%LOCALAPPDATA%\Brahma Evo\startup.log` | `main.py:82` | Second root |
| Install dir | `%LOCALAPPDATA%\Brahma_Evo` | `installer/install_wizard.py:210` | Third root |
| CLI sessions | `~/.brahma/sessions.db` (fallback) | `agent/session.py:42` | Primary is `workspace_store` under user data |
| Project instructions | `BRAHMA.md`, `.brahma/AGENTS.md` | `agent/prompts.py:37` | Discovery order |
| Project skills | `.brahma/skills`, `%LOCALAPPDATA%\BrahmaAI\skills`, `~/.config/brahma/skills` | `agent/skills.py` | Three roots |
| MCP config | `BRAHMA_MCP_CONFIG`, `.brahma/mcp.json`, `.mcp.json` | `agent/mcp.py:242-257` | Discovery order |
| Env vars | 24 `BRAHMA_*` names | see §2.5 | All need aliases |
| Python packages | `brahma_api`, `brahma_connect` | 35 + 51 files | Import-path churn |
| API model IDs | `brahma-evo`, `:read`, `:plan`, `:passthrough` | `brahma_api/models.py:39` | External contract |
| API routes | `/v1/brahma/whoami`, `/sessions`, `/runs/{id}` | `brahma_api/server.py` | External contract |
| API headers | `X-Brahma-Approval`, `X-Brahma-Session` | `brahma_api/server.py` | External contract |
| API response keys | `"brahma": {...}`, `system_fingerprint: "brahma-evo/…"` | `brahma_api/translate.py` | External contract |
| Dashboard | `brahma_token`, `brahma_key`, `brahma_device_token`; `brahma.key/.crt`; `BRAHMA-DASHBOARD-v1` salt; cert CN | `dashboard/server.py` | Stored device tokens + TLS |
| Gateway | `_BRAHMA._tcp.local.`, `config/brahma_connect.json`, `config/brahma_connect/devices.json` | `brahma_connect/gateway/server.py` | Pairing compatibility |
| Android | package/namespace `com.brahma.connect`, `applicationId` `com.brahma.connect`, `BrahmaConnectApplication`, `BrahmaConnectForegroundService`, `BrahmaAccessibilityService`, `BrahmaWebSocketClient`, `BrahmaGatewayDiscovery`, `BrahmaConnectApp`, `ic_brahma_launcher`, `Theme.BrahmaConnect`, `brahma_accessibility_service.xml`, `Brahma Evo Connect` strings | 24 files | Store listing + pairing |
| Installer | `BrahmaEvo.spec`, `BrahmaEvo_Setup.spec`, `BrahmaEcho*.spec`, `BrahmaEvo.exe`, `BrahmaEvo_Setup.exe`, registry `…\Uninstall\BrahmaEvo`, `Brahma Evo.lnk` | 10 files | Also `install.sh`, `uninstall.sh`, `uninstall.cmd` |
| Build/CI | `build_all.ps1`, `bootstrap.ps1`, `start_brahma.bat`, `EVO.bat`, 3 workflows | 7 files | Artifact names, `BRAHMA_RELEASE_SIGN_KEY` |
| Release tools | `BrahmaEvo` product in `build_release.py`, `rotate_release_key.py`, `sign_release.py`, `generate_sbom.py` | 4 files | Signed manifest identity |
| Repo/community | `SynapticSmith/Brahma-Ai-Evo` (origin), `titechprabhasolutions/Brahma-Ai-Evo` (upstream), `security@brahma.ai`, `https://brahma.ai`, Discord invite | multiple | Outside-repo decisions |

### 2.2 Match counts by directory

| Directory | Files | Matches |
|---|---:|---:|
| `docs/` | 48 | 2,043 |
| `core/` | 111 | 620 |
| `tests/` | 70 | 418 |
| `actions/` | 53 | 217 |
| `brahma-connect-android/` | 24 | 189 |
| `installer/` | 10 | 134 |
| `main.py` | 1 | 117 |
| `brahma_api/` | 8 | 99 |
| `agent/` | 15 | 69 |
| `brahma_connect/` | 9 | 60 |
| `dashboard/` | 4 | 45 |
| `assets/` | 8 | 43 |
| `features/` | 22 | 33 |
| `.github/` | 2 | 26 |
| `README.md` | 1 | 24 |
| `memory/` | 6 | 20 |
| root scripts/config/tools | ~20 | ~60 |

### 2.3 Exact-match counts worth planning around

| Token | Files | Matches |
|---|---:|---:|
| `Brahma Evo` | 113 | 424 |
| `BRAHMA_*` (any token) | 166 | 868 |
| `.brahma` | 90 | 334 |
| `brahma_api` | 35 | 264 |
| `brahma_connect` | 51 | 222 |
| `BrahmaEvo` | 41 | 187 |
| `BrahmaAI` | 76 | 162 |
| `brahma-evo` | 21 | 127 |
| `BrahmaLive` | 43 | 124 |
| `BrahmaUI` | 29 | 99 |
| `com.brahma` | 19 | 86 |
| `Brahma_Lite_Logo` | 13 | 24 |
| `X-Brahma` | 7 | 24 |
| `/v1/brahma` | 7 | 21 |

### 2.4 Environment variables actually read by code

```
BRAHMA_AGENT_ARTIFACTS_DIR
BRAHMA_AGENT_INSTRUCTION_PARENTS
BRAHMA_AGENT_PERMISSIONS
BRAHMA_AGENT_PROVIDER
BRAHMA_AGENT_TOKEN_<ID>          (dynamic suffix)
BRAHMA_AGENT_TOKEN_REMOTE_SECURE
BRAHMA_AGENT_WORKSPACE
BRAHMA_DASHBOARD_HOST
BRAHMA_DEV_ALLOW_UPDATE
BRAHMA_DPAPI                     (binary data prefix — see §7.4)
BRAHMA_DPI_AWARENESS
BRAHMA_GATEWAY_PORT
BRAHMA_MCP_CONFIG
BRAHMA_NO_RELAUNCH
BRAHMA_PRINT_PY
BRAHMA_RELEASE_PUBLIC_KEY
BRAHMA_RELEASE_SIGN_KEY
BRAHMA_REMOTE_AGENTS
BRAHMA_SEARCH_BACKEND
BRAHMA_SKILLS_DIR
BRAHMA_SUBAGENT_LIBRARY
BRAHMA_TARGET_TITLE
BRAHMA_TEST_API_KEY
BRAHMA_UPDATE_POLICY
BRAHMA_WEB_ALLOW_PRIVATE
```

Plus internal/test-only: `BRAHMA_CANARY_` (taint canary prefix),
`BRAHMA_SANDBOX_PROBE_OK`, `BRAHMA_OUTSIDE_WRITTEN`, `BRAHMA_NET_CONNECTED`,
`BRAHMA_UID_OK`, `BRAHMA_CODE_BLOCK_*`, `BRAHMA_INLINE_CODE_*`.

### 2.5 Live docs vs historical records

**Update (live product/developer docs):**

- `README.md`
- `AGENTS.md` (and any `BRAHMA.md`)
- `CONTRIBUTING.md`, `SECURITY.md`, `PRIVACY_POLICY.md`
- `docs/ARCHITECTURE-SPEC.md`
- `docs/USER-GUIDE.md`
- `docs/AGENTIC-HARNESS.md`, `docs/AGENTIC-HARNESS-STATUS.md`,
  `docs/AGENTIC-HARNESS-NEXT.md`
- `docs/OPENAI-COMPATIBLE-API-DESIGN.md`
- `docs/SECURITY-ARCHITECTURE-ROADMAP.md` (only the live headers/links, not the
  historical analysis body)
- `docs/REMEDIATION-TRACKER.md`, `docs/ROUTING-AUDIT.md`,
  `docs/ROUTING-IMPLEMENTATION.md` (only if they are still active; otherwise
  mark as historical)

**Do not rewrite (audit trail):**

- `docs/scan-reports/WP-01..WP-12` — generated analysis reports.
- `docs/VERSION-1.0.1-*.md`, `docs/PRODUCTION-RELEASE-REPORT.md`,
  `docs/SYSTEM-ANALYSIS-MASTER-REPORT.md`,
  `docs/FULL-SYSTEM-SCAN-AND-ANALYSIS-PLAN.md`, `docs/CODE-REVIEW-TODO.md`,
  `docs/GITHUB-ACTIONS-REMEDIATION.md`, `docs/UPSTREAM-INTEGRATION-PLAN.md`,
  `docs/SECURITY-ANALYSIS-ISSUE-4.md`, `docs/AGENT-BRIDGE-*`,
  `docs/agent-orchestrator-bridge.md`, `docs/ADR-018..021`.
- Add a one-line banner to each: `> Historical record: product name changed to
  Abhasa in 2026; "Brahma" below is the name in use at the time of writing.`
  Do not change the body.

---

## 3. Recommended architecture: one branding seam

Create **`core/branding.py`** (stdlib only, no PyQt/provider/COM imports) as the
single source of truth. Everything else reads from it. This prevents a
4,403-match shotgun rename from becoming 4,403 places to get wrong.

Suggested contents:

```python
"""Single source of truth for product naming and legacy compatibility."""

BRAND = "Abhasa"
PRODUCT = "Abhasa Evo"
BRAND_SLUG = "abhasa"           # lower-case, hyphen-free
BRAND_LEGACY = ("Brahma", "Brahma Evo", "Brahma AI", "BrahmaAI")
ENV_PREFIX = "ABHASA"
ENV_LEGACY_PREFIX = "BRAHMA"
WAKE_WORDS = ("abhasa", "abhasa evo", "hey abhasa", "hi abhasa", "hello abhasa")
WAKE_WORDS_LEGACY = ("brahma", "brahma evo", "hey brahma evo", "hi brahma evo", "hello brahma evo")
PROJECT_INSTRUCTION_FILES = ("AGENTS.md", "ABHASA.md", ".abhasa/AGENTS.md",
                             "BRAHMA.md", ".brahma/AGENTS.md")
PROJECT_SKILL_DIRS = (".abhasa/skills", ".brahma/skills")

def env(name: str, default=None):
    """Read ABHASA_<name>, then BRAHMA_<name>, then default."""

def data_dir_name() -> str: ...
def legacy_data_dir_names() -> tuple[str, ...]: ...
```

Rules for the seam:

- **Never** import `core.branding` from `agent/` at module scope if it grows a
  dependency. Keep it stdlib-only so the harness CI (`3.11/3.12/3.13`, pytest
  only) keeps importing.
- `core/user_paths.get_user_data_dir()` becomes the only data-root resolver and
  calls `core.branding`.
- Every legacy read is explicit and logged once:
  `print("[Abhasa] Using legacy BRAHMA_* value; set ABHASA_* instead.")`.
- Add `scripts/rebrand_audit.py` (see §9) to track remaining legacy tokens.

---

## 4. Decision gates (blocking)

These must be decided and recorded in `ADR-029` before code changes.

| Gate | Decision | Recommended | Why it blocks |
|---|---|---|---|
| **G1** | Amend `TRADEMARK.md` and `LICENSE` before shipping | Yes, blocking | Current notice forbids rebranding until amended (§8.1 of proposition) |
| **G2** | Assistant name vs product name | `Abhasa` / `Abhasa Evo` | Drives every string |
| **G3** | Fallback order | `Pratibimba`, then `Dipa` | Only needed if clearance fails |
| **G4** | Trademark/domain clearance for `Abhasa` | Commission before public ship | The name has a pejorative "semblance/illusion" sense; clearance is not asserted |
| **G5** | Compatibility window length | One minor release, then cleanup | Drives aliases and deprecation warnings |
| **G6** | Rename internal Python classes `BrahmaUI`/`BrahmaLive`? | **No** in this release; defer | 72 files + tests, no user value, high churn |
| **G7** | Rename Python packages `brahma_api`/`brahma_connect`? | **Alias now, rename later** | Import churn; external API consumers |
| **G8** | Rename Android `applicationId`? | **Keep `com.brahma.connect` for one release**; ship `com.abhasa.connect` as a new app only if the store listing can migrate | Changing `applicationId` makes it a different app: reinstall, lost pairing, new listing |
| **G9** | Repo rename timing | Rename `origin`/GitHub after the first dual-read release; keep redirects | Breaks bookmarks, forks, CI badges |
| **G10** | Keep `BRAHMA_DPAPI` binary prefix? | **Keep forever** | It is a stored-secret format marker; changing it makes old secrets unreadable |
| **G11** | What counts as "historical docs" | §2.5 list | Avoids rewriting the audit trail |

---

## 5. Compatibility matrix

| Surface | New | Legacy (accepted for one release) | Migration |
|---|---|---|---|
| Assistant name | `Abhasa` | `Brahma` | `config/identity.json` user override wins; default changes only |
| Product name | `Abhasa Evo` | `Brahma Evo` | Strings only |
| Wake words | `abhasa`, `abhasa evo`, `hey/hi/hello abhasa` | `brahma`, `brahma evo`, `hey/hi/hello brahma evo` | Add new, keep old; deprecation log once |
| User data | `%LOCALAPPDATA%\Abhasa` | `%LOCALAPPDATA%\BrahmaAI`, `%LOCALAPPDATA%\Brahma Evo` | Copy + verify; never delete in the same release |
| Install dir | `%LOCALAPPDATA%\Abhasa_Evo` | `%LOCALAPPDATA%\Brahma_Evo` | New installer installs new; uninstaller removes both |
| CLI sessions | `~/.abhasa/sessions.db` | `~/.brahma/sessions.db` | Copy if new missing |
| Project instructions | `ABHASA.md`, `.abhasa/AGENTS.md` | `BRAHMA.md`, `.brahma/AGENTS.md` | Read both; prefer new |
| Project skills | `.abhasa/skills` | `.brahma/skills` | Read both; prefer new |
| MCP config | `.abhasa/mcp.json` | `.brahma/mcp.json` | Read both; prefer new |
| Env vars | `ABHASA_*` | `BRAHMA_*` | `env()` helper: new first, old fallback, warn once |
| API model IDs | `abhasa-evo[:read\|:plan\|:passthrough]` | `brahma-evo[:…]` | Accept both; list both in `/v1/models` during window |
| API routes | `/v1/abhasa/*` | `/v1/brahma/*` | Mount both |
| API headers | `X-Abhasa-Approval`, `X-Abhasa-Session` | `X-Brahma-*` | Accept both; emit new |
| API response | `"abhasa"` object, `system_fingerprint: "abhasa-evo/…"` | `"brahma"` object, old fingerprint | Emit both during window |
| mDNS | `_ABHASA._tcp.local.` | `_BRAHMA._tcp.local.` | Advertise both; discover both |
| Dashboard | `abhasa_token`, `abhasa.key/.crt`, new salt | `brahma_*` | Dual-read; keep old cert if present |
| Android package | `com.abhasa.connect` (deferred) | `com.brahma.connect` | See G8 |
| Exe/installer | `AbhasaEvo.exe`, `AbhasaEvo_Setup.exe` | `BrahmaEvo.exe` | New build; uninstaller handles old |
| Registry | `…\Uninstall\AbhasaEvo` | `…\Uninstall\BrahmaEvo` | Write new; clean old on uninstall |
| Repo | `…/Abhasa-Ai-Evo` | `…/Brahma-Ai-Evo` | GitHub redirect; update remotes/docs |

---

## 6. Phase 0 — Legal and governance (blocking)

**Goal:** make the rename legal and recorded before any user-visible change.

- [ ] **P0-1** Write `docs/ADR-029-REBRAND-TO-ABHASA.md` with:
  - decision (D1–D7 from the proposition),
  - the naming test result,
  - the compatibility window,
  - the migration sequence,
  - the trademark/license ordering,
  - the `applicationId` decision (G8).
- [ ] **P0-2** Amend `TRADEMARK.md` **first**:
  - claim `Abhasa`, `Abhasa Evo`, the new logo,
  - describe `Brahma`, `Brahma AI`, `Brahma AI - Lite` as superseded,
  - keep the "no rebranding by third parties" clause for the new marks.
- [ ] **P0-3** Amend `LICENSE`:
  - title → `ABHASA EVO SOURCE-AVAILABLE PERSONAL USE LICENSE`,
  - add `formerly Brahma AI Evo` so the chain is unbroken,
  - update the official repository URL,
  - keep the same terms (no distribution, no commercial use).
- [ ] **P0-4** Commission trademark/domain clearance for `Abhasa` (D7). Record
  the result in the ADR. Do not announce publicly until this returns.
- [ ] **P0-5** Decide and record G6–G9 in the ADR.

**Definition of Done:** ADR merged; `TRADEMARK.md` and `LICENSE` name the new
marks and explicitly supersede the old ones; no product surface has changed yet.

**Rollback:** revert the ADR/legal commit; no runtime impact.

---

## 7. Phase 1 — Branding seam and audit tool

**Goal:** one place to change, one way to measure progress.

- [ ] **P1-1** Add `core/branding.py` with the constants and `env()` helper
  described in §3. Keep it stdlib-only.
- [ ] **P1-2** Refactor `core/user_paths.get_user_data_dir()` to:
  - return `%LOCALAPPDATA%\Abhasa` (or `~/Abhasa` fallback),
  - create it,
  - if it is empty and a legacy root exists, copy `BrahmaAI` and `Brahma Evo`
    into it (`shutil.copytree(..., dirs_exist_ok=True)`),
  - write a `.migrated-from-brahma` marker,
  - **never delete** the legacy root.
- [ ] **P1-3** Add `scripts/rebrand_audit.py`:
  - scans tracked files for `brahma` case-insensitive,
  - groups by directory and by token (`Brahma Evo`, `BRAHMA_*`, `.brahma`,
    `brahma_api`, `brahma_connect`, `com.brahma`, …),
  - allowlists historical docs from §2.5,
  - exits non-zero on new legacy tokens in live surfaces,
  - supports `--report` (table) and `--json`.
- [ ] **P1-4** Wire `scripts/rebrand_audit.py --report` into
  `scripts/precommit_check.py` as a **warning** first; make it blocking in the
  cleanup release.
- [ ] **P1-5** Add a `tests/test_branding.py` that pins:
  - `BRAND == "Abhasa"`,
  - `env()` prefers `ABHASA_*` and falls back to `BRAHMA_*`,
  - `get_user_data_dir()` creates the new root and does not delete the old one,
  - wake-word sets contain both new and legacy.

**Definition of Done:** seam exists; data root resolves to the new directory;
migration is idempotent; audit tool reports the current 4,403 matches as a
baseline.

**Rollback:** the seam is additive; reverting the commit restores the old root.

---

## 8. Phase 2 — Identity, prompts, and user-facing copy

**Goal:** the product says "Abhasa" everywhere a user reads or hears it. No
paths, packages, or binaries change yet.

### 8.1 Identity defaults

- [ ] **P2-1** `core/identity.py`:
  - default `assistant.name` → `Abhasa`,
  - default `assistant.application_name` → `Abhasa Evo`,
  - getter fallbacks (`get_assistant_name`, `get_application_name`) → new names.
- [ ] **P2-2** `config/identity.json` is user data and ignored. Do not edit it.
  Existing users keep their override. Add a one-time notice only if the stored
  name is still the legacy default.

### 8.2 Prompts

- [ ] **P2-3** `main.py` three fallbacks:
  - line ~251 `You are Brahma Evo…` → `You are Abhasa…`,
  - line ~354 `You are Brahma Evo…` → `You are Abhasa…`,
  - line ~380 `You are Brahma Evo…` → `You are Abhasa…`,
  - line ~259 `or "Brahma Evo"` → `or "Abhasa"`.
- [ ] **P2-4** `core/prompt.txt` line 1 and the two `.brahma/skills` mentions
  (lines 43, 88) → `Abhasa` / `.abhasa/skills` (dual-read note).
- [ ] **P2-5** `agent/prompts.py` `BASE_SYSTEM` line 141 → `You are Abhasa…`.
- [ ] **P2-6** `agent/__main__.py` argparse description and MCP client name
  (`brahma-agent` → `abhasa-agent`).

### 8.3 UI copy

- [ ] **P2-7** `ui.py`: window title, `setApplicationDisplayName`, tray tooltip,
  logo constants (see Phase 9 for asset files).
- [ ] **P2-8** `core/ui_apply_state.py`: all status/placeholder/footer strings.
- [ ] **P2-9** `core/ui_chat.py`: bubble author, plan label, placeholder,
  "Try asking…", footer.
- [ ] **P2-10** `core/ui_commandbar.py`, `core/ui_connectivity.py`,
  `core/ui_voice_library.py`, `core/ui_devices.py`, `core/ui_telemetry.py`:
  all user-visible `Brahma`/`Brahma Evo` strings.
- [ ] **P2-11** `core/desktop_tools.py`: tool description strings and log
  prefixes.
- [ ] **P2-12** `main.py` `TOOL_DECLARATIONS`: every description that says
  "Brahma" or "Brahma Evo" (≈25 sites).
- [ ] **P2-13** `core/audio_io.py`, `core/attention_flow.py`,
  `core/device_flow.py`, `core/desktop_host.py`: log prefixes and spoken text.
- [ ] **P2-14** `discord_bot.py`: assistant name, command group name, system
  prompt, log messages.
- [ ] **P2-15** `dashboard/server.py`: page copy, "Connecting to Brahma…",
  "Press Mobile Connect in Brahma".
- [ ] **P2-16** `brahma_connect/gateway/mobile_portal.py`: title, headers,
  notification text.
- [ ] **P2-17** `installer/install_wizard.py`, `installer/uninstall_wizard.py`,
  `installer/install.sh`, `installer/uninstall.sh`, `installer/uninstall.cmd`:
  all user-visible copy (keep binary names for Phase 5).
- [ ] **P2-18** `actions/call_assistant.py`, `actions/calorie_counter.py`,
  `actions/auto_heal_engine.py`, `actions/brahma_dev_agent.py`,
  `features/*` synthesized-skill strings: user-visible copy only.
- [ ] **P2-19** `core/execution_reflection.py`, `core/boot_sentry.py`,
  `core/clipboard_sentry.py`, `core/anticipatory_watcher.py`: docstrings/logs
  (cosmetic; low priority).

**Definition of Done:** a fresh install with no `identity.json` shows `Abhasa`
in the window title, tray, prompts, chat bubbles, logs, Discord, dashboard, and
mobile portal. No path or binary changed.

**Rollback:** revert the copy commit.

---

## 9. Phase 3 — Wake words

**Goal:** "Hey Abhasa" works; "Hey Brahma Evo" still works for one release.

- [ ] **P3-1** `main.py:_wakeword_detected`:
  - add `abhasa`, `abhasa evo`, `hey/hi/hello abhasa`,
  - keep the `brahma evo` set,
  - keep the bare `hey/hi/hello` in-session behaviour.
- [ ] **P3-2** `main.py:_standby_wakeword_detected`:
  - accept `abhasa` and `brahma`,
  - update the docstring.
- [ ] **P3-3** Emit one deprecation log when a legacy wake phrase is matched:
  `SYS: "Brahma Evo" is deprecated; say "Abhasa" instead.`
  Do not repeat every turn.
- [ ] **P3-4** Update `accept_wakeword.py` and `accept_local_speech.py`
  fixtures to test both sets.
- [ ] **P3-5** Update `tests/test_ai_evolution.py` ("Hey Brahma…" fixture) and
  any other wake-word test.

**Definition of Done:** both wake-word sets pass the acceptance harness; the
standby path opens a session on `abhasa` and still on `brahma`.

**Rollback:** revert the wake-word commit.

---

## 10. Phase 4 — Data roots, project files, and migration

**Goal:** new installs write to `Abhasa` paths; old installs keep working; no
data is lost.

### 10.1 Central root

- [ ] **P4-1** `core/user_paths.py` migration (P1-2) is the only place that
  decides the root. Verify every consumer uses it.
- [ ] **P4-2** Replace the 75 hardcoded `"BrahmaAI"` occurrences in code with
  `get_user_data_dir()` or `branding` helpers. Exceptions:
  - User-Agent strings (`BrahmaAI-HoloGlobe/1.0`, etc.) — cosmetic, can be
    changed in the same sweep but are not paths.
  - `BRAHMA_DPAPI` — see §7.4.
- [ ] **P4-3** `main.py:82` startup log → `Abhasa Evo/startup.log` (or better,
  `get_user_data_dir()/"startup.log"`).
- [ ] **P4-4** `installer/install_wizard.py` install dir →
  `%LOCALAPPDATA%\Abhasa_Evo`; `uninstall_wizard.py` removes both new and old.
- [ ] **P4-5** `installer/install.sh` / `uninstall.sh`:
  - `~/.abhasa-evo`,
  - `~/Applications/Abhasa Evo.app`,
  - `~/.local/bin/abhasa`,
  - `abhasa-evo.desktop`,
  - keep old paths in the uninstaller.
- [ ] **P4-6** `dashboard/server.py` upload dirs:
  `~/Downloads/Abhasa Uploads`, `~/Documents/Abhasa Uploads`; accept old dirs
  when present.
- [ ] **P4-7** Desktop deliverables `~/Desktop/BrahmaAI` → `~/Desktop/Abhasa`
  in `actions/office_builder.py`, `browser_control.py`,
  `google_workspace_mcp.py`, and the `features/*` skill templates.
- [ ] **P4-8** Add a one-time migration command/tool:
  `python -m agent migrate-brand` or `scripts/migrate_to_abhasa.py`:
  - copy `%LOCALAPPDATA%\BrahmaAI` → `%LOCALAPPDATA%\Abhasa`,
  - copy `%LOCALAPPDATA%\Brahma Evo` → `%LOCALAPPDATA%\Abhasa Evo` (or merge),
  - copy `~/.brahma/sessions.db` → `~/.abhasa/sessions.db`,
  - copy `config/brahma_api.json` → `config/abhasa_api.json`,
  - copy `config/brahma_connect.json` → `config/abhasa_connect.json`,
  - copy `.brahma/mcp.json` → `.abhasa/mcp.json` in the workspace,
  - verify counts/sizes, write a manifest, never delete the source.

### 10.2 Project instructions and skills

- [ ] **P4-9** `agent/prompts.py` `PROJECT_FILES` →
  `("AGENTS.md", "ABHASA.md", ".abhasa/AGENTS.md", "BRAHMA.md", ".brahma/AGENTS.md")`
  with new files preferred.
- [ ] **P4-10** `agent/skills.py`:
  - `project_roots()` reads `.abhasa/skills` then `.brahma/skills`,
  - `user_skills_root()` → `%LOCALAPPDATA%\Abhasa\skills`, fallback
    `~/.config/abhasa/skills`, then legacy,
  - `BRAHMA_SKILLS_DIR` alias via `branding.env("SKILLS_DIR")`.
- [ ] **P4-11** `agent/mcp.py` `discover_config()` order:
  `ABHASA_MCP_CONFIG`/`BRAHMA_MCP_CONFIG`, `.abhasa/mcp.json`,
  `.brahma/mcp.json`, `.mcp.json`.
- [ ] **P4-12** `agent/__main__.py` help text and error strings.
- [ ] **P4-13** `git mv .brahma .abhasa` for the tracked project skills, then
  keep a compatibility read of `.brahma/skills`. Update
  `installer/BrahmaEvo.spec` datas to bundle both (or the new one plus a
  fallback).
- [ ] **P4-14** Add `ABHASA.md` at the repo root (copy of `BRAHMA.md` or
  `AGENTS.md` as appropriate) and keep `BRAHMA.md` for one release.

**Definition of Done:** a new run creates only `Abhasa` paths; an old run still
finds its data; the migration tool is idempotent and verifies before reporting
success; the harness still discovers `AGENTS.md` first and prefers
`ABHASA.md` over `BRAHMA.md`.

**Rollback:** the migration copies, never moves; reverting the code restores
the old root.

---

## 11. Phase 5 — Environment variables

**Goal:** `ABHASA_*` wins; `BRAHMA_*` still works; secrets stay readable.

- [ ] **P5-1** Add `branding.env(name, default)`:
  - read `ABHASA_<name>`,
  - then `BRAHMA_<name>`,
  - warn once per process when the legacy name is used,
  - never read both silently.
- [ ] **P5-2** Convert the 24 env reads listed in §2.4. Keep the old names in
  help text and docs for one release.
- [ ] **P5-3** `EVO.bat` `BRAHMA_PRINT_PY` → accept `ABHASA_PRINT_PY` first.
- [ ] **P5-4** `build_all.ps1` `BRAHMA_RELEASE_SIGN_KEY` →
  `ABHASA_RELEASE_SIGN_KEY` first; keep the old name as fallback.
- [ ] **P5-5** `tools/build_release.py`, `sign_release.py`,
  `rotate_release_key.py`, `scripts/generate_sbom.py`: accept both env names,
  emit new product name.
- [ ] **P5-6** **Do not rename `BRAHMA_DPAPI`** in
  `core/secret_store.py:32` and `core/redaction.py:27`. It is a binary envelope
  prefix. If a cosmetic rename is required, write a migration that rewrites the
  prefix on read and supports both.
- [ ] **P5-7** `BRAHMA_CANARY_` in `core/trust.py` is a test/taint prefix. It
  can stay or be renamed with the tests; no compatibility need.
- [ ] **P5-8** Update `AGENTS.md`, `docs/AGENTIC-HARNESS.md`, and
  `docs/USER-GUIDE.md` env-var tables to show `ABHASA_*` first, `BRAHMA_*` as
  deprecated.

**Definition of Done:** every env var works under both names; a legacy-only
environment still boots; a new-only environment does not read legacy values.

**Rollback:** `branding.env` is additive; reverting the call sites restores the
old reads.

---

## 12. Phase 6 — Packaging, binaries, and OS surfaces

**Goal:** the installer ships `AbhasaEvo`, while the uninstaller can still
remove `BrahmaEvo`.

### 12.1 Version metadata

- [ ] **P6-1** `version.txt`:
  - `FileDescription` → `Abhasa Evo AI Assistant`,
  - `InternalName` → `AbhasaEvo`,
  - `OriginalFilename` → `AbhasaEvo.exe`,
  - `ProductName` → `Abhasa Evo`.
- [ ] **P6-2** `version_setup.txt`: same for the installer.
- [ ] **P6-3** `installer/BrahmaEvo.spec` → `installer/AbhasaEvo.spec`:
  - `name='AbhasaEvo'` in both `EXE` and `COLLECT`,
  - datas: `abhasa_api`, `abhasa_connect`, `.abhasa/skills` (plus legacy
    fallbacks if needed),
  - hiddenimports: new package names,
  - icon: new asset.
- [ ] **P6-4** `installer/BrahmaEvo_Setup.spec` → `installer/AbhasaEvo_Setup.spec`:
  - `source_dir` → `dist/AbhasaEvo`,
  - `name='AbhasaEvo_Setup'`.
- [ ] **P6-5** `installer/Uninstall.spec`: icon and name.
- [ ] **P6-6** `installer/BrahmaEcho*.spec`: these are stale/duplicate specs
  with hardcoded `D:\…` paths. Either delete them (preferred, they are not in
  `build_all.ps1`) or rename them consistently. Do not leave a second product
  name in the tree.
- [ ] **P6-7** `build_all.ps1`: spec paths, artifact paths, mock artifact
  strings, release key env, dry-run banner.
- [ ] **P6-8** `.github/workflows/build.yml`,
  `build-all-platforms.yml`, `ci.yml`: artifact names, spec paths, SBOM
  verification, `--name "AbhasaEvo"`, release.json artifact path.
- [ ] **P6-9** `tools/build_release.py`, `tools/rotate_release_key.py`,
  `tools/sign_release.py`: `"product": "AbhasaEvo"`; keep the old product
  accepted when verifying old manifests.

### 12.2 Installer / uninstaller behaviour

- [ ] **P6-10** `installer/install_wizard.py`:
  - title/labels/copy,
  - `BrahmaEvo.exe` → `AbhasaEvo.exe`,
  - `Brahma Evo.lnk` → `Abhasa Evo.lnk`,
  - install dir `Abhasa_Evo`,
  - registry key `…\Uninstall\AbhasaEvo`,
  - icon path.
- [ ] **P6-11** `installer/uninstall_wizard.py`:
  - detect and remove **both** `AbhasaEvo.exe` and `BrahmaEvo.exe`,
  - remove both shortcut sets,
  - remove both registry keys,
  - remove both data roots only when the user checks "remove user data",
  - copy.
- [ ] **P6-12** `installer/uninstall.cmd`, `installer/install.sh`,
  `installer/uninstall.sh`: same dual handling.
- [ ] **P6-13** `config/create_desktop_shortcut.ps1`: new shortcut name, new
  icon, keep the old shortcut untouched.
- [ ] **P6-14** `start_brahma.bat` → `start_abhasa.bat` (or keep the old file
  as a one-line forwarder); `start_brahma.vbs` → `start_abhasa.vbs`;
  `EVO.bat` copy and brand.
- [ ] **P6-15** `bootstrap.ps1`: banner, error copy, and the launcher it calls.
- [ ] **P6-16** Scheduled task `BrahmaAI_GameUpdater` in `actions/game_updater.py`
  → `Abhasa_GameUpdater`; delete/ignore the old task on upgrade.
- [ ] **P6-17** `installer/install.sh` macOS bundle id
  `com.titech.brahmaevo` → `com.abhasa.evo` (or `com.abhasa.evo` under the
  owner's reverse-DNS).
- [ ] **P6-18** Firewall/network identifiers: `dashboard/server.py` no longer
  mutates firewall, but any remaining rule names or cert CNs must be new.

**Definition of Done:** `build_all.ps1` produces `dist\AbhasaEvo\AbhasaEvo.exe`
and `dist\AbhasaEvo_Setup.exe`; a clean install creates only new names; running
the new uninstaller on an old install removes the old names too.

**Rollback:** keep the old spec/binary names until the new build is verified;
the uninstaller is the only irreversible piece, so test it on a VM first.

---

## 13. Phase 7 — Python packages and external contracts

**Goal:** new import paths and API identity, with old names still working.

### 13.1 Packages

- [ ] **P7-1** Create `abhasa_api/` as the real package. Keep
  `brahma_api/__init__.py` as a shim that re-exports the new package and emits a
  `DeprecationWarning`.
- [ ] **P7-2** Same for `abhasa_connect/` and `brahma_connect/`.
- [ ] **P7-3** Update `main.py` imports, `installer/*.spec` datas/hiddenimports,
  `tests/api_support.py`, and all `from brahma_api` / `from brahma_connect`
  imports.
- [ ] **P7-4** Update `config/brahma_api.example.json` →
  `config/abhasa_api.example.json`; loader reads new first, old fallback.
- [ ] **P7-5** `brahma_connect/gateway/server.py` config/registry paths:
  `config/abhasa_connect.json`, `config/abhasa_connect/devices.json`; dual-read
  old files.
- [ ] **P7-6** Update `pyproject.toml` `extend-exclude` if the package names
  appear there; update `scripts/generate_sbom.py` package name.

### 13.2 OpenAI-compatible API

- [ ] **P7-7** `brahma_api/models.py`:
  - `LISTED_MODELS` includes `abhasa-evo`, `abhasa-evo:read`,
    `abhasa-evo:plan`, `abhasa-evo:passthrough`,
  - accept legacy `brahma-evo*` as aliases,
  - `owned_by` → `abhasa`,
  - response metadata key `brahma` → `abhasa` (emit both during window).
- [ ] **P7-8** `brahma_api/server.py`:
  - mount `/v1/abhasa/whoami`, `/sessions`, `/runs/{id}` in addition to the old
    routes,
  - accept `X-Abhasa-Approval` and `X-Abhasa-Session`, emit new names,
  - `service` string → `abhasa_api` (keep `brahma_api` as `service_legacy`),
  - FastAPI title.
- [ ] **P7-9** `brahma_api/translate.py`:
  - `system_fingerprint` → `abhasa-evo/…`,
  - metadata object key → `abhasa` (emit both),
  - keep old values readable by old clients.
- [ ] **P7-10** `brahma_api/schemas.py`, `errors.py`, `runner.py`, `config.py`,
  `__main__.py`: messages, audit entity `brahma_api` → `abhasa_api`, config
  filename.
- [ ] **P7-11** Add compatibility tests:
  - old model id still returns 200,
  - old header still works,
  - old route still works,
  - new model id appears in `/v1/models`,
  - old and new metadata keys both present during the window.

### 13.3 Gateway and dashboard

- [ ] **P7-12** `brahma_connect/gateway/server.py`,
  `discovery.py`, `pairing.py`:
  - service name `_ABHASA._tcp.local.`,
  - advertise both names during the window,
  - discover both,
  - pairing payload version stays compatible.
- [ ] **P7-13** `brahma_connect/gateway/mobile_portal.py`: title, copy, service
  label.
- [ ] **P7-14** `dashboard/server.py`:
  - cert filenames `abhasa.key/.crt`, CN `Abhasa Local Remote`,
  - read old `brahma.key/.crt` if new missing,
  - `_AES_SALT` → new value **only with a migration** for stored device tokens;
    otherwise keep the old salt (it is not user-visible),
  - localStorage keys `abhasa_token`, `abhasa_key`, `abhasa_device_token`;
    read old keys as fallback in the page JS,
  - page copy.
- [ ] **P7-15** `core/audit.py` logger `brahma.audit` → `abhasa.audit`;
  `discord_bot.py` logger `brahma_evo.discord` → `abhasa_evo.discord`
  (cosmetic, but do it with the rest).

**Definition of Done:** an old API client works unchanged; a new client sees
`abhasa-evo`; an old phone can still pair; a new phone sees `_ABHASA._tcp`;
dashboard tokens survive.

**Rollback:** the shims and dual-reads are additive; remove them in the cleanup
release.

---

## 14. Phase 8 — Android companion

**Goal:** brand the companion without breaking paired devices.

**Decision first (G8).** Changing `applicationId` makes a different app. If the
store listing cannot migrate, keep `com.brahma.connect` for one release and
change only display strings/class names.

- [ ] **P8-1** `brahma-connect-android/settings.gradle.kts`:
  `rootProject.name = "abhasa-connect-android"` (directory rename optional).
- [ ] **P8-2** `app/build.gradle.kts`:
  - `namespace = "com.abhasa.connect"` (safe),
  - `applicationId = "com.abhasa.connect"` **only if G8 approves**; otherwise
    keep `com.brahma.connect`.
- [ ] **P8-3** Move `app/src/main/java/com/brahma/connect/` →
  `com/abhasa/connect/` and update `package` + imports.
- [ ] **P8-4** Rename classes/files:
  - `BrahmaConnectApplication` → `AbhasaConnectApplication`,
  - `BrahmaConnectForegroundService` → `AbhasaConnectForegroundService`,
  - `BrahmaAccessibilityService` → `AbhasaAccessibilityService`,
  - `BrahmaWebSocketClient` → `AbhasaWebSocketClient`,
  - `BrahmaGatewayDiscovery` → `AbhasaGatewayDiscovery`,
  - `BrahmaConnectApp` → `AbhasaConnectApp`,
  - `BrahmaConnectTheme` → `AbhasaConnectTheme`,
  - `BrahmaConnectCapabilities` → `AbhasaConnectCapabilities`.
- [ ] **P8-5** `AndroidManifest.xml`: application/service names, icon resource,
  label, theme, accessibility resource.
- [ ] **P8-6** `res/values/strings.xml`: `Abhasa Evo Connect`,
  `Abhasa Evo Connect accessibility bridge…`.
- [ ] **P8-7** `res/xml/brahma_accessibility_service.xml` →
  `abhasa_accessibility_service.xml`; update the manifest reference.
- [ ] **P8-8** `res/drawable/ic_brahma_launcher.png` → new launcher asset
  (see Phase 9).
- [ ] **P8-9** `res/values/themes.xml`, `values-night/themes.xml`: theme names.
- [ ] **P8-10** `BrahmaConnectForegroundService.kt`:
  - notification channel `abhasa_connect`,
  - action strings `com.abhasa.connect.action.*`,
  - notification titles.
- [ ] **P8-11** `MainActivity.kt`, `DeviceCommandHandler.kt`,
  `PairingStorage.kt`, `AgentStateStore.kt`: user-visible strings and package
  imports.
- [ ] **P8-12** `app/src/main/assets/web_background/index.html`: title,
  `setBrahmaState` → keep the JS hook name for compatibility or add
  `setAbhasaState` and keep the old alias; the desktop injects this hook.
- [ ] **P8-13** `brahma-connect-android/README.md`.
- [ ] **P8-14** Pairing compatibility test: old desktop ↔ new app, new desktop
  ↔ old app, both during the window.

**Definition of Done:** the Android build succeeds; a new app pairs with a new
desktop; an old app still pairs during the window; the launcher label and icon
are new.

**Rollback:** keep `com.brahma.connect` and the old resource names until the
store listing decision is final.

---

## 15. Phase 9 — Assets and visual identity

**Goal:** a mark that says "reflection", not "deity".

- [ ] **P9-1** Design the new mark per the naming statement: *Named for the
  light it throws back, not for the light it is.*
  - a surface/reflection motif,
  - no deity, no third eye, no mandala-as-halo,
  - readable at 16 px.
- [ ] **P9-2** Produce:
  - `assets/abhasa_evo_logo.png` (220 px+),
  - `assets/abhasa_evo_logo.ico`,
  - `assets/Abhasa_Lite_Logo.png`,
  - `assets/Abhasa_Lite_Logo.ico`,
  - Android `ic_abhasa_launcher.png` (all densities),
  - macOS `.icns` if the install.sh path is kept.
- [ ] **P9-3** Keep the old asset files for one release: the uninstaller and
  old shortcuts reference them. Do not delete until the cleanup release.
- [ ] **P9-4** Update references:
  - `ui.py` `LOGO_FILE`/`LOGO_ICO`,
  - `main.py` shortcut icon,
  - `installer/*.spec` icon paths,
  - `installer/install_wizard.py`, `uninstall_wizard.py`,
  - `config/create_desktop_shortcut.ps1`,
  - `core/ui_commandbar.py` mini logo,
  - `core/ui_connectivity.py` shortcut/pin icons.
- [ ] **P9-5** Add an "About" page definition:
  > *ābhāsa* — a reflection, an appearance; the light a surface throws back.
  > Named for the light it throws back, not for the light it is.
- [ ] **P9-6** Never abbreviate to "Bhasa" in copy or assets (it reads as
  *bhāṣā*, "language").

---

## 16. Phase 10 — Docs, repo, and community

**Goal:** the public story is coherent and the audit trail is preserved.

- [ ] **P10-1** Rewrite `README.md`:
  - title, logo, naming statement, About section,
  - clone URL placeholder for the new repo,
  - license name,
  - all voice/command examples (`"Abhasa, …"`),
  - architecture tree (`abhasa_api`, `abhasa_connect`, `abhasa-connect-android`).
- [ ] **P10-2** `AGENTS.md`: replace `BRAHMA.md`/`.brahma` references with
  `ABHASA.md`/`.abhasa`, note the dual-read window, update commands.
- [ ] **P10-3** `CONTRIBUTING.md`, `SECURITY.md`, `PRIVACY_POLICY.md`:
  names, URLs, email (`security@abhasa.ai` — only if the domain is cleared).
- [ ] **P10-4** `docs/ARCHITECTURE-SPEC.md`: title, module names, env vars,
  paths, document-authority table. This is the system map; update it in the same
  PR as the code it describes.
- [ ] **P10-5** `docs/USER-GUIDE.md`: install filename, screenshots/copy, API
  section (`abhasa_api`), voice examples.
- [ ] **P10-6** `docs/AGENTIC-HARNESS*.md`: `You are Abhasa`, env vars, paths.
- [ ] **P10-7** `docs/OPENAI-COMPATIBLE-API-DESIGN.md`: model IDs, routes,
  headers, package name. Mark old IDs as deprecated aliases.
- [ ] **P10-8** `docs/SECURITY-ARCHITECTURE-ROADMAP.md`: live headers/links
  only; leave the historical analysis body.
- [ ] **P10-9** Add the historical-record banner to the docs in §2.5.
- [ ] **P10-10** Add `CHANGELOG`/release note:
  - what changed,
  - why (link `REBRAND-PROPOSITION.md` and `ADR-029`),
  - the compatibility window,
  - the migration command.
- [ ] **P10-11** Repo rename:
  - rename on GitHub,
  - keep the old URL redirect,
  - update `origin`/`upstream` remotes,
  - update badges, clone commands, issue links,
  - announce with the reason, not just the name.
- [ ] **P10-12** Community:
  - Discord bot display name and command group,
  - Discord server name/channels,
  - domain/email (`abhasa.ai` if cleared),
  - social handles,
  - store listing (Android) if G8 approves the new package.
- [ ] **P10-13** Do **not** hand-edit generated indexes or scan reports.

---

## 17. Phase 11 — Verification and release

Run after every phase; full run before release.

### 17.1 Static and unit gates

```powershell
py -3.11 -m ruff check .
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
py -3.11 -m pytest tests -q -k agent
py -3.11 -m agent run "say hello" --provider stub --json --no-session
python scripts/rebrand_audit.py --report
```

- [ ] **P11-1** Ruff clean.
- [ ] **P11-2** Full pytest green (expect ~95 s; offscreen Qt).
- [ ] **P11-3** Harness subset green on 3.11/3.12/3.13 (CI matrix).
- [ ] **P11-4** CLI smoke run prints `Abhasa` in the prompt/banner.
- [ ] **P11-5** Audit tool reports only allowlisted historical docs.
- [ ] **P11-6** No new `Brahma` in live user-facing strings. Use the allowlist
  in `scripts/rebrand_audit.py`.

### 17.2 Functional gates

- [ ] **P11-7** Fresh install on a clean Windows VM:
  - `AbhasaEvo_Setup.exe` installs to `%LOCALAPPDATA%\Abhasa_Evo`,
  - shortcut `Abhasa Evo.lnk`,
  - registry `…\Uninstall\AbhasaEvo`,
  - app title/tray/prompt say `Abhasa`,
  - data written under `%LOCALAPPDATA%\Abhasa`.
- [ ] **P11-8** Upgrade install on a machine with old data:
  - migration copies and verifies,
  - old settings/API keys/memories are present,
  - old app can still run if kept.
- [ ] **P11-9** Uninstaller removes both new and old names and both data roots
  only when requested.
- [ ] **P11-10** Wake words: `abhasa`, `hey abhasa`, `brahma evo`,
  `hey brahma evo`.
- [ ] **P11-11** API compatibility:
  - old model id `brahma-evo` works,
  - new `abhasa-evo` works,
  - old route/header works,
  - `/v1/models` lists both during the window.
- [ ] **P11-12** Gateway: old phone pairs with new desktop; new phone sees
  `_ABHASA._tcp.local.`; old mDNS still discovered.
- [ ] **P11-13** Dashboard: existing device token still logs in; new cert is
  created under the new name; old cert still accepted.
- [ ] **P11-14** Android build (`.\gradlew.bat assembleDebug`) and pairing
  matrix from P8-14.
- [ ] **P11-15** Secrets: DPAPI-encrypted values written before the rebrand
  still decrypt; new values write the same prefix.

### 17.3 Release gates

- [ ] **P11-16** `build_all.ps1` produces the new artifacts and SHA256SUMS.
- [ ] **P11-17** `tools/sign_release.py` signs with the new env name and the old
  key still verifies old manifests.
- [ ] **P11-18** SBOM generation (`scripts/generate_sbom.py --verify`) passes
  with the new product name.
- [ ] **P11-19** CI artifact names are new; old artifacts remain downloadable.
- [ ] **P11-20** Release note links the proposition and the ADR.

---

## 18. Phase 12 — Cleanup (after one full release)

Do this only after telemetry/feedback shows no remaining legacy users.

- [ ] **P12-1** Remove legacy wake words and the deprecation log.
- [ ] **P12-2** Remove `BRAHMA_*` env fallbacks.
- [ ] **P12-3** Remove `.brahma` / `BRAHMA.md` discovery fallbacks.
- [ ] **P12-4** Remove `brahma_api` / `brahma_connect` shim packages.
- [ ] **P12-5** Remove old API routes/headers/model IDs/metadata keys.
- [ ] **P12-6** Remove old mDNS service advertisement/discovery.
- [ ] **P12-7** Remove old dashboard token/cert fallbacks and localStorage
  fallbacks.
- [ ] **P12-8** Delete old assets, old spec files, old launchers.
- [ ] **P12-9** Delete old data roots (only after a full release cycle and an
  explicit user prompt).
- [ ] **P12-10** Remove old docs banner/redirect notes; keep the historical
  docs themselves.
- [ ] **P12-11** Flip `scripts/rebrand_audit.py` from warning to blocking.
- [ ] **P12-12** Rename `BrahmaUI`/`BrahmaLive` only if there is a separate
  reason; otherwise leave them as internal names.

---

## 19. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Shipping before `TRADEMARK.md`/`LICENSE` are amended | Medium | Legal | G1 blocking gate; Phase 0 first |
| R2 | `Abhasa` clearance fails (pejorative "semblance") | Medium | Brand | Commission clearance early; fallback `Pratibimba` |
| R3 | Users lose data during root migration | Medium | High | Copy, verify, never delete; marker file; rollback path |
| R4 | Old DPAPI secrets become unreadable | Low | High | Never rename `BRAHMA_DPAPI`; test decrypt of a pre-rebrand secret |
| R5 | API clients break on model IDs/routes/headers | Medium | High | Dual-read, dual-emit, compatibility tests |
| R6 | Paired phones stop working | Medium | High | Advertise/discover both mDNS names; keep pairing payload compatible |
| R7 | Android `applicationId` change orphans users | High if changed | High | G8: keep `com.brahma.connect` for one release; new listing only if store allows migration |
| R8 | Installer/uninstaller leaves old files/registry keys | Medium | Medium | Uninstaller removes both; VM test |
| R9 | Test suite breaks on exact strings | High | Medium | Update tests with the code; add compatibility tests |
| R10 | Repo rename breaks links/forks/CI | Medium | Medium | GitHub redirect; update remotes/docs; announce |
| R11 | Historical docs get rewritten, losing audit trail | Medium | Medium | §2.5 do-not-rewrite list; banner only |
| R12 | Partial rename leaves two brand names in the UI | High | Medium | `scripts/rebrand_audit.py`; phase gates |
| R13 | Wake word "abhasa" misheard | Medium | Low | Acceptance harness; keep legacy alias |
| R14 | "Bhasa" abbreviation creeps in | Medium | Medium | Lint/copy rule; audit tool pattern |
| R15 | Signing key env rename breaks release signing | Low | High | Accept both names; test old key verification |
| R16 | SBOM/release manifest product name mismatch | Low | Medium | Update tools; verify old manifests still parse |

---

## 20. Suggested PR / commit sequence

Each PR should be independently green.

1. **PR-1 - ADR + legal**: `ADR-029`, `TRADEMARK.md`, `LICENSE`. No code.
2. **PR-2 — branding seam**: `core/branding.py`, `core/user_paths.py`
   migration, `scripts/rebrand_audit.py`, `tests/test_branding.py`.
3. **PR-3 — identity + prompts**: `core/identity.py`, `main.py` prompts,
   `core/prompt.txt`, `agent/prompts.py`.
4. **PR-4 — UI copy**: `ui.py`, `core/ui_*.py`, `discord_bot.py`,
   `dashboard/server.py`, `mobile_portal.py`, installer copy.
5. **PR-5 — wake words**: `main.py`, acceptance tests.
6. **PR-6 — data roots + project files**: hardcoded `BrahmaAI` sweep,
   `.brahma` → `.abhasa`, migration tool, `agent/skills.py`, `agent/mcp.py`,
   `agent/prompts.py`.
7. **PR-7 — env vars**: `branding.env`, 24 call sites, build scripts.
8. **PR-8 — packaging**: specs, version files, `build_all.ps1`, CI, release
   tools, launchers, installer/uninstaller.
9. **PR-9 — packages + API**: `abhasa_api`/`abhasa_connect` shims, model IDs,
   routes, headers, response keys, compatibility tests.
10. **PR-10 — gateway + dashboard**: mDNS, config paths, certs, tokens.
11. **PR-11 — Android**: package/classes/resources (respect G8).
12. **PR-12 — assets + docs + repo**: logo, README, AGENTS, live docs,
    historical banners, repo rename, announcement.
13. **PR-13 — cleanup** (after one release): remove aliases, old assets, old
    paths, flip audit to blocking.

---

## 21. Handoff notes

### What the executing agent must know

- **Do not start with a global find/replace.** The tree has 4,403 matches and
  several are data-format constants or historical records.
- **Read `AGENTS.md`** before changing harness code. `agent/` must stay
  stdlib-only; CI collects `tests/test_agent_*.py` on 3.11/3.12/3.13 with only
  pytest.
- **The permission boundary is `AgentRuntime.execute`.** Do not add a second
  path while renaming.
- **`core/user_paths.get_user_data_dir()` is the only data-root seam.** Make it
  do the migration; do not add a second resolver.
- **`BRAHMA_DPAPI` is a binary prefix, not a brand string.** Leave it.
- **`config/identity.json`, `config/agent_permissions.json`,
  `config/brahma_api.json`, `.brahma/mcp.json` are user data.** Do not commit
  them.
- **Historical docs are evidence.** Add a banner, do not rewrite.
- **The proposition's date is 2026-11-06; the tree has moved.** Re-run the
  counts before starting and update this plan's inventory if the delta is
  material.

### Commands the executing agent will need

```powershell
# Baseline
py -3.11 -m ruff check .
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
py -3.11 -m pytest tests -q -k agent
py -3.11 -m agent run "say hello" --provider stub --json --no-session

# Brand audit (after PR-2)
python scripts/rebrand_audit.py --report
python scripts/rebrand_audit.py --json > rebrand-baseline.json

# Packaging
.\build_all.ps1
.\build_all.ps1 -DryRun

# Android
cd brahma-connect-android
.\gradlew.bat assembleDebug
```

### Open questions for the owner

1. Is `Abhasa` cleared for trademark and domain? (G4)
2. Does the Android app need a new store listing, or can `applicationId` stay?
   (G8)
3. Is the repo rename part of this release or the next? (G9)
4. Should `BrahmaUI`/`BrahmaLive` ever be renamed, or are they permanently
   internal? (G6)
5. Is `security@brahma.ai` a real mailbox, and does `abhasa.ai` exist? (P10-3)
6. Which docs are still "live" vs "historical"? Confirm §2.5.

---

## Appendix A — Exact file inventory by surface

### A.1 Core identity and prompts

- `core/identity.py`
- `main.py` (lines ~82, 148-181, 251-259, 305, 354, 380, 653-681, 1208-1306,
  1540-1551, 1707-1711, 1988-2031, 2200, 2451-2452, 2744, 2864, 2903,
  2927-3016, 3063-3125, 3150-3174, 3206-3211)
- `core/prompt.txt` (lines 1, 43, 88)
- `agent/prompts.py` (line 37, 141)
- `agent/__main__.py` (lines 238, 559, 577-578, 676, 716, 727, 799)

### A.2 UI and user-facing copy

- `ui.py` (lines 61-62, 211-212, 369-370, 429, 593, 666, 730, 735, 785)
- `core/ui_apply_state.py`
- `core/ui_chat.py`
- `core/ui_commandbar.py`
- `core/ui_connectivity.py`
- `core/ui_voice_library.py`
- `core/ui_devices.py`
- `core/ui_telemetry.py`
- `core/audio_io.py`
- `core/attention_flow.py`
- `core/device_flow.py`
- `core/desktop_host.py`
- `core/desktop_tools.py`
- `discord_bot.py`
- `dashboard/server.py`
- `brahma_connect/gateway/mobile_portal.py`
- `installer/install_wizard.py`
- `installer/uninstall_wizard.py`
- `installer/install.sh`
- `installer/uninstall.sh`
- `installer/uninstall.cmd`
- `actions/call_assistant.py`
- `actions/calorie_counter.py`
- `actions/auto_heal_engine.py`
- `actions/brahma_dev_agent.py`
- `features/*`

### A.3 Data roots and project files

- `core/user_paths.py`
- `core/identity.py`
- `memory/config_manager.py`
- `workspace_store.py`
- `agent/session.py`
- `agent/skills.py`
- `agent/mcp.py`
- `agent/prompts.py`
- `main.py`
- `installer/install_wizard.py`
- `installer/uninstall_wizard.py`
- `installer/install.sh`
- `installer/uninstall.sh`
- `installer/uninstall.cmd`
- `dashboard/server.py`
- `actions/browser_control.py`
- `actions/office_builder.py`
- `actions/google_workspace_mcp.py`
- `actions/game_updater.py`
- `core/avatar_catalog.py`
- `core/speech_tts.py`
- `core/voice_catalog.py`
- `core/tool_index.py`
- `core/skill_store.py`
- `core/agent_bridge/artifacts.py`
- `core/agent_bridge/detectors.py`
- `features/*` deliverable paths
- `tests/test_speech_tts_voices.py`, `tests/test_voice_catalog.py`,
  `tests/test_voice_library_ui.py`

### A.4 Env vars

- `agent/sandbox.py`, `agent/permissions.py`, `agent/runtime.py`,
  `agent/process.py`, `agent/providers.py`, `agent/mcp.py`, `agent/skills.py`,
  `agent/subagent_library.py`, `agent/__main__.py`
- `core/agent_delegation.py`, `core/agent_bridge/detectors.py`,
  `core/agent_bridge/adapters/*`, `core/secret_store.py`, `core/redaction.py`,
  `core/trust.py`, `core/tool_policy.py`
- `dashboard/server.py`
- `build_all.ps1`, `EVO.bat`
- `.github/workflows/*`

### A.5 Packaging and OS

- `version.txt`, `version_setup.txt`
- `installer/BrahmaEvo.spec`, `installer/BrahmaEvo_Setup.spec`,
  `installer/Uninstall.spec`, `installer/BrahmaEcho.spec`,
  `installer/BrahmaEcho_Setup.spec`
- `installer/install_wizard.py`, `installer/uninstall_wizard.py`
- `installer/install.sh`, `installer/uninstall.sh`, `installer/uninstall.cmd`
- `build_all.ps1`, `bootstrap.ps1`, `start_brahma.bat`, `start_brahma.vbs`,
  `EVO.bat`
- `config/create_desktop_shortcut.ps1`
- `.github/workflows/build.yml`, `.github/workflows/build-all-platforms.yml`,
  `.github/workflows/ci.yml`
- `tools/build_release.py`, `tools/sign_release.py`,
  `tools/rotate_release_key.py`, `scripts/generate_sbom.py`

### A.6 Python packages and external contracts

- `brahma_api/*` (all 8 modules)
- `brahma_connect/*` (all 9 modules)
- `main.py` imports and startup
- `installer/*.spec`
- `tests/api_*.py`, `tests/test_brahma_connect*.py`,
  `tests/test_gateway_auth.py`, `tests/test_security_q0b.py`
- `dashboard/server.py`
- `docs/OPENAI-COMPATIBLE-API-DESIGN.md`

### A.7 Android

- `brahma-connect-android/settings.gradle.kts`
- `brahma-connect-android/app/build.gradle.kts`
- `brahma-connect-android/app/src/main/AndroidManifest.xml`
- `brahma-connect-android/app/src/main/java/com/brahma/connect/**` (15 files)
- `brahma-connect-android/app/src/main/res/values/strings.xml`
- `brahma-connect-android/app/src/main/res/values/themes.xml`,
  `values-night/themes.xml`
- `brahma-connect-android/app/src/main/res/xml/brahma_accessibility_service.xml`
- `brahma-connect-android/app/src/main/res/drawable/ic_brahma_launcher.png`
- `brahma-connect-android/app/src/main/assets/web_background/index.html`
- `brahma-connect-android/README.md`

### A.8 Docs

- Live: `README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `PRIVACY_POLICY.md`, `docs/ARCHITECTURE-SPEC.md`, `docs/USER-GUIDE.md`,
  `docs/AGENTIC-HARNESS*.md`, `docs/OPENAI-COMPATIBLE-API-DESIGN.md`,
  `docs/SECURITY-ARCHITECTURE-ROADMAP.md` (headers only).
- Historical: `docs/scan-reports/*`, `docs/VERSION-1.0.1-*.md`,
  `docs/PRODUCTION-RELEASE-REPORT.md`, `docs/SYSTEM-ANALYSIS-MASTER-REPORT.md`,
  `docs/FULL-SYSTEM-SCAN-AND-ANALYSIS-PLAN.md`, `docs/CODE-REVIEW-TODO.md`,
  `docs/GITHUB-ACTIONS-REMEDIATION.md`, `docs/UPSTREAM-INTEGRATION-PLAN.md`,
  `docs/SECURITY-ANALYSIS-ISSUE-4.md`, `docs/AGENT-BRIDGE-*`,
  `docs/agent-orchestrator-bridge.md`, `docs/ADR-018..021`.

---

## Appendix B — Naming test (from the proposition)

Run any future candidate through these before it reaches a logo:

1. **Switch-off test** — does the name still make sense when the machine is
   off?
2. **Person test** — would it be correct for a person, saint, or god?
3. **Goal test** — is the term something the path is *toward*?
4. **Wake-word test** — can a tired user say "Hey ___" at 2 a.m.?
5. **Falsification test** — if the product starts acting like the name, is that
   a bug?

`Abhasa` passes all five. `Pratibimba` is the formal fallback. `Dipa` is the
consumer fallback. Do not use `Bhasa`, `Bimba`, `Sakshi`, `Manas`, `Buddhi`,
`Akkha`, `Sahaya`, or `Mitta`.
