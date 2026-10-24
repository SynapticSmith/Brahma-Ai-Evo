# Security Analysis: Upstream Issue #4

**Source Issue:** [titechprabhasolutions/Brahma-Ai-Evo#4](https://github.com/titechprabhasolutions/Brahma-Ai-Evo/issues/4)  
**Reported By:** MR-UNKNOWN74IDK  
**Severity:** Critical  
**Analysis Date:** 2026-10-24  
**Repository Analyzed:** SynapticSmith/Brahma-Ai-Evo (this fork)  
**Baseline Reference:** `docs/VERSION-1.0.1-LOG.md`

## Executive Summary

Upstream issue #4 reports critical security vulnerabilities in the original Brahma-AI-Evo related to untrusted code execution, unverified plugin loading, and unsigned self-updates. This analysis examines our fork against each reported finding to determine if the same issues exist and what mitigations/remediation are needed.

**Overall Status:** Our fork contains most of the same vulnerable patterns as reported in the upstream issue. While we have added some safety mechanisms (AST validation, sandboxed testing via SkillCrucible), the fundamental trust boundaries described in the issue remain absent or insufficiently enforced.

## Detailed Finding Analysis

### Critical Severity Findings

#### 1. LLM-Generated Code Executed and Persisted Without Trust Boundary
- **Upstream Component:** `core/skill_forge.py`
- **Issue:** Synthesizes Python code from LLM output, writes it to filesystem, loads it into running process without approval, signing, sandboxing, or integrity validation. Risk of prompt injection/RCE.

**Our Fork Status:** **PRESENT - VULNERABLE**

**Evidence in our codebase:**
- `core/skill_forge.py:46-200` - `SkillForge.forge_skill()` synthesizes code via LLM, iteratively tests via `SkillCrucible`, writes generated code to `features/{name}.py` and `features/{name}/skill.py` (lines 158-185), updates `features/__init__.py`, then hot-registers via `DynamicToolRegistry.initialize()` and verifies registration (lines 186-199). No explicit user approval step, no signing, no integrity check.
- The generation prompt (lines 245-302) instructs the LLM to produce production-ready code with network capabilities and file writes.
- Code is persisted to disk in the application directories under user data path (`get_user_data_dir()`/features) and immediately loaded into the running process.

**Assessment:** Same pattern as upstream. The Crucible provides some validation but does not establish a trust boundary - it's a quality/safety check, not an authorization gate.

#### 2. Arbitrary Plugin Auto-Import Without Allowlisting/Signing
- **Upstream Component:** `plugin_manager.py`
- **Issue:** Imports every `.py` file in plugins directory with `importlib` without allowlisting, signing, or verification.

**Our Fork Status:** **PRESENT - VULNERABLE**

**Evidence in our codebase:**
- `plugin_manager.py:14-35` - `load_plugins()` iterates over all `*.py` files in `self.plugins_dir` (skipping `__*`), creates module specs, loads and executes them via `spec.loader.exec_module(mod)`, registers plugins. No allowlist, no signature verification, no checksums, no user consent.
- Plugins are loaded at startup in `main.py:4234-4235` (`PluginManager(BASE_DIR)`, `plugin_manager.load_plugins()`).
- `plugins/` directory exists as an extension point per docs (docs/agent-orchestrator-bridge.md mentions it as hook-based with plugins currently empty).

**Assessment:** Identical vulnerability as reported.

#### 3. Unsigned Self-Update Logic (Remote Code Execution Primitive)
- **Upstream Component:** `updater.py`, `core/updater.py`
- **Issue:** Fetches updates from GitHub and does `git reset --hard` without authentication, signature verification, or user consent.

**Our Fork Status:** **PRESENT - VULNERABLE (MULTIPLE IMPLEMENTATIONS)**

**Evidence in our codebase:**
- `updater.py:15-60` - `_run_git()` with timeouts, but `update_from_github()` does `git fetch origin BRANCH` then `git pull --ff-only origin BRANCH` (line 57). While it checks for clean working tree and fast-forward, there is **no signature verification**, **no commit signing verification**, **no checksum validation**, and it pulls from a hardcoded remote (`https://github.com/titechprabhasolutions/Brahma---personal.git` line 11). No explicit user consent shown in this code path.
- `core/updater.py:37-76` - `UpdateChecker` checks GitHub API for new commits (line 38-46), emits signal if different. `apply_update_and_restart()` does `git fetch origin main` then `git reset --hard origin/main` (lines 68-70) with **no verification** and restarts via `os.execv` (line 74). Also no user consent, no signature checking.
- Both paths can replace local code with remote code and execute it.

**Assessment:** Same critical vulnerability. The root-level `updater.py` does fast-forward checks but still trusts remote; `core/updater.py` does hard reset without any verification.

### High Severity Findings

#### 4. Generated Code Instructed to Disable TLS Verification
- **Upstream Component:** `core/skill_forge.py`
- **Issue:** Generation prompt explicitly allows `verify=False` and insecure transport.

**Our Fork Status:** **PRESENT - INSTRUCTED IN PROMPT**

**Evidence in our codebase:**
- `core/skill_forge.py:254-258` - In synthesis prompt: "When external live data or web downloads are needed, prefer `requests` with `timeout=8, verify=False`, OR if using `urllib`, ALWAYS bypass Windows SSL verification via `import ssl; ctx = ssl._create_unverified_context()` because Python on Windows frequently throws `[SSL: CERTIFICATE_VERIFY_FAILED]`."
- `core/skill_forge.py:381` - In repair prompt: similar instruction to use `verify=False` or bypass SSL verification.

**Assessment:** The LLM is explicitly instructed to generate code that disables TLS verification - this is dangerous and matches the issue.

#### 5. Automatic Dependency Installation Without Pinning/Hash Validation
- **Upstream Component:** `core/skill_crucible.py`
- **Issue:** Resolves imports to package names and installs with pip without pinning, hash validation, or provenance checking.

**Our Fork Status:** **PRESENT - VULNERABLE**

**Evidence in our codebase:**
- `core/skill_crucible.py:156-202` - `resolve_dependencies()` installs missing packages via `pip install pip_name --quiet` (line 190) with no version pinning, no hash checking, no index restrictions, no provenance verification. It does uninstall/reinstall for `speedtest` in some cases (line 189). No verification of package source.
- `core/skill_forge.py:90-95` - Calls `SkillCrucible.resolve_dependencies(deps)` as part of the verification flow.
- `core/skill_crucible.py:68-83` has `IMPORT_TO_PIP` mapping but no security controls.

**Assessment:** Same supply chain risk as reported.

#### 6. Secrets Stored in Recoverable Local Format
- **Upstream Component:** `smart_home/storage.py`, `core/user_paths.py`
- **Issue:** Credentials stored in user profile directory protected with locally stored encryption key instead of OS-native secure storage.

**Our Fork Status:** **PRESENT - VULNERABLE**

**Evidence in our codebase:**
- `smart_home/storage.py:13-60` - `CredentialVault` uses `cryptography.fernet.Fernet` with key loaded from `KEY_FILE` (`CONFIG_DIR / "smart_home.key"`, line 23). Key is generated if missing and stored as raw bytes in the file (lines 40-45). Same pattern as described - encryption key is stored locally alongside encrypted data.
- Data stored in SQLite (`smart_home.sqlite3`) with encrypted credentials.
- `core/user_paths.py` manages user data directories.

**Assessment:** If the host/user profile is compromised, both key and encrypted data are accessible - same risk as reported. No use of OS keychain/DPAPI/Keychain/libsecret.

### Medium Severity Findings

#### 7. Insufficient Validation in Sandbox
- **Upstream Component:** `core/skill_crucible.py`
- **Issue:** Validation uses narrow blacklist of strings, doesn't prevent subprocess, file writes, dynamic imports, os.system, eval, exec, unsafe registry operations.

**Our Fork Status:** **PARTIALLY ADDRESSED BUT INSUFFICIENT**

**Evidence in our codebase:**
- `core/skill_crucible.py:25-41` has `BANNED_AST_PATTERNS` checking for specific strings in string literals (format commands, system paths like system32, specific Brahma modules). 
- `core/skill_crucible.py:98-131` does AST parsing, checks for `execute` function presence, scans string literals for banned patterns.
- **Gaps:** No blocking of `subprocess`, `os.system`, `eval`, `exec`, `__import__`, `importlib`, file write operations to sensitive paths, network operations, etc. The blacklist is very narrow and easily bypassed. The sandbox runs as a subprocess (`run_sandbox_test` line 274) with timeout, but that's process isolation not security isolation - it still runs with same user privileges and filesystem access.
- Also note the prompt explicitly tells it to use `subprocess` patterns aren't banned; it tells to use `requests`/`urllib`.

**Assessment:** Better than nothing (has AST check + subprocess sandbox), but doesn't meet the issue's concern about comprehensive prevention. Easy to bypass.

#### 8. High-Risk Automation Actions Lack Strong Privilege Separation
- **Upstream Component:** App architecture/automation modules
- **Issue:** Broad desktop automation with user privileges, needs approval boundaries.

**Our Fork Status:** **PRESENT**

**Evidence in our codebase:**
- The app has extensive automation capabilities (PC control, UI automation, etc. as described in README and codebase)
- No visible approval gates for destructive/high-risk operations in the code paths examined
- Skills can be forged and executed with full user privileges

**Assessment:** Matches the concern.

## Comparison with Our Fork's Changes

Looking at `docs/VERSION-1.0.1-LOG.md`, our fork has made significant improvements in many areas (provider-agnostic core, gateway for LLM calls, better testing, etc.), but **none of the security issues mentioned in #4 appear to be addressed**. The changelog doesn't mention security hardening related to plugin loading, skill forging sandboxing improvements beyond what was there, update verification, or secret storage migration.

## Recommendations

### Immediate (Priority 0 - Containment)
1. **Disable/Restrict Skill Forge by default** - Add configuration to disable automatic skill forging, or require explicit user confirmation before persisting and loading generated skills.
2. **Harden Plugin Loading** - Implement allowlist for plugins, require explicit opt-in, or disable auto-loading from writable directories. Consider moving plugins to a signed/trusted location.
3. **Disable or Secure Auto-Updates** - Disable automatic Git-based updates, or require cryptographic signature verification (e.g., signed commits/tags) and explicit user consent before applying updates.
4. **Add Confirmation Gates** - For any code execution from LLM output or plugins, require explicit user approval with clear warnings.

### Short-term (Priority 1)
1. **Remove/Restrict TLS Bypass Instructions** - Change the skill forge prompts to forbid `verify=False` and SSL verification bypasses. Enforce secure HTTPS.
2. **Secure Dependency Installation** - Pin dependencies, use hash verification (`pip install --require-hashes`), maintain allowlists, or remove auto-install capability entirely.
3. **Migrate Secrets Storage** - Use OS-native secure storage (Windows Credential Manager/DPAPI with proper protection, macOS Keychain, Linux Secret Service). The current Fernet approach with local key file is insufficient.
4. **Strengthen Sandbox Validation** - Expand AST checks to block `subprocess`, `os.system`, `eval`, `exec`, `__import__`, `importlib`, file operations outside allowed paths, network calls, etc. Consider using `ast.NodeVisitor` to walk and block dangerous operations.
5. **Add Execution Isolation** - Run generated skills/plugins in separate process with restricted permissions, resource limits, and no access to sensitive paths.

### Medium-term (Priority 2)
1. **Implement Code Signing** - Require digital signatures for plugins and generated skills before loading.
2. **Secure Update Pipeline** - Implement cryptographic verification of updates (e.g., signed release artifacts, verified commit signatures).
3. **Privilege Separation** - Separate high-risk automation capabilities behind permission boundaries with explicit approval flows.
4. **Supply Chain Security** - Pin all dependencies, use lock files, integrate dependency scanning (e.g., Safety, pip-audit) into CI.

### Long-term (Priority 3)
1. **Security Architecture Review** - Redesign the skill/plugin system with proper trust boundaries.
2. **Prompt Injection Protection** - Add defenses against malicious prompts that could generate harmful code.
3. **Security Monitoring** - Add logging and alerting for suspicious activities.
4. **Formal Security Process** - Establish security review process for generated code.

## Conclusion

Our fork **does contain all the critical vulnerabilities** reported in upstream issue #4. The security posture is essentially the same as the original repository. While our fork has many functional improvements (provider abstraction, better LLM routing, testing infrastructure), no security hardening for these specific issues has been implemented.

These vulnerabilities represent real RCE risks, especially given the application's broad desktop automation capabilities. They should be treated as **critical security issues** requiring immediate attention before deployment in any sensitive environment.

## References
- [Upstream Issue #4](https://github.com/titechprabhasolutions/Brahma-Ai-Evo/issues/4)
- [Our Fork Changelog](./VERSION-1.0.1-LOG.md)
- [Skill Forge Implementation](../core/skill_forge.py)
- [Plugin Manager](../plugin_manager.py)
- [Skill Crucible](../core/skill_crucible.py)
- [Updaters](../updater.py), [Core Updater](../core/updater.py)
- [Smart Home Storage](../smart_home/storage.py)
