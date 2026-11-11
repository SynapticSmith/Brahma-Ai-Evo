"""The HTTP surface: models, chat completions (non-streaming + SSE), and diagnostics.

Every request resolves to a ``Principal`` (a token record) and a
``ResolvedModel`` (lane + preset + workspace). The route then hands the work to
``AgentRunner``. Nothing here calls a tool, reads a policy decision, or spawns a
process -- that is the whole point of the design (ADR-022, proposed).

Phase 1 serves:
- ``GET /v1/models``
- ``POST /v1/chat/completions`` (non-streaming and SSE streaming)
- ``GET /v1/brahma/whoami``
- ``GET /v1/brahma/runs/{id}``
- Concurrency limiting (global + per-token semaphores yielding 429)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent.providers import ProviderUnavailable

from . import models, translate
from .auth import Principal, TokenRecord, TokenRegistry
from .config import API_VERSION, BrahmaApiConfig
from .errors import (
    ApiError,
    forbidden,
    install_handlers,
    invalid,
    not_found,
    rate_limited,
    too_large,
    unauthorized,
    unavailable,
)
from .runner import AgentRunner
from .schemas import ChatRequest, validate_request
from .streaming import (
    finish_chunk,
    initial_role_chunk,
    split_text_chunks,
    sse_comment,
    sse_data,
    sse_done,
    stream_error_chunk,
    text_delta_chunk,
    tool_calls_delta_chunk,
    usage_chunk,
)

logger = logging.getLogger(__name__)


def _audit_logger(config: BrahmaApiConfig) -> Optional[Any]:
    if not config.audit:
        return None
    try:
        from core.audit import AuditLogger

        path = Path(config.audit_log).expanduser() if config.audit_log else None
        return AuditLogger(path)
    except Exception:
        # A missing audit directory must not stop the port; the run still
        # happens, and the failure is visible in the console.
        return None


def _anonymous(config: BrahmaApiConfig) -> Principal:
    """Only for ``auth_required=false``, which is a deliberate local choice."""
    alias = next(iter(config.workspaces), None)
    return Principal(
        TokenRecord(
            id="anonymous",
            label="anonymous",
            sha256="",
            profile="reader",
            workspace=alias,
            created_at="",
        )
    )


class ConcurrencyLimiter:
    """Caps concurrent runs globally and per-token (design doc §5.12)."""

    def __init__(self, global_limit: int = 2, per_token_limit: int = 1) -> None:
        self.global_limit = max(1, global_limit)
        self.per_token_limit = max(1, per_token_limit)
        self._global_sem = asyncio.Semaphore(self.global_limit)
        self._token_sems: Dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, token_id: str) -> Any:
        if self._global_sem.locked():
            raise rate_limited("Global concurrency limit reached.", retry_after=5)
        async with self._lock:
            if token_id not in self._token_sems:
                self._token_sems[token_id] = asyncio.Semaphore(self.per_token_limit)
            token_sem = self._token_sems[token_id]
        if token_sem.locked():
            raise rate_limited("Per-token concurrency limit reached.", retry_after=5)

        await self._global_sem.acquire()
        try:
            await token_sem.acquire()
        except BaseException:
            self._global_sem.release()
            raise

        class _ReleaseContext:
            def __init__(self, g_sem: asyncio.Semaphore, t_sem: asyncio.Semaphore) -> None:
                self._g = g_sem
                self._t = t_sem
                self._released = False

            def release(self) -> None:
                if not self._released:
                    self._released = True
                    self._t.release()
                    self._g.release()

            async def __aenter__(self) -> "_ReleaseContext":
                return self

            async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
                self.release()

        return _ReleaseContext(self._global_sem, token_sem)


class RateLimiter:
    """Sliding-window request rate limiter per token (design doc §5.12)."""

    def __init__(self, requests_per_minute: int = 30) -> None:
        self.rpm = max(1, requests_per_minute)
        self._history: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, token_id: str) -> None:
        now = time.monotonic()
        async with self._lock:
            timestamps = self._history.setdefault(token_id, [])
            cutoff = now - 60.0
            timestamps = [t for t in timestamps if t > cutoff]
            self._history[token_id] = timestamps
            if len(timestamps) >= self.rpm:
                oldest = timestamps[0]
                retry_after = max(1, int(60.0 - (now - oldest)) + 1)
                raise rate_limited(
                    f"Rate limit of {self.rpm} requests per minute exceeded.",
                    retry_after=retry_after,
                )
            timestamps.append(now)


class SessionTracker:
    """Ensures at most one active run per pinned session, returning 409 when busy."""

    def __init__(self) -> None:
        self._active: set[str] = set()
        self._lock = asyncio.Lock()

    async def acquire(self, session_id: str) -> Any:
        async with self._lock:
            if session_id in self._active:
                raise ApiError(
                    409,
                    "Session is busy with another run.",
                    "invalid_request_error",
                    None,
                    "session_busy",
                )
            self._active.add(session_id)

        class _Release:
            def __init__(self, tracker: "SessionTracker", sid: str) -> None:
                self._tracker = tracker
                self._sid = sid
                self._released = False

            async def release(self) -> None:
                if not self._released:
                    self._released = True
                    async with self._tracker._lock:
                        self._tracker._active.discard(self._sid)

            async def __aenter__(self) -> "_Release":
                return self

            async def __aexit__(self, exc_type: Any, exc_val: Any, tb: Any) -> None:
                await self.release()

        return _Release(self, session_id)


def create_app(
    config: BrahmaApiConfig,
    *,
    registry: Optional[TokenRegistry] = None,
    runner: Optional[AgentRunner] = None,
    peer_store: Optional[Any] = None,
    peer_identity: Optional[Any] = None,
    peer_registry: Optional[Any] = None,
    peer_artifact_store: Optional[Any] = None,
    peer_service: Optional[Any] = None,
) -> FastAPI:
    app = FastAPI(
        title="Brahma Evo OpenAI-compatible port",
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_handlers(app)
    tokens = registry or TokenRegistry(config.tokens_path)
    agent_runner = runner or AgentRunner(config, audit=_audit_logger(config))
    limiter = ConcurrencyLimiter(
        global_limit=config.limits.concurrent_runs,
        per_token_limit=config.limits.concurrent_runs_per_token,
    )
    rate_limiter = RateLimiter(requests_per_minute=config.limits.requests_per_minute)
    session_tracker = SessionTracker()
    is_custom_base = config.base_dir and Path(config.base_dir).resolve() != Path.cwd().resolve()
    base_peer_dir = (Path(config.base_dir) / "peer") if is_custom_base else None

    resolved_identity = peer_identity
    if resolved_identity is None:
        try:
            from core.peer.identity import NodeIdentity

            identity_dir = (base_peer_dir / "identity") if base_peer_dir else None
            resolved_identity = NodeIdentity.load_or_create(
                identity_dir=identity_dir,
                enforce_machine_binding=False,
            )
        except Exception as exc:
            logger.warning("Could not initialize NodeIdentity: %s", exc)
            resolved_identity = None

    resolved_service = peer_service
    resolved_store = peer_store
    resolved_registry = peer_registry
    resolved_artifacts = peer_artifact_store
    if resolved_service is None and resolved_identity is not None:
        try:
            from core.peer.artifacts import PeerArtifactStore
            from core.peer.pairing import PeerRegistry
            from core.peer.service import PeerService, set_active_peer_service
            from core.peer.store import PeerTaskStore

            if base_peer_dir:
                resolved_store = resolved_store or PeerTaskStore(db_path=base_peer_dir / "tasks.db")
                resolved_registry = resolved_registry or PeerRegistry(registry_file=base_peer_dir / "peers.json")
                resolved_artifacts = resolved_artifacts or PeerArtifactStore(base_dir=base_peer_dir / "artifacts")
            else:
                resolved_store = resolved_store or PeerTaskStore()
                resolved_registry = resolved_registry or PeerRegistry()
                resolved_artifacts = resolved_artifacts or PeerArtifactStore()

            resolved_service = PeerService(
                identity=resolved_identity,
                store=resolved_store,
                registry=resolved_registry,
                artifact_store=resolved_artifacts,
                endpoint=config.peer_endpoint,
            )
            set_active_peer_service(resolved_service)
        except Exception as exc:
            logger.warning("Failed to initialize PeerService: %s", exc, exc_info=True)
            resolved_service = None
    elif resolved_service is not None:
        try:
            from core.peer.service import set_active_peer_service

            set_active_peer_service(resolved_service)
        except Exception:
            pass

    app.state.config = config
    app.state.registry = tokens
    app.state.runner = agent_runner
    app.state.limiter = limiter
    app.state.rate_limiter = rate_limiter
    app.state.session_tracker = session_tracker
    app.state.peer_store = resolved_store
    app.state.peer_identity = resolved_identity
    app.state.peer_registry = resolved_registry
    app.state.artifact_store = resolved_artifacts
    app.state.peer_service = resolved_service

    @app.middleware("http")
    async def _body_limit(request: Request, call_next: Callable[..., Any]) -> Any:
        raw = request.headers.get("content-length")
        if raw:
            try:
                length = int(raw)
            except ValueError:
                length = 0
            if length > config.limits.max_body_bytes:
                err = too_large(
                    f"Request body is {length} bytes; the limit is "
                    f"{config.limits.max_body_bytes}."
                )
                return JSONResponse(err.body(), status_code=err.status)
        return await call_next(request)

    def principal_for(authorization: Optional[str] = Header(default=None)) -> Principal:
        if not config.auth_required:
            return _anonymous(config)
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise unauthorized("Missing bearer token. Send Authorization: Bearer <token>.")
        record = tokens.verify(value.strip())
        if record is None:
            raise unauthorized()
        tokens.touch(record.id)
        return Principal(record)

    def require(scope: str) -> Callable[..., Principal]:
        def dependency(
            principal: Principal = Depends(principal_for),
        ) -> Principal:
            if not principal.has_scope(scope):
                raise forbidden(
                    f"This key does not carry the {scope!r} scope.",
                    code="insufficient_scope",
                )
            return principal

        return dependency

    # -- routes ------------------------------------------------------------

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"status": "ok", "service": "brahma_api", "version": API_VERSION}

    @app.get("/v1/models")
    def list_models(principal: Principal = Depends(require("models"))) -> Dict[str, Any]:
        return {"object": "list", "data": models.model_rows(config, principal)}

    @app.get("/v1/brahma/whoami")
    def whoami(principal: Principal = Depends(principal_for)) -> Dict[str, Any]:
        return {
            "token_id": principal.token.id,
            "label": principal.token.label,
            "profile": principal.token.profile,
            "scopes": list(principal.token.scopes),
            "workspace": principal.workspace,
            "provider": principal.token.provider,
            "upstream_model": principal.token.upstream_model,
            "created_at": principal.token.created_at,
            "last_used_at": principal.token.last_used_at,
        }

    @app.get("/v1/brahma/sessions")
    def list_sessions(principal: Principal = Depends(principal_for)) -> Dict[str, Any]:
        from agent.session import AgentSession

        recent = AgentSession.recent(limit=50)
        return {
            "object": "list",
            "data": [record.to_dict() for record in recent],
        }

    @app.get("/v1/brahma/runs/{run_id}")
    def get_run(
        run_id: str,
        principal: Principal = Depends(principal_for),
    ) -> Dict[str, Any]:
        run = agent_runner.get_run(run_id)
        if run is None:
            raise not_found(f"Run {run_id!r} not found.", code="run_not_found")
        if run.get("token_id") != principal.token.id and not principal.has_scope("admin"):
            raise not_found(f"Run {run_id!r} not found.", code="run_not_found")
        return run

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        body: ChatRequest,
        principal: Principal = Depends(require("chat")),
    ) -> Any:
        # Rate limiting per token (sliding window)
        await rate_limiter.check(principal.token.id)

        resolved = models.resolve(config, principal, body.model)
        ignored = validate_request(body, tools_enabled=resolved.tools, lane=resolved.lane)

        # GUI Approval Channel
        approval_header = (request.headers.get("x-brahma-approval") or "deny").strip().lower()
        if approval_header == "gui":
            client_ip = request.client.host if request.client else "127.0.0.1"
            if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
                raise forbidden("GUI approval is only allowed for loopback callers.", code="policy_denied")
            if not principal.has_scope("approve"):
                raise forbidden("This key does not carry the 'approve' scope.", code="insufficient_scope")
            approval_channel = "gui"
        elif approval_header == "deny":
            approval_channel = "deny"
        else:
            raise invalid(f"Unknown X-Brahma-Approval {approval_header!r}; choose 'deny' or 'gui'.", param="X-Brahma-Approval")

        # Pinned session binding
        session_header = (request.headers.get("x-brahma-session") or "").strip()
        session_id = session_header or (body.user.strip() if body.user else None)
        sess_record = None
        session_lock = None
        if session_id:
            session_lock = await session_tracker.acquire(session_id)
            try:
                from agent.session import AgentSession, SessionNotFound
                try:
                    sess_record = AgentSession.open(session_id)
                except SessionNotFound:
                    sess_record = AgentSession.create(
                        cwd=str(resolved.workspace or config.base_dir),
                        session_id=session_id,
                        title=f"API session {session_id}",
                        model=resolved.model_id,
                    )
            except Exception:
                await session_lock.release()
                raise

        run_id = secrets.token_hex(6)
        created = int(time.time())

        if resolved.lane == "passthrough":
            wire_messages = translate.build_passthrough_messages(
                body.messages,
                harness_system="",
                max_messages=config.limits.max_messages,
            )
            seed = None
        else:
            wire_messages = None
            seed = translate.build_seed(body.messages, max_messages=config.limits.max_messages)
            if sess_record is not None:
                from agent.session_bridge import history
                stored_msgs = history(sess_record)
                if stored_msgs:
                    # Stored session wins over replayed history (§5.9)
                    seed = translate.Seed(history=stored_msgs, prompt=seed.prompt, directives=seed.directives)

        if body.stream:
            return await _stream_completions(
                request=request,
                config=config,
                principal=principal,
                resolved=resolved,
                seed=seed,
                wire_messages=wire_messages,
                body=body,
                run_id=run_id,
                created=created,
                runner=agent_runner,
                limiter=limiter,
                session_lock=session_lock,
                sess_record=sess_record,
                session_id=session_id,
                approval_channel=approval_channel,
            )

        # Non-streaming path
        ctx = await limiter.acquire(principal.token.id)
        cancel = threading.Event()
        try:
            if resolved.lane == "passthrough":
                outcome = await asyncio.to_thread(
                    agent_runner.run_passthrough,
                    principal,
                    resolved,
                    wire_messages,
                    body.tools,
                    temperature=body.temperature,
                    cancel=cancel,
                    run_id=run_id,
                )
            else:
                outcome = await asyncio.to_thread(
                    agent_runner.run,
                    principal,
                    resolved,
                    seed,
                    temperature=body.temperature,
                    cancel=cancel,
                    run_id=run_id,
                    approval_channel=approval_channel,
                )
        except ProviderUnavailable as exc:
            raise unavailable(str(exc)) from exc
        finally:
            ctx.release()
            if session_lock:
                await session_lock.release()

        result = outcome.result
        if resolved.lane != "passthrough" and not result.text.strip():
            if result.stop_reason == "permission_denied":
                raise forbidden(
                    "The agent was denied permission before it could answer.",
                    code="policy_denied",
                )
            if result.stop_reason == "error":
                raise ApiError(
                    500,
                    "The agent stopped on an internal error before answering.",
                    "server_error",
                    None,
                    "internal_error",
                )

        if sess_record is not None:
            if resolved.lane != "passthrough":
                if seed and seed.prompt:
                    sess_record.append_message({"role": "user", "content": seed.prompt})
                sess_record.append_message({"role": "assistant", "content": result.text})
            else:
                last_user = next((m for m in reversed(body.messages) if m.role == "user"), None)
                if last_user:
                    from .translate import _content_text
                    sess_record.append_message({"role": "user", "content": _content_text(last_user)})
                asst_entry: Dict[str, Any] = {"role": "assistant", "content": result.text or None}
                if outcome.tool_calls:
                    asst_entry["tool_calls"] = outcome.tool_calls
                sess_record.append_message(asst_entry)

        return translate.completion_body(
            run_id=run_id,
            model_id=resolved.model_id,
            text=result.text,
            stop_reason=result.stop_reason,
            result_usage=result.usage,
            lane=resolved.lane,
            provider=outcome.provider,
            upstream_model=outcome.upstream_model,
            policy_fingerprint=outcome.policy_fingerprint,
            ignored_params=ignored,
            session_id=session_id,
            tool_calls=outcome.tool_calls,
        )

    # -- Peer routes (BTE-1, ADR-030) --------------------------------------
    from .peer import build_peer_router

    peer_store = getattr(app.state, "peer_store", None)
    peer_identity = getattr(app.state, "peer_identity", None)
    peer_registry = getattr(app.state, "peer_registry", None)
    peer_artifact_store = getattr(app.state, "artifact_store", None)
    peer_service = getattr(app.state, "peer_service", None)
    app.include_router(
        build_peer_router(
            config,
            tokens,
            agent_runner,
            store=peer_store,
            identity=peer_identity,
            peer_registry=peer_registry,
            artifact_store=peer_artifact_store,
            peer_service=peer_service,
        )
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if resolved_service is not None:
            resolved_service.close()
        try:
            from core.peer.service import get_active_peer_service, set_active_peer_service
            if get_active_peer_service() is resolved_service:
                set_active_peer_service(None)
        except Exception:
            pass

    app.router.lifespan_context = lifespan
    return app


async def _stream_completions(
    *,
    request: Request,
    config: BrahmaApiConfig,
    principal: Principal,
    resolved: models.ResolvedModel,
    seed: Optional[translate.Seed],
    wire_messages: Optional[List[Dict[str, Any]]],
    body: ChatRequest,
    run_id: str,
    created: int,
    runner: AgentRunner,
    limiter: ConcurrencyLimiter,
    session_lock: Optional[Any] = None,
    sess_record: Optional[Any] = None,
    session_id: Optional[str] = None,
    approval_channel: str = "deny",
) -> StreamingResponse:
    ctx = await limiter.acquire(principal.token.id)
    cancel = threading.Event()
    queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
    async_loop = asyncio.get_running_loop()

    def sink_callback(event: Dict[str, Any]) -> None:
        async_loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        try:
            if resolved.lane == "passthrough":
                outcome = runner.run_passthrough(
                    principal,
                    resolved,
                    wire_messages or [],
                    body.tools,
                    temperature=body.temperature,
                    cancel=cancel,
                    sink_callback=sink_callback,
                    run_id=run_id,
                )
            else:
                outcome = runner.run(
                    principal,
                    resolved,
                    seed,  # type: ignore
                    temperature=body.temperature,
                    cancel=cancel,
                    sink_callback=sink_callback,
                    run_id=run_id,
                    approval_channel=approval_channel,
                )
            async_loop.call_soon_threadsafe(queue.put_nowait, {"type": "_done", "outcome": outcome})
        except Exception as exc:
            async_loop.call_soon_threadsafe(queue.put_nowait, {"type": "_error", "exc": exc})

    threading.Thread(target=worker, daemon=True).start()

    async def event_generator() -> Any:
        try:
            # First chunk introduces the assistant role
            yield sse_data(
                initial_role_chunk(
                    run_id=run_id,
                    model_id=resolved.model_id,
                    created=created,
                )
            )

            if session_id:
                yield sse_comment(f"brahma.session {json.dumps({'id': session_id})}")

            deltas_streamed = False
            done = False
            while not done:
                # Disconnect detection
                if await request.is_disconnected():
                    cancel.set()
                    runner.audit_cancelled(principal, resolved, "disconnect")
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=config.keepalive_s)
                except asyncio.TimeoutError:
                    yield sse_comment("keepalive")
                    continue

                event_type = event.get("type")
                if event_type == "turn_start":
                    yield sse_comment(f"brahma.turn {json.dumps({'turn': event.get('turn')})}")
                elif event_type == "tool_end":
                    yield sse_comment(
                        f"brahma.tool {json.dumps({'name': event.get('name'), 'chars': event.get('chars')})}"
                    )
                elif event_type in ("subagent_start", "subagent_end"):
                    # Progress for a delegated run. Comments keep the OpenAI
                    # stream valid: a client that only understands deltas can
                    # ignore them, and one that watches them can show the
                    # child working instead of a silent multi-minute gap.
                    payload = {
                        "id": event.get("subagent"),
                        "phase": "start" if event_type == "subagent_start" else "end",
                        "task": event.get("task"),
                        "tools": event.get("tools"),
                        "stop_reason": event.get("stop_reason"),
                        "turns": event.get("turns"),
                        "chars": event.get("chars"),
                    }
                    yield sse_comment(
                        "brahma.subagent "
                        + json.dumps({k: v for k, v in payload.items() if v is not None})
                    )
                elif event_type == "subagent_event":
                    inner = event.get("event") or {}
                    payload = {
                        "id": event.get("subagent"),
                        "phase": "event",
                        "event": inner.get("type"),
                        "turn": inner.get("turn"),
                        "tool": inner.get("name"),
                        "chars": inner.get("chars"),
                    }
                    yield sse_comment(
                        "brahma.subagent "
                        + json.dumps({k: v for k, v in payload.items() if v is not None})
                    )
                elif event_type == "delta":
                    deltas_streamed = True
                    delta = event.get("delta") or {}
                    kind = delta.get("kind")
                    if kind == "text":
                        text_val = delta.get("text", "")
                        if text_val:
                            yield sse_data(
                                text_delta_chunk(
                                    run_id=run_id,
                                    model_id=resolved.model_id,
                                    content=text_val,
                                    created=created,
                                )
                            )
                    elif kind == "tool_call":
                        tc_idx = delta.get("index", 0)
                        tc_id = delta.get("id")
                        tc_name = delta.get("name")
                        tc_args = delta.get("arguments", "")
                        tc_frag: Dict[str, Any] = {
                            "index": tc_idx,
                            "type": "function",
                            "function": {"arguments": tc_args},
                        }
                        if tc_id:
                            tc_frag["id"] = tc_id
                        if tc_name:
                            tc_frag["function"]["name"] = tc_name
                        yield sse_data(
                            tool_calls_delta_chunk(
                                run_id=run_id,
                                model_id=resolved.model_id,
                                tool_calls=[tc_frag],
                                created=created,
                            )
                        )
                elif event_type == "_error":
                    exc = event["exc"]
                    code = "provider_unavailable" if isinstance(exc, ProviderUnavailable) else "internal_error"
                    yield sse_data(
                        stream_error_chunk(
                            str(exc),
                            type="server_error",
                            code=code,
                        )
                    )
                    done = True
                elif event_type == "_done":
                    outcome = event["outcome"]
                    result = outcome.result
                    fingerprint = outcome.policy_fingerprint

                    if resolved.lane == "passthrough":
                        finish_reason = "tool_calls" if outcome.tool_calls else "stop"
                        if not deltas_streamed:
                            if result.text:
                                for piece in split_text_chunks(result.text):
                                    yield sse_data(
                                        text_delta_chunk(
                                            run_id=run_id,
                                            model_id=resolved.model_id,
                                            content=piece,
                                            created=created,
                                            system_fingerprint=fingerprint,
                                        )
                                    )
                            if outcome.tool_calls:
                                for idx, tc in enumerate(outcome.tool_calls):
                                    func = tc.get("function") or {}
                                    yield sse_data(
                                        tool_calls_delta_chunk(
                                            run_id=run_id,
                                            model_id=resolved.model_id,
                                            tool_calls=[{
                                                "index": idx,
                                                "id": tc.get("id"),
                                                "type": "function",
                                                "function": {
                                                    "name": func.get("name"),
                                                    "arguments": func.get("arguments", ""),
                                                },
                                            }],
                                            created=created,
                                            system_fingerprint=fingerprint,
                                        )
                                    )
                        yield sse_data(
                            finish_chunk(
                                run_id=run_id,
                                model_id=resolved.model_id,
                                finish_reason=finish_reason,
                                created=created,
                                system_fingerprint=fingerprint,
                            )
                        )
                    else:
                        # Agent lane
                        if not result.text.strip() and result.stop_reason == "permission_denied":
                            yield sse_data(
                                stream_error_chunk(
                                    "The agent was denied permission before it could answer.",
                                    type="permission_denied",
                                    code="policy_denied",
                                )
                            )
                            done = True
                            break

                        if not result.text.strip() and result.stop_reason == "error":
                            yield sse_data(
                                stream_error_chunk(
                                    "The agent stopped on an internal error before answering.",
                                    type="server_error",
                                    code="internal_error",
                                )
                            )
                            done = True
                            break

                        # The passthrough lane already uses this guard: when
                        # the loop streamed the text as deltas, re-sending
                        # ``result.text`` here would pipe the answer to the
                        # client twice. The agent lane does not stream today,
                        # but the guard keeps that true if it ever does.
                        if not deltas_streamed:
                            for delta_text in split_text_chunks(result.text):
                                yield sse_data(
                                    text_delta_chunk(
                                        run_id=run_id,
                                        model_id=resolved.model_id,
                                        content=delta_text,
                                        created=created,
                                        system_fingerprint=fingerprint,
                                    )
                                )

                        finish_reason = translate.FINISH_REASONS.get(result.stop_reason, "stop")
                        yield sse_data(
                            finish_chunk(
                                run_id=run_id,
                                model_id=resolved.model_id,
                                finish_reason=finish_reason,
                                created=created,
                                system_fingerprint=fingerprint,
                            )
                        )

                    # Usage chunk
                    include_usage = True
                    if body.stream_options is not None:
                        include_usage = bool(body.stream_options.get("include_usage", True))
                    if include_usage:
                        usage = translate.extract_usage(result.usage)
                        yield sse_data(
                            usage_chunk(
                                run_id=run_id,
                                model_id=resolved.model_id,
                                usage=usage,
                                created=created,
                                system_fingerprint=fingerprint,
                            )
                        )

                    # Persist session turns
                    if sess_record is not None:
                        if resolved.lane != "passthrough":
                            if seed and seed.prompt:
                                sess_record.append_message({"role": "user", "content": seed.prompt})
                            sess_record.append_message({"role": "assistant", "content": result.text})
                        else:
                            last_user = next((m for m in reversed(body.messages) if m.role == "user"), None)
                            if last_user:
                                from .translate import _content_text
                                sess_record.append_message({"role": "user", "content": _content_text(last_user)})
                            asst_entry = {"role": "assistant", "content": result.text or None}
                            if outcome.tool_calls:
                                asst_entry["tool_calls"] = outcome.tool_calls
                            sess_record.append_message(asst_entry)

                    done = True

            yield sse_done()
        finally:
            ctx.release()
            if session_lock:
                await session_lock.release()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class CompositeServer:
    """Runs primary HTTP port (8010) and optional TLS peer port (8443) concurrently."""

    def __init__(self, main_server: uvicorn.Server, peer_server: Optional[uvicorn.Server] = None) -> None:
        self.main_server = main_server
        self.peer_server = peer_server

    @property
    def config(self) -> uvicorn.Config:
        return self.main_server.config

    @property
    def started(self) -> bool:
        if self.peer_server:
            return self.main_server.started and self.peer_server.started
        return self.main_server.started

    @property
    def should_exit(self) -> bool:
        return self.main_server.should_exit or (self.peer_server.should_exit if self.peer_server else False)

    @should_exit.setter
    def should_exit(self, value: bool) -> None:
        self.main_server.should_exit = value
        if self.peer_server:
            self.peer_server.should_exit = value

    async def serve(self, sockets: Any = None) -> None:
        if self.peer_server is None:
            await self.main_server.serve(sockets=sockets)
            return

        async def _sync_exit() -> None:
            while not (self.main_server.should_exit or self.peer_server.should_exit):
                await asyncio.sleep(0.05)
            self.main_server.should_exit = True
            self.peer_server.should_exit = True

        sync_task = asyncio.create_task(_sync_exit())
        try:
            await asyncio.gather(
                self.main_server.serve(sockets=sockets),
                self.peer_server.serve(),
            )
        finally:
            sync_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass

    def run(self, sockets: Any = None) -> None:
        try:
            asyncio.run(self.serve(sockets=sockets))
        except KeyboardInterrupt:
            self.should_exit = True


def serve(config: BrahmaApiConfig, *, app: Optional[FastAPI] = None) -> Any:
    """Build the ASGI server. Refuses to start when the config says disabled."""
    if not config.enabled:
        raise SystemExit(
            "brahma_api is disabled. Set \"enabled\": true in config/brahma_api.json "
            "(loopback and a bearer token are still required)."
        )
    application = app or create_app(config)
    main_uv_config = uvicorn.Config(
        application,
        host=config.host,
        port=config.port,
        log_level="warning",
        access_log=False,
    )

    cert_path: Optional[Path] = None
    key_path: Optional[Path] = None
    if config.peer_tls:
        if config.peer_cert and config.peer_key:
            cert_path = Path(config.peer_cert)
            key_path = Path(config.peer_key)
        else:
            ident = getattr(getattr(application, "state", None), "peer_identity", None)
            if ident is None:
                from core.peer.identity import NodeIdentity

                ident = NodeIdentity.load_or_create(enforce_machine_binding=False)
            base_dir = getattr(config, "base_dir", None)
            identity_dir = (base_dir / "peer" / "identity") if base_dir else None
            cert_path, key_path = ident.ensure_certificate_files(cert_dir=identity_dir)

    if config.peer_port == config.port:
        if config.peer_tls and cert_path and key_path:
            main_uv_config.ssl_certfile = str(cert_path)
            main_uv_config.ssl_keyfile = str(key_path)
        return uvicorn.Server(main_uv_config)

    main_server = uvicorn.Server(main_uv_config)
    if config.peer_tls and cert_path and key_path:
        peer_uv_config = uvicorn.Config(
            application,
            host=config.host,
            port=config.peer_port,
            log_level="warning",
            access_log=False,
            lifespan="off",
            ssl_certfile=str(cert_path),
            ssl_keyfile=str(key_path),
        )
    else:
        peer_uv_config = uvicorn.Config(
            application,
            host=config.host,
            port=config.peer_port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
    peer_server = uvicorn.Server(peer_uv_config)
    return CompositeServer(main_server, peer_server)
