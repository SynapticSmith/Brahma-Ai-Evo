"""FastAPI router for BTE-1 peer tasks (ADR-030, Invariants S-01, S-02, S-04, S-08)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple
import urllib.parse
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from agent.spec import all_specs
from core.peer.artifacts import PeerArtifactStore
from core.peer.crypto import (
    ed25519_public_from_raw,
    verify_request_signature,
)
from core.peer.model import (
    BTE_VERSION,
    BrahmaTaskError,
    BrahmaTaskRequest,
    BrahmaTaskResult,
    now_iso,
)
from core.peer.pairing import (
    NonceTracker,
    PairingCoordinator,
    PairingOffer,
    PeerRegistry,
    PinnedPeer,
    prune_pending_offers,
)
from core.peer.recursion import (
    RecursionDeniedError,
    check_recursion,
    filter_leaf_tools,
)
from core.peer.state import WireState
from core.peer.store import (
    IdempotencyConflictError,
    PeerTaskStore,
)

from .auth import Principal, TokenRegistry
from .config import BrahmaApiConfig
from .errors import conflict, forbidden, invalid, not_found, unauthorized, unavailable
from .models import ResolvedModel, _intersect
from .runner import AgentRunner
from .translate import Seed

logger = logging.getLogger(__name__)


BTE_TO_A2A_STATE = {
    "submitted": "TASK_STATE_SUBMITTED",
    "working": "TASK_STATE_WORKING",
    "input_required": "TASK_STATE_INPUT_REQUIRED",
    "auth_required": "TASK_STATE_AUTH_REQUIRED",
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "canceled": "TASK_STATE_CANCELED",
    "rejected": "TASK_STATE_REJECTED",
    "unknown": "TASK_STATE_UNKNOWN",
}


def build_peer_router(
    config: BrahmaApiConfig,
    tokens: TokenRegistry,
    agent_runner: AgentRunner,
    store: Optional[PeerTaskStore] = None,
    self_node_id: Optional[str] = None,
    identity: Optional[Any] = None,
    peer_registry: Optional[Any] = None,
    artifact_store: Optional[Any] = None,
    peer_service: Optional[Any] = None,
) -> APIRouter:
    router = APIRouter(tags=["peer"])
    task_store = store or PeerTaskStore()
    resolved_ident = identity or (peer_service.identity if peer_service else None)
    if resolved_ident is None:
        try:
            from core.peer.identity import NodeIdentity

            resolved_ident = NodeIdentity.load_or_create(enforce_machine_binding=False)
        except Exception:
            resolved_ident = None
    node_id = (resolved_ident.node_id if resolved_ident else (self_node_id or config.host)).strip().lower()
    spki_sha256 = resolved_ident.spki_sha256 if resolved_ident else getattr(config, "spki_sha256", "")
    peers = peer_registry or PeerRegistry()
    nonce_tracker = NonceTracker()
    default_artifact_store = artifact_store or PeerArtifactStore()
    service = peer_service

    persistent_coord = getattr(service, "coordinator", None)
    if persistent_coord is None and resolved_ident:
        persistent_coord = PairingCoordinator(resolved_ident, peers)
    pending_offers_map = getattr(service, "pending_offers", None)
    if pending_offers_map is None:
        pending_offers_map = {}

    def _announced_endpoint(req: Optional[Request] = None) -> str:
        """The endpoint a peer can dial, never a wildcard bind address.

        ``config.peer_endpoint`` already resolves a wildcard bind, but a
        ``PeerService`` can also be constructed directly with one; normalising
        here keeps the A2A card, the pairing offer, the beacon and the UI on
        the same answer (ADR-034, S-07).
        """
        from core.peer.endpoint import announced_host as _resolve_host
        from core.peer.endpoint import format_host, is_wildcard_host

        active_svc = (getattr(getattr(req, "app", None), "state", None) and getattr(req.app.state, "peer_service", None)) or service
        raw = (
            active_svc.endpoint
            if active_svc and getattr(active_svc, "endpoint", None)
            else getattr(config, "peer_endpoint", f"http://{config.host}:{config.port}")
        )
        parsed = urllib.parse.urlparse(str(raw))
        if parsed.hostname and not is_wildcard_host(parsed.hostname):
            return str(raw)
        host = getattr(config, "advertised_host", None) or _resolve_host(
            getattr(config, "host", ""), getattr(config, "announced_host", None)
        )
        port = parsed.port or getattr(config, "peer_port", getattr(config, "port", 8010))
        scheme = parsed.scheme or ("https" if getattr(config, "peer_tls", True) else "http")
        return f"{scheme}://{format_host(host)}:{port}"

    def _announced_host_port(req: Optional[Request] = None) -> Tuple[str, int]:
        from core.peer.endpoint import announced_host as _resolve_host

        ep = _announced_endpoint(req)
        parsed = urllib.parse.urlparse(ep)
        h = parsed.hostname or _resolve_host(
            getattr(config, "host", ""), getattr(config, "announced_host", None)
        )
        p = parsed.port or getattr(config, "peer_port", getattr(config, "port", 8010))
        return h, p

    def principal_for(authorization: Optional[str] = Header(default=None)) -> Principal:
        if not config.auth_required:
            from .server import _anonymous
            return _anonymous(config)
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise unauthorized("Missing bearer token. Send Authorization: Bearer <token>.")
        tokens.reload_if_changed()
        record = tokens.verify(value.strip())
        if record is None:
            raise unauthorized()
        tokens.touch(record.id)
        return Principal(record)

    def require_any(*scopes: str) -> Callable[..., Principal]:
        def dependency(principal: Principal = Depends(principal_for)) -> Principal:
            if not any(principal.has_scope(s) for s in scopes):
                raise forbidden(
                    f"This key does not carry any of the required scopes: {scopes!r}",
                    code="insufficient_scope",
                )
            return principal
        return dependency

    def require_pairing_consent(principal: Principal = Depends(principal_for)) -> Principal:
        """Gating dependency for pairing consent: operator preset or administrative/pair scope."""
        if not config.auth_required:
            return principal
        has_admin_scope = principal.has_scope("admin") or principal.has_scope("pair")
        is_operator = principal.profile in ("operator", "admin")
        if not (has_admin_scope or is_operator):
            raise forbidden(
                f"Pairing consent requires operator preset or administrative/pair scope; key profile is {principal.profile!r}.",
                code="insufficient_scope",
            )
        return principal

    def _verify_signature_if_present(
        method: str,
        path: str,
        body_bytes: bytes,
        x_brahma_signature: Optional[str],
        x_brahma_timestamp: Optional[str],
        x_brahma_nonce: Optional[str],
        caller_node: str,
        *,
        must_sign: bool = False,
    ) -> bool:
        """Verify Ed25519 request signature if present, or enforce for registered peers."""
        peer = peers.get_peer(caller_node)
        is_paired_peer = bool(peer is not None and peer.static_pub_raw)
        if not x_brahma_signature:
            if must_sign or is_paired_peer:
                raise unauthorized(
                    f"Sender node '{caller_node}' is a registered paired peer and must sign requests with X-Brahma-Signature.",
                    code="signature_required",
                )
            return False

        if not x_brahma_timestamp or not x_brahma_nonce:
            raise unauthorized(
                "X-Brahma-Signature requires X-Brahma-Timestamp and X-Brahma-Nonce.",
                code="missing_signature_headers",
            )

        try:
            ts = float(x_brahma_timestamp)
        except ValueError:
            raise unauthorized("Invalid X-Brahma-Timestamp", code="invalid_timestamp")

        if abs(time.time() - ts) > 300.0:
            raise unauthorized("Request signature expired or timestamp skewed > 300s", code="signature_expired")

        if not nonce_tracker.check_and_record(x_brahma_nonce):
            raise unauthorized(f"Request nonce '{x_brahma_nonce}' replayed", code="nonce_replayed")

        if peer is None or not peer.static_pub_raw:
            raise unauthorized(f"Sender node '{caller_node}' is not a registered pinned peer", code="unregistered_peer")

        try:
            pub_bytes = base64.b64decode(peer.static_pub_raw)
            pub_key = ed25519_public_from_raw(pub_bytes)
            valid = verify_request_signature(
                public_key=pub_key,
                signature_b64=x_brahma_signature,
                method=method,
                path=path,
                body=body_bytes,
                timestamp=x_brahma_timestamp,
                nonce=x_brahma_nonce,
                node_id=caller_node,
            )
            if not valid:
                raise unauthorized("Invalid X-Brahma-Signature", code="invalid_signature")
            return True
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            raise unauthorized(f"Signature verification error: {exc}", code="invalid_signature") from exc

    def _check_task_authorization(
        principal: Principal,
        row: Dict[str, Any],
        bound_node: str,
        raw_sig: Optional[str],
        request: Request,
        raw_ts: Optional[str],
        raw_nonce: Optional[str],
        body_bytes: bytes = b"",
        action_name: str = "access",
        *,
        signature_verified: bool = False,
    ) -> None:
        """Enforce origin isolation: creator token, bound token, or cryptographically signed peer.

        ``signature_verified`` is set when the caller already verified this
        request's signature earlier in the same handler. Re-verifying would
        record the nonce a second time and reject an idempotent retry as a
        replay (ADR-037 / Invariant S-04).
        """
        if not config.auth_required:
            return
        caller_tok_id = getattr(principal.token, "id", None) if getattr(principal, "token", None) else None
        stored_tok_id = row.get("token_id")

        is_creator = bool(caller_tok_id and stored_tok_id and caller_tok_id == stored_tok_id)
        is_bound = bool(bound_node and bound_node == row["origin_node"].lower())
        is_signed = False

        peer = peers.get_peer(row["origin_node"])
        is_paired_peer = bool(peer is not None and peer.static_pub_raw)
        is_mutating = action_name in ("cancel", "upload_artifact")  # Task creation is verified at line 465
        enforce_signature = is_paired_peer and is_mutating

        if signature_verified:
            is_signed = True
        elif enforce_signature or raw_sig:
            is_signed = _verify_signature_if_present(
                method=request.method,
                path=request.url.path,
                body_bytes=body_bytes,
                x_brahma_signature=raw_sig,
                x_brahma_timestamp=raw_ts,
                x_brahma_nonce=raw_nonce,
                caller_node=row["origin_node"],
                must_sign=enforce_signature,
            )

        if stored_tok_id:
            if not (is_creator or is_bound or is_signed):
                raise forbidden(
                    f"Caller is not authorized to {action_name} tasks for origin {row['origin_node']!r}.",
                    code="origin_mismatch",
                )
        else:
            raw_x_node = request.headers.get("x-brahma-node") or request.query_params.get("origin_node")
            x_node = (raw_x_node or "").strip().lower()
            origin_match = bool(x_node and x_node == row["origin_node"].lower())
            if not (origin_match or is_bound or is_signed):
                raise forbidden(
                    f"Caller is not authorized to {action_name} tasks for origin {row['origin_node']!r}.",
                    code="origin_mismatch",
                )

    @router.get("/.well-known/agent.json")
    def agent_card() -> Dict[str, Any]:
        """BTE-1 Agent Card for discovery and capability advertising."""
        return {
            "schema": f"brahma.peer/{BTE_VERSION.split('.')[0]}",
            "node_id": node_id,
            "spki_sha256": spki_sha256,
            "name": getattr(config, "node_name", "Brahma Node"),
            "version": "1.4.0",
            "capabilities": ["os:windows"] + [f"repo:{k}" for k in config.workspaces.keys()],
            "verbs": ["run_task", "query_status", "cancel_task"],
            "limits": {
                "max_turns": config.limits.max_turns,
                "wall_clock_s": config.limits.wall_clock_s,
                "max_body_bytes": config.limits.max_body_bytes,
            },
            "profiles": ["reader", "worker"],
        }

    @router.get("/.well-known/agent-card.json")
    def a2a_agent_card(request: Request) -> Dict[str, Any]:
        """A2A v1.0 standard Agent Card specification endpoint."""
        ws_keys = list(config.workspaces.keys())
        skills = [
            {
                "id": f"repo-{k}",
                "name": f"Repository workspace '{k}'",
                "description": f"Access tools and context for workspace '{k}'",
                "tags": [f"repo:{k}", "profile:reader", "profile:worker"],
            }
            for k in ws_keys
        ]
        return {
            "protocolVersion": "1.0",
            "id": f"node-{node_id}",
            "name": getattr(config, "node_name", "Brahma Node"),
            "spki_sha256": spki_sha256,
            "description": "Brahma Peer Task Node (A2A v1.0 Binding).",
            "url": _announced_endpoint(request),
            "preferredTransport": "HTTP+JSON",
            "version": "1.4.0",
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "stateTransitionHistory": False,
                "extensions": [
                    {
                        "uri": "https://brahma.local/ext/request-signature/v1",
                        "description": "Mutating requests carry X-Brahma-Signature.",
                        "required": False,
                    }
                ],
            },
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "text/markdown", "application/json"],
            "skills": skills,
            "securitySchemes": {
                "brahmaBearer": {"type": "http", "scheme": "bearer"},
            },
            "security": [{"brahmaBearer": []}],
            "supportsAuthenticatedExtendedCard": True,
        }

    def _execute_task_in_background(
        task_req: BrahmaTaskRequest,
        principal: Principal,
        effective_profile: str,
        workspace_path: Any,
        store: PeerTaskStore,
    ) -> None:
        """Execute a BTE task inside a worker thread using AgentRunner."""
        task_start_time = now_iso()
        stop_heartbeat = threading.Event()

        def _heartbeat_worker() -> None:
            while not stop_heartbeat.wait(5.0):
                try:
                    store.heartbeat(task_req.origin_node, task_req.task_id, lease_ttl_s=30.0)
                except Exception:
                    pass

        hb_thread = threading.Thread(target=_heartbeat_worker, daemon=True)
        hb_thread.start()

        try:
            store.update_state(
                task_req.origin_node,
                task_req.task_id,
                WireState.WORKING.value,
                started_at=task_start_time,
                lease_ttl_s=30.0,
            )

            # Invariant S-02: Worker loop surface must be strictly a leaf
            leaf_tools = filter_leaf_tools(all_specs())

            # Clamping limits against local server ceiling
            req_turns = int(task_req.budget.get("max_turns") or config.limits.max_turns)
            effective_max_turns = max(1, min(req_turns, config.limits.max_turns))

            req_tool_calls = task_req.budget.get("max_tool_calls")
            effective_max_tool_calls = (
                max(1, min(int(req_tool_calls), 50))
                if req_tool_calls is not None
                else None
            )

            req_deadline = float(task_req.deadline_s or 0.0)
            server_wall_clock = float(config.limits.wall_clock_s or 0.0)
            if server_wall_clock <= 0.0:
                effective_wall_clock = req_deadline if req_deadline > 0.0 else 0.0
            elif req_deadline > 0.0:
                effective_wall_clock = max(1.0, min(req_deadline, server_wall_clock))
            else:
                effective_wall_clock = server_wall_clock

            resolved = ResolvedModel(
                model_id="brahma-evo",
                lane="agent",
                profile=effective_profile,
                workspace_alias=task_req.workspace,
                workspace=workspace_path,
                tools=(effective_profile != "chat"),
                max_turns=effective_max_turns,
            )
            seed = Seed(prompt=task_req.prompt, directives=[], history=[])

            # Invariant S-01: Execution flows strictly through AgentRunner -> AgentRuntime.execute
            outcome = agent_runner.run(
                principal,
                resolved,
                seed,
                task_id=task_req.task_id,
                tools=leaf_tools if effective_profile != "chat" else [],
                max_turns=resolved.max_turns,
                wall_clock_s=effective_wall_clock,
                max_tool_calls=effective_max_tool_calls,
                approval_channel="deny",  # Peer runs deny-on-ask remotely
            )

            # Cancel race resolution: verify if cancel was requested during execution
            curr_row = store.get_task(task_req.origin_node, task_req.task_id) or {}
            is_canceled = (
                bool(curr_row.get("cancel_requested_at"))
                or curr_row.get("state") == WireState.CANCELED.value
                or getattr(outcome.result, "stop_reason", None) == "cancelled"
            )
            if is_canceled:
                terminal_state = WireState.CANCELED.value
            elif not outcome.result.ok:
                terminal_state = WireState.FAILED.value
            else:
                terminal_state = WireState.COMPLETED.value

            if terminal_state == WireState.FAILED.value:
                err = BrahmaTaskError(
                    code="execution_failed",
                    message=outcome.result.text or f"Execution failed: stop_reason={outcome.result.stop_reason!r}",
                    retryable=(task_req.effect in ("none", "idempotent")),
                )
                store.update_state(
                    task_req.origin_node,
                    task_req.task_id,
                    WireState.FAILED.value,
                    error_code=err.code,
                    error_json=json.dumps(err.to_dict()),
                    finished_at=now_iso(),
                )
                return

            result_obj = BrahmaTaskResult(
                task_id=task_req.task_id,
                state=terminal_state,
                started_at=task_start_time,
                finished_at=now_iso(),
                result={"text": outcome.result.text} if not is_canceled else None,
                usage={
                    "turns": outcome.result.turns,
                    "tool_calls": len(outcome.tool_calls or []),
                    "elapsed_s": outcome.elapsed_s,
                },
            )
            store.update_state(
                task_req.origin_node,
                task_req.task_id,
                terminal_state,
                result_json=json.dumps(result_obj.to_dict()),
                error_code="canceled" if is_canceled else None,
                finished_at=now_iso(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error executing peer task %s: %s", task_req.task_id, exc)
            err = BrahmaTaskError(
                code="execution_failed",
                message=str(exc),
                retryable=(task_req.effect in ("none", "idempotent")),
            )
            store.update_state(
                task_req.origin_node,
                task_req.task_id,
                WireState.FAILED.value,
                error_code=err.code,
                error_json=json.dumps(err.to_dict()),
                finished_at=now_iso(),
            )
        finally:
            stop_heartbeat.set()
            active_ledger = getattr(service, "ledger", None) if service else None
            if active_ledger is not None:
                active_ledger.release(task_req.task_id)

    @router.post("/tasks")
    async def create_task(
        request: Request,
        data: Dict[str, Any],
        x_brahma_node: Optional[str] = Header(default=None),
        x_brahma_signature: Optional[str] = Header(default=None),
        x_brahma_timestamp: Optional[str] = Header(default=None),
        x_brahma_nonce: Optional[str] = Header(default=None),
        principal: Principal = Depends(require_any("peer:run", "chat")),
    ) -> Any:
        """Create or adopt a BTE-1 task request."""
        active_store = getattr(request.app.state, "peer_store", None) or task_store

        # Invariant S-04 / ADR-037: Refuse new admissions if node is in drain/maintenance mode
        active_service = getattr(request.app.state, "peer_service", None) or peer_service
        if active_service and getattr(active_service, "drain_mode", False):
            raise unavailable("Node is in drain/maintenance mode.", code="peer_draining")

        raw_x_node = x_brahma_node if isinstance(x_brahma_node, str) else request.headers.get("x-brahma-node")
        raw_sig = x_brahma_signature if isinstance(x_brahma_signature, str) else request.headers.get("x-brahma-signature")
        raw_ts = x_brahma_timestamp if isinstance(x_brahma_timestamp, str) else request.headers.get("x-brahma-timestamp")
        raw_nonce = x_brahma_nonce if isinstance(x_brahma_nonce, str) else request.headers.get("x-brahma-nonce")

        # Origin isolation and Token Binding verification
        bound_node = (principal.token.node_id or "").strip().lower() if getattr(principal, "token", None) else ""
        x_node = (raw_x_node or "").strip().lower()
        if bound_node:
            if x_node and x_node != bound_node:
                raise forbidden("Caller token is bound to a different node_id.", code="origin_mismatch")

        try:
            task_req = BrahmaTaskRequest.from_dict(data)
        except ValueError as exc:
            raise invalid(str(exc)) from exc

        if bound_node and task_req.origin_node.strip().lower() != bound_node:
            raise forbidden("Caller token is bound to a different node_id.", code="origin_mismatch")
        if x_node and task_req.origin_node.strip().lower() != x_node:
            raise invalid("X-Brahma-Node does not match origin_node.", code="origin_mismatch")

        # Verify request signature if present (or enforce for paired peers)
        raw_body = await request.body()
        _verify_signature_if_present(
            method="POST",
            path=request.url.path,
            body_bytes=raw_body,
            x_brahma_signature=raw_sig,
            x_brahma_timestamp=raw_ts,
            x_brahma_nonce=raw_nonce,
            caller_node=task_req.origin_node,
        )

        # Invariant S-08: Recursion and origin check
        try:
            check_recursion(task_req, node_id, max_hops=0)
        except RecursionDeniedError as exc:
            raise invalid(str(exc), code="recursion_denied") from exc

        # Workspace verification: alias must exist in peer's config
        ws_path = config.workspace_path(task_req.workspace)
        if ws_path is None:
            raise invalid(
                f"unknown_workspace: workspace alias {task_req.workspace!r} is not configured on this node.",
                code="unknown_workspace",
            )

        # Workspace binding: if token is bound to a specific workspace, it cannot select another workspace!
        if principal.workspace and task_req.workspace != principal.workspace:
            raise forbidden(
                f"This key is bound to workspace '{principal.workspace}'; it cannot select '{task_req.workspace}'.",
                code="insufficient_scope",
            )

        # Profile ceiling check: requested profile cannot exceed token preset
        req_profile = str(data.get("profile") or "").strip().lower() if "profile" in data else principal.profile
        if not req_profile:
            req_profile = principal.profile
        effective_profile = _intersect(principal, req_profile)
        order = {"chat": 0, "reader": 1, "worker": 2, "operator": 3}
        if "profile" in data and order.get(req_profile, 0) > order.get(principal.profile, 0):
            raise forbidden(
                f"Token profile ceiling {principal.profile!r} cannot execute requested profile {req_profile!r}.",
                code="insufficient_profile",
            )

        # Idempotency check via (origin_node, task_id)
        caller_token_id = getattr(principal.token, "id", None) if getattr(principal, "token", None) else None
        try:
            is_existing, row = active_store.record_task(task_req, peer_node=node_id, token_id=caller_token_id)
        except IdempotencyConflictError as exc:
            raise conflict(str(exc), code="idempotency_conflict") from exc

        if is_existing:
            _check_task_authorization(
                principal,
                row,
                bound_node,
                raw_sig,
                request,
                raw_ts,
                raw_nonce,
                body_bytes=raw_body,
                action_name="access",
                # The creation path above already verified this request's
                # signature. Re-verifying here would record the nonce twice and
                # turn an idempotent retry into a nonce_replayed 401 (S-04).
                signature_verified=bool(raw_sig),
            )
            return JSONResponse(
                status_code=200,
                content={
                    "task_id": row["task_id"],
                    "state": row["state"],
                    "accepted_at": row["accepted_at"],
                    "result": json.loads(row["result_json"]) if row.get("result_json") else None,
                    "error": json.loads(row["error_json"]) if row.get("error_json") else None,
                },
            )

        # S5 Concurrency Bounding: acquire slot on ReservationLedger for new task
        active_ledger = getattr(active_service, "ledger", None) if active_service else None
        if active_ledger is not None:
            from core.peer.scheduler import ConcurrencyLimitExceededError
            try:
                active_ledger.acquire(task_req.origin_node, task_req.task_id)
            except ConcurrencyLimitExceededError as exc:
                with active_store._lock, active_store._connection() as conn:
                    conn.execute(
                        "DELETE FROM peer_tasks WHERE origin_node = ? AND task_id = ?",
                        (task_req.origin_node, task_req.task_id),
                    )
                    conn.commit()
                raise unavailable(str(exc), code="concurrency_limit_exceeded") from exc

        # Execution mode: sync or async
        reply_cfg = task_req.reply or {}
        is_sync = (
            reply_cfg.get("mode") == "sync"
            or reply_cfg.get("wait") is True
            or data.get("sync") is True
        )
        if is_sync:
            # Synchronous execution in worker thread
            await asyncio.to_thread(_execute_task_in_background, task_req, principal, effective_profile, ws_path, active_store)
            stored = active_store.get_task(task_req.origin_node, task_req.task_id) or {}
            st = stored.get("state", WireState.COMPLETED.value)
            if st == WireState.FAILED.value:
                err_data = json.loads(stored.get("error_json") or "{}") if stored.get("error_json") else {"code": stored.get("error_code") or "execution_failed"}
                return JSONResponse(
                    status_code=500,
                    content={
                        "task_id": stored.get("task_id", task_req.task_id),
                        "state": WireState.FAILED.value,
                        "error": err_data,
                    },
                )
            res_json = json.loads(stored.get("result_json") or "{}")
            if not res_json:
                res_json = {
                    "task_id": stored.get("task_id", task_req.task_id),
                    "state": st,
                    "result": None,
                }
            return JSONResponse(status_code=200, content=res_json)

        # Asynchronous execution (default 202 Accepted)
        worker_thread = threading.Thread(
            target=_execute_task_in_background,
            args=(task_req, principal, effective_profile, ws_path, active_store),
            daemon=True,
        )
        worker_thread.start()

        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_req.task_id,
                "state": WireState.SUBMITTED.value,
                "accepted_at": row["accepted_at"],
                "poll_after_s": 2,
            },
        )

    @router.get("/tasks/{task_id}")
    def get_task_status(
        task_id: str,
        request: Request,
        x_brahma_node: Optional[str] = Header(default=None),
        origin_node: Optional[str] = None,
        x_brahma_signature: Optional[str] = Header(default=None),
        x_brahma_timestamp: Optional[str] = Header(default=None),
        x_brahma_nonce: Optional[str] = Header(default=None),
        principal: Principal = Depends(require_any("peer:status", "peer:run", "chat")),
    ) -> Dict[str, Any]:
        """Query real-time status and result of a task."""
        active_store = getattr(request.app.state, "peer_store", None) or task_store

        # Invariant S-07: Reconcile expired leases on status checks (excluding locally active tasks)
        active_ids = set(agent_runner.active_task_ids())
        active_store.reconcile_expired_leases(exclude_task_ids=active_ids)

        raw_x_node = x_brahma_node if isinstance(x_brahma_node, str) else (request.headers.get("x-brahma-node") or origin_node)
        raw_sig = x_brahma_signature if isinstance(x_brahma_signature, str) else request.headers.get("x-brahma-signature")
        raw_ts = x_brahma_timestamp if isinstance(x_brahma_timestamp, str) else request.headers.get("x-brahma-timestamp")
        raw_nonce = x_brahma_nonce if isinstance(x_brahma_nonce, str) else request.headers.get("x-brahma-nonce")

        bound_node = (principal.token.node_id or "").strip().lower() if getattr(principal, "token", None) else ""
        x_node = (raw_x_node or "").strip().lower()

        caller_tok_id = getattr(principal.token, "id", None) if getattr(principal, "token", None) else None
        if bound_node:
            if x_node and x_node != bound_node:
                raise forbidden("Caller token is bound to a different node_id.", code="origin_mismatch")
            row = active_store.get_task(bound_node, task_id)
        elif x_node:
            row = active_store.get_task(x_node, task_id)
        else:
            # Origin header omitted: lookup by task_id alone (preferring caller_token_id on collision)
            row = active_store.get_task_by_id(task_id, caller_token_id=caller_tok_id)

        if row is None:
            caller_desc = x_node or bound_node or "caller"
            raise not_found(f"Task {task_id!r} not found for origin {caller_desc!r}.", code="task_not_found")

        _check_task_authorization(
            principal,
            row,
            bound_node,
            raw_sig,
            request,
            raw_ts,
            raw_nonce,
            body_bytes=b"",
            action_name="access",
        )

        result_payload = json.loads(row["result_json"]) if row.get("result_json") else None
        error_payload = json.loads(row["error_json"]) if row.get("error_json") else None

        return {
            "task_id": row["task_id"],
            "origin_node": row["origin_node"],
            "state": row["state"],
            "attempt": row["attempt"],
            "accepted_at": row["accepted_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "result": result_payload.get("result") if result_payload else None,
            "error": error_payload or ({"code": row["error_code"]} if row.get("error_code") else None),
            "artifacts": result_payload.get("artifacts", []) if result_payload else [],
            "usage": result_payload.get("usage", {}) if result_payload else {},
        }

    @router.post("/tasks/{task_id}/cancel")
    async def cancel_task(
        task_id: str,
        request: Request,
        x_brahma_node: Optional[str] = Header(default=None),
        origin_node: Optional[str] = None,
        x_brahma_signature: Optional[str] = Header(default=None),
        x_brahma_timestamp: Optional[str] = Header(default=None),
        x_brahma_nonce: Optional[str] = Header(default=None),
        principal: Principal = Depends(require_any("peer:cancel", "peer:run", "chat")),
    ) -> Dict[str, Any]:
        """Request cooperative task cancellation."""
        active_store = getattr(request.app.state, "peer_store", None) or task_store

        raw_x_node = x_brahma_node if isinstance(x_brahma_node, str) else (request.headers.get("x-brahma-node") or origin_node)
        raw_sig = x_brahma_signature if isinstance(x_brahma_signature, str) else request.headers.get("x-brahma-signature")
        raw_ts = x_brahma_timestamp if isinstance(x_brahma_timestamp, str) else request.headers.get("x-brahma-timestamp")
        raw_nonce = x_brahma_nonce if isinstance(x_brahma_nonce, str) else request.headers.get("x-brahma-nonce")

        bound_node = (principal.token.node_id or "").strip().lower() if getattr(principal, "token", None) else ""
        x_node = (raw_x_node or "").strip().lower()

        caller_tok_id = getattr(principal.token, "id", None) if getattr(principal, "token", None) else None
        if bound_node:
            if x_node and x_node != bound_node:
                raise forbidden("Caller token is bound to a different node_id.", code="origin_mismatch")
            row = active_store.get_task(bound_node, task_id)
        elif x_node:
            row = active_store.get_task(x_node, task_id)
        else:
            row = active_store.get_task_by_id(task_id, caller_token_id=caller_tok_id)

        if row is None:
            caller_desc = x_node or bound_node or "caller"
            raise not_found(f"Task {task_id!r} not found for origin {caller_desc!r}.", code="task_not_found")

        raw_body = await request.body()
        _check_task_authorization(
            principal,
            row,
            bound_node,
            raw_sig,
            request,
            raw_ts,
            raw_nonce,
            body_bytes=raw_body,
            action_name="cancel",
        )

        caller_origin = row["origin_node"]
        agent_runner.cancel_active_task(task_id)
        active_service = getattr(request.app.state, "peer_service", None) or service
        if active_service and getattr(active_service, "ledger", None):
            active_service.ledger.release(task_id)
        updated = active_store.request_cancel(caller_origin, task_id)
        if updated is None:
            raise not_found(f"Task {task_id!r} not found.", code="task_not_found")

        return {
            "task_id": updated["task_id"],
            "state": updated["state"],
            "cancel_requested_at": updated.get("cancel_requested_at"),
            "finished_at": updated.get("finished_at"),
        }

    @router.post("/tasks/{task_id}:cancel")
    async def a2a_cancel_task(
        task_id: str,
        request: Request,
        x_brahma_node: Optional[str] = Header(default=None),
        origin_node: Optional[str] = None,
        x_brahma_signature: Optional[str] = Header(default=None),
        x_brahma_timestamp: Optional[str] = Header(default=None),
        x_brahma_nonce: Optional[str] = Header(default=None),
        principal: Principal = Depends(require_any("peer:cancel", "peer:run", "chat")),
    ) -> Dict[str, Any]:
        """A2A v1.0 cancel task alias using colon verb."""
        res = await cancel_task(
            task_id=task_id,
            request=request,
            x_brahma_node=x_brahma_node,
            origin_node=origin_node,
            x_brahma_signature=x_brahma_signature,
            x_brahma_timestamp=x_brahma_timestamp,
            x_brahma_nonce=x_brahma_nonce,
            principal=principal,
        )
        bte_state = res.get("state", "canceled")
        return {
            "taskId": res["task_id"],
            "state": BTE_TO_A2A_STATE.get(bte_state, "TASK_STATE_CANCELED"),
            "canceledAt": res.get("finished_at") or res.get("cancel_requested_at"),
        }

    @router.post("/message:send")
    async def a2a_message_send(
        request: Request,
        a2a_version: Optional[str] = Header(default=None, alias="A2A-Version"),
        principal: Principal = Depends(require_any("peer:run", "chat")),
    ) -> Dict[str, Any]:
        """A2A v1.0 task execution endpoint."""
        if a2a_version and not a2a_version.startswith("1."):
            raise invalid(f"Unsupported A2A-Version: {a2a_version!r}. Supported: '1.0'", code="version_unsupported")

        try:
            body_json = await request.json()
        except Exception:
            raise invalid("Invalid JSON body", code="invalid_json")

        msg = body_json.get("message") or {}
        parts = msg.get("parts") or []
        prompt_text = ""
        for p in parts:
            if isinstance(p, dict) and "text" in p:
                prompt_text += p["text"] + "\n"
            elif isinstance(p, str):
                prompt_text += p + "\n"
        prompt_text = prompt_text.strip() or str(body_json.get("prompt") or "").strip()
        if not prompt_text:
            raise invalid("Missing prompt or text parts in A2A message", code="missing_prompt")

        ws_val = body_json.get("workspace") or principal.workspace
        if not ws_val or ws_val == "default":
            ws_val = next(iter(config.workspaces.keys())) if config.workspaces else "default"
        workspace_alias = str(ws_val)

        task_id = str(body_json.get("task_id") or uuid.uuid4())
        origin = str(body_json.get("origin_node") or "a2a-client")
        trace_id = str(body_json.get("trace_id") or uuid.uuid4())
        root_task_id = str(body_json.get("root_task_id") or task_id)
        coordinator_node = str(body_json.get("coordinator_node") or origin)

        bte_req = BrahmaTaskRequest(
            task_id=task_id,
            trace_id=trace_id,
            root_task_id=root_task_id,
            origin_node=origin,
            coordinator_node=coordinator_node,
            workspace=workspace_alias,
            prompt=prompt_text,
            profile="chat" if principal.profile == "chat" else principal.profile,
            reply={"mode": "sync"},
        )
        task_dict = bte_req.to_dict()
        task_dict["sync"] = True
        res = await create_task(
            request=request,
            data=task_dict,
            principal=principal,
        )
        if isinstance(res, JSONResponse):
            bte_data = json.loads(res.body.decode("utf-8"))
        elif isinstance(res, dict):
            bte_data = res
        else:
            bte_data = getattr(res, "__dict__", {})

        state = bte_data.get("state", "completed")
        res_text = ""
        if isinstance(bte_data.get("result"), dict):
            res_text = str(bte_data["result"].get("text", ""))
        else:
            res_text = str(bte_data.get("result") or "")

        return {
            "taskId": bte_data.get("task_id") or task_id,
            "state": BTE_TO_A2A_STATE.get(state, "TASK_STATE_COMPLETED"),
            "response": {
                "role": "agent",
                "parts": [{"text": res_text}],
            },
            "finishedAt": bte_data.get("finished_at"),
        }

    # -- Artifact Endpoints (ADR-038 & Invariant S-09) -----------------------

    @router.get("/tasks/{task_id}/artifacts")
    def list_task_artifacts(
        task_id: str,
        request: Request,
        x_brahma_node: Optional[str] = Header(default=None),
        origin_node: Optional[str] = None,
        x_brahma_signature: Optional[str] = Header(default=None),
        x_brahma_timestamp: Optional[str] = Header(default=None),
        x_brahma_nonce: Optional[str] = Header(default=None),
        principal: Principal = Depends(require_any("peer:run", "peer:status", "chat")),
    ) -> Dict[str, Any]:
        """List artifacts produced by or associated with a peer task."""
        active_store = getattr(request.app.state, "peer_store", None) or task_store
        active_artifacts = getattr(request.app.state, "artifact_store", None) or default_artifact_store

        raw_x_node = x_brahma_node if isinstance(x_brahma_node, str) else (request.headers.get("x-brahma-node") or origin_node)
        raw_sig = x_brahma_signature if isinstance(x_brahma_signature, str) else request.headers.get("x-brahma-signature")
        raw_ts = x_brahma_timestamp if isinstance(x_brahma_timestamp, str) else request.headers.get("x-brahma-timestamp")
        raw_nonce = x_brahma_nonce if isinstance(x_brahma_nonce, str) else request.headers.get("x-brahma-nonce")

        bound_node = (principal.token.node_id or "").strip().lower() if getattr(principal, "token", None) else ""
        x_node = (raw_x_node or "").strip().lower()
        caller_tok_id = getattr(principal.token, "id", None) if getattr(principal, "token", None) else None

        if bound_node:
            if x_node and x_node != bound_node:
                raise forbidden("Caller token is bound to a different node_id.", code="origin_mismatch")
            row = active_store.get_task(bound_node, task_id)
        elif x_node:
            row = active_store.get_task(x_node, task_id)
        else:
            row = active_store.get_task_by_id(task_id, caller_token_id=caller_tok_id)

        if row is None:
            caller_desc = x_node or bound_node or "caller"
            raise not_found(f"Task {task_id!r} not found for origin {caller_desc!r}.", code="task_not_found")

        _check_task_authorization(
            principal,
            row,
            bound_node,
            raw_sig,
            request,
            raw_ts,
            raw_nonce,
            body_bytes=b"",
            action_name="list_artifacts",
        )

        arts = active_artifacts.list_artifacts(task_id)
        return {
            "task_id": task_id,
            "artifacts": [a.to_dict() for a in arts],
        }

    @router.post("/tasks/{task_id}/artifacts")
    async def upload_task_artifact(
        task_id: str,
        request: Request,
        x_brahma_node: Optional[str] = Header(default=None),
        origin_node: Optional[str] = None,
        x_brahma_signature: Optional[str] = Header(default=None),
        x_brahma_timestamp: Optional[str] = Header(default=None),
        x_brahma_nonce: Optional[str] = Header(default=None),
        principal: Principal = Depends(require_any("peer:run", "chat")),
    ) -> Any:
        """Upload a raw content-addressed artifact for a task."""
        active_store = getattr(request.app.state, "peer_store", None) or task_store
        active_artifacts = getattr(request.app.state, "artifact_store", None) or default_artifact_store

        raw_x_node = x_brahma_node if isinstance(x_brahma_node, str) else (request.headers.get("x-brahma-node") or origin_node)
        raw_sig = x_brahma_signature if isinstance(x_brahma_signature, str) else request.headers.get("x-brahma-signature")
        raw_ts = x_brahma_timestamp if isinstance(x_brahma_timestamp, str) else request.headers.get("x-brahma-timestamp")
        raw_nonce = x_brahma_nonce if isinstance(x_brahma_nonce, str) else request.headers.get("x-brahma-nonce")

        bound_node = (principal.token.node_id or "").strip().lower() if getattr(principal, "token", None) else ""
        x_node = (raw_x_node or "").strip().lower()
        caller_tok_id = getattr(principal.token, "id", None) if getattr(principal, "token", None) else None

        if bound_node:
            if x_node and x_node != bound_node:
                raise forbidden("Caller token is bound to a different node_id.", code="origin_mismatch")
            row = active_store.get_task(bound_node, task_id)
        elif x_node:
            row = active_store.get_task(x_node, task_id)
        else:
            row = active_store.get_task_by_id(task_id, caller_token_id=caller_tok_id)

        if row is None:
            caller_desc = x_node or bound_node or "caller"
            raise not_found(f"Task {task_id!r} not found for origin {caller_desc!r}.", code="task_not_found")

        raw_body = await request.body()
        _check_task_authorization(
            principal,
            row,
            bound_node,
            raw_sig,
            request,
            raw_ts,
            raw_nonce,
            body_bytes=raw_body,
            action_name="upload_artifact",
        )

        art_name = request.headers.get("x-brahma-artifact-name") or "artifact.bin"
        content_type = request.headers.get("content-type") or "application/octet-stream"

        try:
            meta = active_artifacts.store_artifact(
                task_id=task_id,
                content=raw_body,
                origin_node=row["origin_node"],
                producer_node=node_id,
                name=art_name,
                media_type=content_type,
            )
        except ValueError as exc:
            raise invalid(str(exc), code="artifact_too_large") from exc

        return JSONResponse(status_code=201, content=meta.to_dict())

    @router.get("/tasks/{task_id}/artifacts/{artifact_id}")
    def get_task_artifact(
        task_id: str,
        artifact_id: str,
        request: Request,
        x_brahma_node: Optional[str] = Header(default=None),
        origin_node: Optional[str] = None,
        x_brahma_signature: Optional[str] = Header(default=None),
        x_brahma_timestamp: Optional[str] = Header(default=None),
        x_brahma_nonce: Optional[str] = Header(default=None),
        principal: Principal = Depends(require_any("peer:run", "peer:status", "chat")),
    ) -> Response:
        """Fetch raw content-addressed artifact bytes."""
        active_store = getattr(request.app.state, "peer_store", None) or task_store
        active_artifacts = getattr(request.app.state, "artifact_store", None) or default_artifact_store

        raw_x_node = x_brahma_node if isinstance(x_brahma_node, str) else (request.headers.get("x-brahma-node") or origin_node)
        raw_sig = x_brahma_signature if isinstance(x_brahma_signature, str) else request.headers.get("x-brahma-signature")
        raw_ts = x_brahma_timestamp if isinstance(x_brahma_timestamp, str) else request.headers.get("x-brahma-timestamp")
        raw_nonce = x_brahma_nonce if isinstance(x_brahma_nonce, str) else request.headers.get("x-brahma-nonce")

        bound_node = (principal.token.node_id or "").strip().lower() if getattr(principal, "token", None) else ""
        x_node = (raw_x_node or "").strip().lower()
        caller_tok_id = getattr(principal.token, "id", None) if getattr(principal, "token", None) else None

        if bound_node:
            if x_node and x_node != bound_node:
                raise forbidden("Caller token is bound to a different node_id.", code="origin_mismatch")
            row = active_store.get_task(bound_node, task_id)
        elif x_node:
            row = active_store.get_task(x_node, task_id)
        else:
            row = active_store.get_task_by_id(task_id, caller_token_id=caller_tok_id)

        if row is None:
            caller_desc = x_node or bound_node or "caller"
            raise not_found(f"Task {task_id!r} not found for origin {caller_desc!r}.", code="task_not_found")

        _check_task_authorization(
            principal,
            row,
            bound_node,
            raw_sig,
            request,
            raw_ts,
            raw_nonce,
            body_bytes=b"",
            action_name="get_artifact",
        )

        res = active_artifacts.get_artifact(task_id, artifact_id)
        if res is None:
            raise not_found(f"Artifact {artifact_id!r} not found for task {task_id!r}.", code="artifact_not_found")

        meta, raw_bytes = res
        return Response(
            content=raw_bytes,
            media_type=meta.media_type or "application/octet-stream",
            headers={
                "X-Brahma-Artifact-SHA256": meta.sha256,
                "Content-Disposition": f'attachment; filename="{meta.name}"',
            },
        )

    # -- Service Status Endpoint (ADR-037 & Deep Dive §8.4) -----------------

    @router.get("/status")
    def get_federation_status(
        request: Request,
        principal: Principal = Depends(require_any("peer:status", "peer:run", "chat")),
    ) -> Dict[str, Any]:
        """Node federation status summary for UI and diagnostics."""
        active_service = getattr(request.app.state, "peer_service", None) or peer_service
        if active_service is not None:
            return active_service.get_status_summary()

        pinned = peers.list_peers() if hasattr(peers, "list_peers") else []
        return {
            "node_id": node_id,
            "spki_sha256": spki_sha256,
            "drain_mode": False,
            "active_tasks": len(agent_runner.active_task_ids()),
            "pinned_peers_count": len(pinned),
            "pinned_peers": [
                {
                    "node_id": p.node_id,
                    "name": p.name,
                    "endpoint": p.endpoint,
                    "spki_sha256": p.spki_sha256,
                    "profile": p.profile,
                    "paired_at": p.paired_at,
                }
                for p in pinned
            ],
            "discovered_peers": [],
        }

    # -- Cryptographic SAS Pairing Handshake (ADR-031, ADR-033, Invariant S-07) -

    @router.post("/pair/offer")
    def create_pairing_offer(
        request: Request,
        principal: Principal = Depends(require_pairing_consent),
    ) -> Dict[str, Any]:
        """Generate a fresh single-use 120s pairing offer for this node (operator authenticated)."""
        if not resolved_ident or not persistent_coord:
            raise unavailable("Node identity not configured on this host.")
        prune_pending_offers(pending_offers_map)
        host, port = _announced_host_port(request)
        offer = persistent_coord.create_offer(host=host, port=port)
        pending_offers_map[offer.nonce] = offer
        return {
            "offer_uri": offer.to_uri(),
            "node_id": offer.node_id,
            "spki_sha256": offer.spki_sha256,
            "host": offer.host,
            "port": offer.port,
            "nonce": offer.nonce,
            "created_at": offer.created_at,
        }

    @router.post("/pair/exchange")
    def execute_pairing_exchange(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Callee side of the mutual SAS pairing handshake with verified operator consent."""
        if not resolved_ident or not persistent_coord:
            raise unavailable("Node identity not configured on this host.")

        prune_pending_offers(pending_offers_map)

        offer_uri = str(payload.get("offer_uri") or "")
        initiator_node_id = str(payload.get("initiator_node_id") or "")
        initiator_spki_sha256 = str(payload.get("initiator_spki_sha256") or "")
        initiator_name = str(payload.get("initiator_name") or "Remote Peer")
        initiator_endpoint = str(payload.get("initiator_endpoint") or "")
        initiator_eph_pub_b64 = str(payload.get("initiator_eph_pub") or "")
        initiator_static_pub_b64 = str(payload.get("initiator_static_pub") or "")
        req_profile = str(payload.get("profile") or "worker").strip().lower()

        if req_profile == "operator":
            raise forbidden("Peers cannot be granted the operator profile.", code="forbidden_peer_profile")
        peer_profile = "worker" if req_profile == "worker" else "reader"

        if not offer_uri or not initiator_node_id or not initiator_spki_sha256:
            raise invalid("Missing required pairing parameters: offer_uri, initiator_node_id, initiator_spki_sha256")

        try:
            offer = PairingOffer.from_uri(offer_uri)
        except Exception as exc:
            raise invalid(f"Invalid pairing offer: {exc}")

        if offer.node_id != node_id:
            raise forbidden(f"Pairing offer target node '{offer.node_id}' does not match this node '{node_id}'")

        if offer.nonce not in pending_offers_map:
            raise forbidden(
                "Pairing offer was not issued by an authenticated operator on this node, or has already been consumed.",
                code="unauthorized_pairing_offer",
            )
        pending_offers_map.pop(offer.nonce, None)

        try:
            initiator_eph_pub = base64.b64decode(initiator_eph_pub_b64)
            initiator_static_pub = base64.b64decode(initiator_static_pub_b64)
        except Exception as exc:
            raise invalid(f"Invalid base64 in ephemeral or static public keys: {exc}")

        try:
            callee_eph_priv, callee_eph_pub, callee_static_pub = persistent_coord.begin_exchange(offer, is_initiator=False)
            sas_code, session_key = persistent_coord.complete_key_exchange(
                eph_priv=callee_eph_priv,
                peer_node_id=initiator_node_id,
                peer_eph_pub_bytes=initiator_eph_pub,
                peer_static_pub_bytes=initiator_static_pub,
                peer_spki_sha256=initiator_spki_sha256,
                nonce=offer.nonce,
                is_initiator=False,
            )
        except Exception as exc:
            raise unauthorized(f"Pairing key exchange failed: {exc}")

        # Mint token bound strictly to initiator for the peer lane only
        default_ws = next(iter(config.workspaces.keys()), "default") if config.workspaces else None
        _rec, minted_token = tokens.mint(
            preset=peer_profile,
            label=f"peer:{initiator_node_id}",
            workspace=default_ws,
            scopes=["peer:run", "peer:status", "peer:cancel", "peer:artifact"],
            node_id=initiator_node_id,
            spki_sha256=initiator_spki_sha256,
            confirm=None,
        )

        enc_token = persistent_coord.wrap_token_payload(
            session_key=session_key,
            token=minted_token,
            profile=peer_profile,
            callee_node_id=node_id,
            callee_spki_sha256=spki_sha256,
            callee_endpoint=_announced_endpoint(request),
        )

        # Pin initiator on callee side with NO outbound token (callee does not use minted_token outbound)
        pinned_peer = PinnedPeer(
            node_id=initiator_node_id,
            name=initiator_name,
            endpoint=initiator_endpoint,
            spki_sha256=initiator_spki_sha256,
            token="",
            profile=peer_profile,
            paired_at=now_iso(),
            static_pub_raw=base64.b64encode(initiator_static_pub).decode("ascii"),
            workspaces=list(payload.get("workspaces") or []),
            labels=dict(payload.get("labels") or {}),
        )
        peers.save_peer(pinned_peer, allow_re_pair=True)

        return {
            "callee_node_id": node_id,
            "callee_spki_sha256": spki_sha256,
            "callee_eph_pub": base64.b64encode(callee_eph_pub).decode("ascii"),
            "callee_static_pub": base64.b64encode(callee_static_pub).decode("ascii"),
            "sas_code": sas_code,
            "encrypted_token": base64.b64encode(enc_token).decode("ascii"),
            "workspaces": list(config.workspaces.keys()),
            "labels": {"node_id": node_id, "host": getattr(config, "advertised_host", config.host)},
        }

    return router
