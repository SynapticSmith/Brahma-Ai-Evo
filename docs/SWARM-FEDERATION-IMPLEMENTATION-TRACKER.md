# Brahma Swarm / Federation — Implementation Progress Tracker

**Document Authority:** `docs/SWARM-FEDERATION-DEEP-DIVE.md` & `docs/SWARM-FEDERATION-INVESTIGATION.md`  
**Target:** 3-node L2 peer task federation with capability scheduling, pinned mutual identity, at-most-once mutations, and zero shared SQLite databases.  
**Last Updated:** 2026-11-10 (Third-Pass Remediation & Full Verification Complete)  

---

## 1. Master Phase Tracker

| Phase | Description | Status | Gate Criteria & Honest State |
| :--- | :--- | :---: | :--- |
| **S0** | **Contracts & Architecture** | ✅ DONE | BTE-1 wire schema, ADR-030..038, threat model, state machine. Docs aligned as Proposed in `ARCHITECTURE-SPEC.md` and `USER-GUIDE.md`. |
| **S0.5** | **Cryptographic Primitives** | ✅ DONE | Ed25519 node key, X25519 + SAS derivation, SPKI pinning, request signing (`core/peer/crypto.py`). |
| **S1** | **API Transport & Adapter** | ✅ DONE | `RemoteAgentAdapter` constructs BTE-1 requests, sends `X-Brahma-Node`, polls `state` or `status`, pinned SPKI via `build_pinned_opener`, auto-signs when paired/pinned. |
| **S2** | **Core Peer Engine & Task Server** | ✅ DONE | `core/peer/store.py` with `token_id` storage, DB schema auto-migration, `brahma_api/peer.py`, airtight origin isolation, workspace binding enforcement, active 5s heartbeat thread, sync 500 error contract, budget clamping, cancel race resolution. |
| **S2.5** | **A2A v1.0 Binding** | ✅ DONE | `/.well-known/agent-card.json`, `POST /message:send`, `POST /tasks/{id}:cancel`, chat-profile token fallback verified. |
| **S3** | **Identity, SAS Pairing & Fencing** | ⏳ IN PROGRESS | NodeIdentity auto-loaded, SAS pairing with replay prevention, pinned TLS, all 8 signature rejection branches return 401 with standard error codes, Invariant S-03 fan-out reachable in production. Physical multi-machine cross-node validation pending. |
| **S4** | **Discovery & UI** | ⏳ PENDING | mDNS / DNS-SD discovery (`_brahma-peer._tcp`), Settings Hub peer pairing card (design complete in deep dive). |
| **S5** | **Capability Routing & Fan-out** | 📐 DESIGN ONLY | Label matching, verifier matrix, capability scheduling, bank/queue limits remain designed in deep dive. |

---

## 2. Completed & Remediated Components

### Core Cryptography & Identity (`core/peer/`)
- [x] **`core/peer/crypto.py`** — Ed25519 keypair generation, sign/verify; X25519 ephemeral ECDH; HKDF-SHA256 6-digit SAS & AES-256-GCM derivation; SPKI SHA-256 hashing; real `PinnedHTTPSConnection.connect()` and `build_pinned_opener`.
- [x] **`core/peer/identity.py`** — DPAPI user-scoped node key storage (`%LOCALAPPDATA%\BrahmaAI\identity\node.json`) with machine entropy; `node_id` base32 calculation; machine-binding tripwire (`MachineGuid` + hostname); 90-day self-signed X.509 certificate generator with SPIFFE and DNS SANs.
- [x] **`core/peer/pairing.py`** — Out-of-band 6-digit SAS pairing protocol; `PairingOffer` with URI timestamp encoding and 120s TTL check; nonce replay check in callee exchange; 3-failed-attempts lockout (5 min); `PeerRegistry` enforcing NO TOFU, `list_peers()` helper; AES-256-GCM token payload encryption.

### Task Server & Engine (`brahma_api/` & `core/peer/`)
- [x] **`core/peer/model.py`** — BTE-1 request, result, error dataclasses; canonical SHA-256 body hashing; size constraints.
- [x] **`core/peer/state.py`** — Wire state machine (`submitted`, `working`, `completed`, `failed`, `canceled`, `rejected`, `unknown`), transition rules, `is_retry_permitted` (Invariants S-05 & S-07).
- [x] **`core/peer/store.py`** — Thread-safe isolated SQLite task database, unique `(origin_node, task_id)` idempotency constraint, `token_id` column persistence for creator authorization, lease expiry queries, and `reconcile_expired_leases(exclude_task_ids=...)`.
  - **Schema migration:** Splits table and index DDL, automatically applies `ALTER TABLE ADD COLUMN` for missing columns before executing index DDL on legacy databases.
  - **Creator-preferred lookup:** `get_task_by_id(task_id, caller_token_id=None)` resolves the caller's row on colliding task IDs across origins.
- [x] **`core/peer/recursion.py`** — Origin node check, hop counter, trace cycle detection, and leaf verification.
- [x] **`brahma_api/errors.py`** — `unauthorized(message, *, code="invalid_api_key")` accepts standard code keyword argument matching forbidden, invalid, and not_found.
- [x] **`brahma_api/peer.py`** — FastAPI router with:
  - **Airtight origin isolation:** Creator token ID stored; unbound tokens cannot access or cancel tasks by spoofing `X-Brahma-Node` (HTTP 403 `origin_mismatch`).
  - **Workspace binding enforcement:** Reject cross-workspace task execution on bound tokens (HTTP 403 `insufficient_scope`).
  - **Active heartbeat:** Background daemon thread renews working lease every 5s (30s TTL).
  - **Watchdog sentinel:** Preserves `wall_clock_s = 0.0` as unbounded (no premature 1.0s clamp).
  - **Adapter interop:** Creator tokens can query status without `X-Brahma-Node` (HTTP 200).
  - **A2A chat token support:** Clean fallback for workspace and chat profile.
  - **Signature verification:** Enforced for paired peers on all mutating routes (creation and cooperative cancellation), validated when present. All 8 rejection branches return HTTP 401 with standard error codes (`signature_required`, `missing_signature_headers`, `invalid_timestamp`, `signature_expired`, `unregistered_peer`, `invalid_signature`, `nonce_replayed`).
  - **Cancel-route signature symmetry:** Cooperative cancellation of paired peer tasks strictly requires Ed25519 signature, preventing token-theft disruption. *(Deliberate architectural decision: A task whose origin is a paired peer can only be canceled with that peer's cryptographic signature — even the creator token is refused unsigned — meaning a local operator cannot cancel a stuck remote-peer task without acting from the peer machine.)*
  - **Endpoint scan string path coercion:** `PeerRegistry` accepts `Path | str`, properly resolving string paths from JSON configs/detectors without raising `AttributeError`.
  - **Sync 500 failure contract:** Failed sync tasks return HTTP 500 with structured error.
- [x] **`brahma_api/runner.py`** — Active task registry (`is_active`, `active_task_ids`, `cancel_active_task`), max turn budget and `max_tool_calls` propagation to loop.
- [x] **`brahma_api/__main__.py`** — CLI `token mint` supports `--node-id` and `--spki-sha256` for peer federation tokens, outputting `node_id` and `spki_sha256` in JSON format.
- [x] **`core/agent_bridge/adapters/remote.py`** — Pinned SPKI TLS support, Ed25519 request signing, auto-detection of paired/pinned peers (`_sign_requests = True`), automatic `PeerRegistry` endpoint scanning on custom or default paths, automatic `BrahmaTaskRequest` envelope construction, `X-Brahma-Node` header injection, dual `state` / `status` polling.

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
| **S-09** | Cross-origin task isolation (unbound tokens cannot spoof origin) | `tests/test_peer_server.py::test_cross_origin_task_isolation`, `tests/test_peer_server.py::test_cross_token_origin_spoofing_prevented`, `tests/test_peer_auth.py::test_signature_rejection_branches_return_401` | ✅ Verified |
| **S-10** | Workspace boundary binding on peer lane | `tests/test_peer_server.py::test_peer_workspace_binding_enforced` | ✅ Verified |

---

## 4. Test Suite Summary

- `tests/test_agent_peer_model.py`: 6 passed
- `tests/test_peer_server.py`: 18 passed (including legacy DB migration, airtight colliding ID origin verification, cross-token spoofing prevention)
- `tests/test_peer_auth.py`: 6 passed (including all 8 Ed25519 signature rejection branches returning HTTP 401, plus paired peer cancel signature enforcement)
- `tests/test_peer_tls.py`: 3 passed (real `connect()` verification)
- `tests/test_agent_peer_fence.py`: 3 passed (including real `RemoteAgentAdapter` specs)
- `tests/test_peer_three_nodes.py`: 1 passed (full 3-node simulation)
- `tests/test_peer_lease.py`: 4 passed (S-05/S-07 lease expiry and mutation retry gates)
- `tests/test_agent_remote_adapter.py`: 10 passed (including auto-signing for paired/pinned peers and on-disk registry endpoint scan)
- **Total Peer Suite (`pytest -k peer`):** 42 passed
- **Total Peer Federation Tests:** 51 passed
- **Harness Full Suite:** Clean (`pytest -k "peer or agent"`)

---

## 5. Environment & Quality Checkpoints

- **Python Version:** 3.11 / 3.12 (virtual env active; `pytest` running clean)
- **Ruff Check:** Clean (0 violations across entire repository)
- **Line Count Ratchets:**
  - `main.py`: 3239 <= 3265 (Clean)
  - `ui.py`: 820 <= 827 (Clean)
- **Harness Integrity:** `agent/` remains 100% stdlib-only.
