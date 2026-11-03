# Security Policy

## Supported Versions

Brahma Evo actively maintains and provides security patches for the following versions:

| Version | Supported | Security Maintenance |
| :--- | :--- | :--- |
| `1.1.x` (current stable) | :white_check_mark: | Full security patching and backports |
| `1.0.x` | :white_check_mark: | Critical CVE patches only |
| `< 1.0.0` | :x: | End of Life (upgrade required) |

---

## Reporting a Vulnerability

We take the security of Brahma Evo seriously. If you discover a security vulnerability, please do **not** disclose it publicly via GitHub issues or discussions.

### Contact & Private Disclosure

* **Email:** `security@brahma.ai`
* **GitHub Advisory:** Report privately through [GitHub Security Advisories](https://github.com/titechprabhasolutions/Brahma-Ai-Evo/security/advisories/new).
* **PGP Key:** For encrypted submissions, use our security PGP key (Fingerprint: `B8A4 391D 70FE C511 8234  1A92 489B 934C A495 991B`).

Please include in your report:
1. Description of the vulnerability and attack vector.
2. Reproduction steps, proof of concept (PoC), or minimal reproduction script.
3. Impact assessment (privilege escalation, remote code execution, secret leakage, etc.).
4. Affected components and operating system environment.

### Response Timelines & SLA

* **Initial Acknowledgement:** Within **48 hours**.
* **Triage & Severity Assessment:** Within **5 business days**.
* **Remediation & Patch Target:**
  * **Critical (CVSS 9.0–10.0):** 7 calendar days.
  * **High (CVSS 7.0–8.9):** 14 calendar days.
  * **Medium / Low (CVSS 0.1–6.9):** 30 calendar days.
* **Coordinated Disclosure:** We adhere to a standard **90-day coordinated disclosure policy**. Security advisories (CVE) will be published simultaneously with the release of the patched version.

---

## Security Architecture & Defenses

Brahma Evo implements defense-in-depth across six distinct security boundaries:

### 1. Process & Sandbox Isolation
* Model-generated skills execute strictly out-of-process inside **Windows AppContainer** profiles and **Windows Job Objects** (`core/skill_host.py`, `sandbox/launcher_win.py`).
* Job Objects enforce strict memory limits (512 MB default), active process counts, wall-clock timeouts, and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
* Subprocess execution utilizes closed `stdin` (`DEVNULL`) and scrubbed environments (`agent.sandbox.SandboxConfig`).

### 2. Secrets & Credential Protection
* All stored credentials (`config/api_keys.json`, `smart_home.key`) are protected using **Windows DPAPI** (`core/secret_store.py`).
* Plaintext credentials never touch disk or the model loop. The **Secret Broker** (`core/secret_broker.py`) enforces per-caller capability scopes and injects API keys strictly at the network boundary (`http_request`).
* The **Redaction Engine** (`core/redaction.py`) automatically scrubs API keys, bearer tokens, Fernet keys, and DPAPI ciphertext from logs and error traces.

### 3. Gateway & Network Boundary
* Brahma Connect Gateway binds to loopback (`127.0.0.1`) by default and is disabled until explicitly enabled.
* LAN pairing uses a **Short Authentication String (SAS)** 6-digit code with a 120-second TTL and automatic 10-minute lockout after 3 consecutive failures (`core/gateway_auth.py`).
* All REST and command execution endpoints require cryptographic **Bearer token authentication**, constant-time verification, and nonce replay cache protection.

### 4. Software Supply Chain & Dependencies
* Runtime dependency installation (`pip install`) is forbidden (`core/dep_broker.py`).
* All external dependencies are pinned in `deps.lock` with mandatory SHA-256 binary wheel hashes under `--only-binary=:all:`.
* Automated CycloneDX v1.5 Software Bill of Materials (SBOM) generation accompanies builds.

### 5. Plugin & Release Integrity
* Plugins require cryptographically signed `.bhplugin` bundles with detached **Ed25519 signatures** verified against a local trust store and revocation list (`core/plugin_store.py`).
* Software updates require signed `release.json` manifests with atomic A/B version directories and automatic rollback to the previous version after 2 failed starts (`core/release_manager.py`).

### 6. Prompt Injection & Taint Tracking
* Untrusted content (web pages, OCR, emails, PDFs) is encapsulated in strict boundary tags (`core/trust.py`).
* The **Quarantined Reader** (`core/quarantine.py`) neutralizes Markdown image pixel exfiltration URLs and embedded HTML scripts.
* The **Taint Policy** (`core/taint_policy.py`) permits read-only operations under taint, while requiring single-use, 60s TTL cryptographic human approval tokens for state-changing operations.
