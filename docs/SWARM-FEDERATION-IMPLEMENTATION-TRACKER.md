# Brahma Swarm / Federation — Implementation Progress Tracker

**Document Authority:** `docs/SWARM-FEDERATION-DEEP-DIVE.md` & `docs/SWARM-FEDERATION-INVESTIGATION.md`  
**Target:** 3-node L2 peer task federation with capability scheduling, pinned mutual identity, at-most-once mutations, and zero shared SQLite databases.  
**Last Updated:** 2026-11-11 (Ninth-Pass Remediation: Hermetic Service Auto-Resolution & Ledger Enforcement, Operator-Authenticated Pairing Offer & Unforgeable Exchange, Restricted Peer Token Scopes, HTTP Listener Alignment, Dynamic Capability Routing Active)  

---

## 1. Master Phase Tracker

| Phase | Description | Status | Gate Criteria & Honest State |
| :--- | :--- | :---: | :--- |
| **S0** | **Contracts & Architecture** | ✅ DONE | BTE-1 wire schema, ADR-030..038, threat model, state machine. Implemented & ratified in `ARCHITECTURE-SPEC.md`. |
| **S0.5** | **Cryptographic Primitives** | ✅ DONE | Ed25519 node key, X25519 + SAS derivation, SPKI pinning, request signing (`core/peer/crypto.py`). |
| **S1** | **API Transport & Adapter** | ✅ DONE | `RemoteAgentAdapter` constructs BTE-1 requests, sends `X-Brahma-Node`, polls `state` or `status`, pinned SPKI via `build_pinned_opener`, auto-signs when paired/pinned, dynamic capability routing via `select_candidate_peer`, registry token fallback, artifact fetching verb. |
| **S2** | **Core Peer Engine & Task Server** | ✅ DONE | `core/peer/store.py` with `token_id` storage, DB schema auto-migration, `brahma_api/peer.py`, airtight origin isolation, workspace binding enforcement, active 5s heartbeat thread, sync 500 error contract, budget clamping, cancel race resolution. |
| **S2.5** | **A2A v1.0 Binding** | ✅ DONE | `/.well-known/agent-card.json`, `POST /message:send`, `POST /tasks/{id}:cancel`, chat-profile token fallback verified. |
| **Artifacts** | **Content-Addressed Artifact Lane (ADR-038)** | ✅ DONE | Content-addressed storage, 32 MiB size cap (`MAX_ARTIFACT_BYTES`), SHA-256 verification, filename sanitization, task-scoped `manifest.jsonl`, origin-isolated HTTP routes (`core/peer/artifacts.py`, `brahma_api/peer.py`), and mutating upload signature symmetry enforced. |
| **S3** | **Identity, SAS Pairing & Fencing** | ✅ DONE | `NodeIdentity` DPAPI storage, mutual cryptographic SAS pairing handshake over `POST /pair/offer` and `POST /pair/exchange`, static public key pinning (`static_pub_raw`), pinned TLS, all 8 signature rejection branches return 401 with standard error codes, Invariant S-03 fan-out reachable in production, cancel-must-sign and upload-must-sign enforced. |
| **S4** | **Discovery & UI** | ✅ DONE | LAN UDP broadcast beacon & listener on port 8444 with self-filtering (`self_node_id`, `core/peer/discovery.py`), `PeerService` lifecycle coordinator with hermetic `base_dir` resolution and process-level singleton `get_active_peer_service()`, drain mode HTTP 503 wired into `create_app` and `cmd_serve`, `PeerManagementWidget` embedded into System Connectivity page, real SAS handshake dialog and in-flight tasks viewer with cooperative cancellation. |
| **S5** | **Capability Routing & Verifier Matrix** | ✅ DONE | `PeerCapabilityCard`, hard requirements filtering, advisory placement scoring, operator pin override (`core/peer/capabilities.py`), global and per-node concurrency bounding (`ReservationLedger`) actively wired into `POST /tasks` admission, dual-node read-only verification matrix (`build_verifier_task` and `PeerService.build_verification_request`), and dynamic candidate peer selection wired into `RemoteAgentAdapter`. |

---

## 2. Completed & Remediated Components

### Core Cryptography, Identity & Pairing (`core/peer/`)
- [x] **`core/peer/crypto.py`** — Ed25519 keypair generation, sign/verify; X25519 ephemeral ECDH; HKDF-SHA256 6-digit SAS & AES-256-GCM derivation; SPKI SHA-256 hashing; real `PinnedHTTPSConnection.connect()` and `build_pinned_opener`.
- [x] **`core/peer/identity.py`** — DPAPI user-scoped node key storage (`%LOCALAPPDATA%\BrahmaAI\identity\node.json`) with machine entropy; `node_id` base32 calculation; machine-binding tripwire (`MachineGuid` + hostname); 90-day self-signed X.509 certificate generator with SPIFFE and DNS SANs.
- [x] **`core/peer/pairing.py`** — Out-of-band 6-digit SAS pairing protocol; `PairingOffer` with URI timestamp encoding and 120s TTL check; nonce replay check in callee exchange; 3-failed-attempts lockout (5 min); `PeerRegistry` enforcing NO TOFU, `list_peers()` helper; AES-256-GCM token payload encryption. Accepts `Path | str` without raising `AttributeError`. `PinnedPeer` carries `workspaces` and `labels` for capability matching.

### Content-Addressed Artifact Lane (`core/peer/artifacts.py` & `brahma_api/peer.py`)
- [x] **`core/peer/artifacts.py`** — Content-addressed store (`%LOCALAPPDATA%\BrahmaAI\peer\artifacts\<task_id>\<digest>.bin`), task-scoped `manifest.jsonl`, SHA-256 integrity verification on read, 32 MiB size cap (`MAX_ARTIFACT_BYTES`), filename sanitization preventing directory traversal. Constructor accepts both `base_dir` and `root_dir` aliases.
- [x] **Artifact HTTP Routes (`brahma_api/peer.py`)** —
  - `POST /tasks/{task_id}/artifacts`: Uploads raw bytes, verifies origin authorization, stores artifact, returns HTTP 201 Created. Mutating action symmetry enforced: unsigned uploads for paired peer tasks return HTTP 401 `signature_required`.
  - `GET /tasks/{task_id}/artifacts/{artifact_id}`: Streams raw bytes with `X-Brahma-Artifact-SHA256` and `Content-Disposition` attachment headers; enforces strict origin isolation (unbound tokens receive HTTP 403 `origin_mismatch`).
  - `GET /tasks/{task_id}/artifacts`: Returns list of artifact metadata records for task.

### Capability Scheduling, Concurrency & Verifier Matrix (`core/peer/capabilities.py` & `core/peer/scheduler.py`)
- [x] **`core/peer/capabilities.py`** — `PeerCapabilityCard` dataclass, hard requirement filtering (`matches_requirements`: profile hierarchy, workspace alias, required labels, required verbs, capacity bounds), advisory placement scoring (`score_peer`: load balancing, preferred labels, affinity), operator pin override (`select_best_peer`).
- [x] **`core/peer/scheduler.py`** — `ReservationLedger` tracking global and per-node concurrency limits; `build_verifier_task` implementing Idea I-08 (dual-node independent verification, read-only profile, untrusted output delimiters).
- [x] **Admission Wiring (`brahma_api/peer.py`)** — `POST /tasks` checks `ReservationLedger.acquire(origin, task_id)` on admission; returns HTTP 503 `concurrency_limit_exceeded` when concurrency limit is reached; releases reservation in worker `finally:` or on task cancellation.

### Discovery, Service Lifecycle & UI Seam (`core/peer/discovery.py`, `core/peer/service.py`, `core/ui_peer.py`)
- [x] **`core/peer/discovery.py`** — UDP broadcast beacon & listener on port 8444 for `_brahma-peer._tcp.local`; advisory opt-in discovery without auto-trust (Anti-Pattern A-06); self-filtering via `self_node_id` prevents loopback clutter.
- [x] **`core/peer/service.py`** — `PeerService` lifecycle coordinator, discovery manager, persistent `coordinator` with `NonceTracker` and `LockoutTracker`, `pending_offers` map, thread-locked active service accessors (`get_active_peer_service`, `set_active_peer_service`), drain mode toggle (`drain_mode`), structured status summary (`get_status_summary`), `select_candidate_peer` with workspace and label routing, and `build_verification_request`.
- [x] **`brahma_api/server.py` & `brahma_api/__main__.py`** — Hermetic resolution of `PeerService` in `create_app` against `config.base_dir / "peer"` with `base_dir=base_peer_dir / "artifacts"`; auto-registration with `set_active_peer_service`; default endpoint scheme aligned to `http://`; `--enable-peer-discovery` flag in `cmd_serve` with loud failure (exit 2) if service fails to start.
- [x] **`core/ui_peer.py` & `core/ui_connectivity.py`** — PyQt6 `PeerManagementWidget` embedded into System Connectivity page in Settings Hub via `build_peer_management_card`:
  - Local node identity & SPKI SHA-256 fingerprint card.
  - Automatic injection of active running `PeerService`.
  - Drain mode toggle switch with visual status indicators.
  - Pinned trusted peers list with unpin action.
  - Discovered LAN peers list with SAS pairing trigger calling `PairingCoordinator` and automated HTTP `POST /pair/exchange` handshake.
  - Single-use Pairing Offer generator dialog (`_create_pairing_offer_dialog`).
  - Total removal of fabricated-token (`tok-peer-...`) and arbitrary key manual fallbacks.
  - In-flight tasks viewer displaying active tasks with real-time cancel action.

### Task Server & Engine (`brahma_api/` & `core/peer/`)
- [x] **`core/peer/model.py`** — BTE-1 request, result, error dataclasses; canonical SHA-256 body hashing; size constraints.
- [x] **`core/peer/state.py`** — Wire state machine (`submitted`, `working`, `completed`, `failed`, `canceled`, `rejected`, `unknown`), transition rules, `is_retry_permitted` (Invariants S-05 & S-07).
- [x] **`core/peer/store.py`** — Thread-safe isolated SQLite task database, unique `(origin_node, task_id)` idempotency constraint, `token_id` column persistence for creator authorization, lease expiry queries, `reconcile_expired_leases(exclude_task_ids=...)`, and `list_active_tasks()`.
- [x] **`core/peer/recursion.py`** — Origin node check, hop counter, trace cycle detection, and leaf verification.
- [x] **`brahma_api/errors.py`** — Standard `ApiError` mapping; `unavailable(message, *, code="provider_unavailable")` supports custom codes including `peer_draining` and `concurrency_limit_exceeded`.
- [x] **`brahma_api/peer.py`** — FastAPI router with:
  - **Pairing protocol:** `POST /pair/offer` strictly requiring authenticated principal (`require_any("admin", "approve", "operator", "chat")`) and tracking issued offers in `pending_offers_map`; `POST /pair/exchange` validating issued nonce, consuming once, enforcing persistent nonce/lockout trackers, rejecting `profile="operator"` (HTTP 403 `forbidden_peer_profile`), restricting minted peer tokens strictly to `peer:*` scopes (preventing model surface auth bypass on `/v1/chat/completions`), storing callee `token=""`, and pinning static public key (`static_pub_raw`).
  - **Airtight origin isolation:** Creator token ID stored; unbound tokens cannot access or cancel tasks by spoofing `X-Brahma-Node` (HTTP 403 `origin_mismatch`).
  - **Workspace binding enforcement:** Reject cross-workspace task execution on bound tokens (HTTP 403 `insufficient_scope`).
  - **Active heartbeat:** Background daemon thread renews working lease every 5s (30s TTL).
  - **Watchdog sentinel:** Preserves `wall_clock_s = 0.0` as unbounded.
  - **Adapter interop:** Creator tokens can query status without `X-Brahma-Node` (HTTP 200).
  - **A2A chat token support:** Clean fallback for workspace and chat profile.
  - **Signature verification:** Enforced for paired peers on all mutating routes (creation, cooperative cancellation, and artifact upload). All rejection branches return HTTP 401 with standard error codes.
  - **Cancel-route signature symmetry:** Cooperative cancellation of paired peer tasks strictly requires Ed25519 signature.
  - **Sync 500 failure contract:** Failed sync tasks return HTTP 500 with structured error.
- [x] **`brahma_api/runner.py`** — Active task registry (`is_active`, `active_task_ids`, `cancel_active_task`), max turn budget and `max_tool_calls` propagation to loop.
- [x] **`brahma_api/__main__.py`** — CLI `token mint` supports `--node-id` and `--spki-sha256` for peer federation tokens.
- [x] **`core/agent_bridge/adapters/remote.py`** — Pinned SPKI TLS support, Ed25519 request signing, auto-detection of paired/pinned peers (`_sign_requests = True`), dynamic candidate peer selection via `svc.select_candidate_peer()` when endpoint is unset, automatic `PeerRegistry` endpoint scanning on custom or default paths, registry token fallback, automatic `BrahmaTaskRequest` envelope construction, `X-Brahma-Node` header injection, dual `state` / `status` polling, and `fetch_artifact` verb support.

### Harness Parallel Scheduling (`agent/loop.py`)
- [x] **`agent/loop.py`** — Invariant S-03 reachable in production:
  - Real `RemoteAgentAdapter` specs with `tool_class="network"`: read-only query verbs produce `("peer", Path("//peer/<node>"))`, allowing concurrent parallel fan-out across distinct peer nodes.
  - Mutating peer calls produce `None` (strictly fenced).
  - Normal reads/writes keep model order on conflicts.

---

## 3. Verification & Safety Invariants Log

| Invariant | Description | Verification Test | Status |
| :--- | :--- | :--- | :---: |
| **S-01** | Peer task runs strictly through callee `AgentRuntime` | `tests/test_peer_server.py::test_post_tasks_sync_execution_and_leaf_invariants` | ✅ Verified |
| **S-02** | Worker tool surface is a leaf (excludes peer & subagent tools) | `tests/test_peer_server.py::test_post_tasks_sync_execution_and_leaf_invariants` | ✅ Verified |
| **S-03** | Peer calls fenced from local tools; multi-peer reads overlap | `tests/test_agent_peer_fence.py`, `tests/test_peer_three_nodes.py` | ✅ Verified |
| **S-04** | Duplicate `(origin_node, task_id)` returns existing task | `tests/test_peer_server.py::test_post_tasks_idempotency_s04` | ✅ Verified |
| **S-05** | Mutations at-most-once; retry only if unstarted | `tests/test_peer_lease.py::test_s05_and_s07_mutation_retry_rules` | ✅ Verified |
| **S-06** | Terminal task states are immutable | `tests/test_agent_peer_model.py::test_wire_state_machine_transitions` | ✅ Verified |
| **S-07** | Lease expiry produces `unknown`, never silent mutation retry | `tests/test_peer_lease.py::test_lease_expiry_and_reconciliation_to_unknown` | ✅ Verified |
| **S-08** | Recursion refusal on `hop > max_hops` or `origin_node == self` | `tests/test_peer_server.py::test_post_tasks_recursion_denied`, `tests/test_peer_three_nodes.py` | ✅ Verified |
| **S-09** | Cross-origin task & artifact isolation (unbound tokens cannot spoof origin) | `tests/test_peer_server.py::test_cross_origin_task_isolation`, `tests/test_peer_server.py::test_cross_token_origin_spoofing_prevented`, `tests/test_peer_auth.py::test_signature_rejection_branches_return_401`, `tests/test_peer_artifacts.py::test_peer_artifact_routes_scope_isolation` | ✅ Verified |
| **S-10** | Workspace boundary binding on peer lane | `tests/test_peer_server.py::test_peer_workspace_binding_enforced` | ✅ Verified |

---

## 4. Test Suite Summary

- `tests/test_agent_peer_model.py`: 6 passed
- `tests/test_peer_server.py`: 20 passed (including legacy DB migration, colliding ID origin resolution, cross-token spoofing prevention, mutual cryptographic SAS pairing handshake with static key pinning, and hermetic custom `base_dir` auto-resolution with `ReservationLedger` limits)
- `tests/test_peer_auth.py`: 6 passed (including all 8 Ed25519 signature rejection branches returning HTTP 401, paired peer cancel signature enforcement, and paired peer artifact upload signature enforcement)
- `tests/test_peer_tls.py`: 3 passed (real `connect()` verification)
- `tests/test_agent_peer_fence.py`: 3 passed (including real `RemoteAgentAdapter` specs)
- `tests/test_peer_three_nodes.py`: 1 passed (full 3-node simulation)
- `tests/test_peer_lease.py`: 4 passed (S-05/S-07 lease expiry and mutation retry gates)
- `tests/test_peer_artifacts.py`: 3 passed (content-addressed storage, 32 MiB size cap, route scope isolation)
- `tests/test_peer_capabilities.py`: 6 passed (capability matching, advisory ranking, concurrency bounds, verifier matrix, HTTP admission ledger check, and `build_verification_request`)
- `tests/test_peer_discovery.py`: 4 passed (loopback beacon broadcast, listener discovery, invalid packet filtering, and loopback self-filtering)
- `tests/test_peer_service.py`: 2 passed (service lifecycle, drain mode HTTP 503 enforcement)
- `tests/test_peer_ui.py`: 1 passed (PyQt6 `PeerManagementWidget` layout, active task cancellation, data refresh, drain toggle, and active service singleton auto-binding)
- `tests/test_agent_remote_adapter.py`: 11 passed (including auto-signing for paired/pinned peers, on-disk registry endpoint scan, dynamic candidate peer selection from active `PeerService` with workspaces and labels, registry token fallback, and artifact fetching)
- **Total Peer Suite (`pytest -k peer`):** 61 passed
- **Total Combined Peer & Remote Adapter Tests:** 70 passed
- **Harness Full Suite (`pytest -k agent`):** 628 passed, 3 skipped, clean

---

## 5. Environment & Quality Checkpoints

- **Python Version:** 3.12 (virtual env active; `pytest` running clean)
- **Ruff Check:** Clean (0 violations across entire repository)
- **Line Count Ratchets:**
  - `main.py`: 3239 <= 3265 (Clean, untouched)
  - `ui.py`: 820 <= 827 (Clean, untouched)
- **Harness Integrity:** `agent/` remains 100% stdlib-only.
