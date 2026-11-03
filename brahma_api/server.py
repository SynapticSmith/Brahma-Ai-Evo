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
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

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
    usage_chunk,
)


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


def create_app(
    config: BrahmaApiConfig,
    *,
    registry: Optional[TokenRegistry] = None,
    runner: Optional[AgentRunner] = None,
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
    app.state.config = config
    app.state.registry = tokens
    app.state.runner = agent_runner
    app.state.limiter = limiter

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
        resolved = models.resolve(config, principal, body.model)
        ignored = validate_request(body, tools_enabled=resolved.tools)
        seed = translate.build_seed(body.messages, max_messages=config.limits.max_messages)
        run_id = secrets.token_hex(6)
        created = int(time.time())

        if body.stream:
            return await _stream_completions(
                request=request,
                config=config,
                principal=principal,
                resolved=resolved,
                seed=seed,
                body=body,
                run_id=run_id,
                created=created,
                runner=agent_runner,
                limiter=limiter,
            )

        # Non-streaming path
        ctx = await limiter.acquire(principal.token.id)
        cancel = threading.Event()
        try:
            outcome = await asyncio.to_thread(
                agent_runner.run,
                principal,
                resolved,
                seed,
                temperature=body.temperature,
                cancel=cancel,
                run_id=run_id,
            )
        except ProviderUnavailable as exc:
            raise unavailable(str(exc)) from exc
        finally:
            ctx.release()

        result = outcome.result
        if not result.text.strip():
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
        )

    return app


async def _stream_completions(
    *,
    request: Request,
    config: BrahmaApiConfig,
    principal: Principal,
    resolved: models.ResolvedModel,
    seed: translate.Seed,
    body: ChatRequest,
    run_id: str,
    created: int,
    runner: AgentRunner,
    limiter: ConcurrencyLimiter,
) -> StreamingResponse:
    ctx = await limiter.acquire(principal.token.id)
    cancel = threading.Event()
    queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
    async_loop = asyncio.get_running_loop()

    def sink_callback(event: Dict[str, Any]) -> None:
        async_loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        try:
            outcome = runner.run(
                principal,
                resolved,
                seed,
                temperature=body.temperature,
                cancel=cancel,
                sink_callback=sink_callback,
                run_id=run_id,
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

                    # Final mode: stream the assistant's buffered final text
                    for delta in split_text_chunks(result.text):
                        yield sse_data(
                            text_delta_chunk(
                                run_id=run_id,
                                model_id=resolved.model_id,
                                content=delta,
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
                    done = True

            yield sse_done()
        finally:
            ctx.release()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def serve(config: BrahmaApiConfig, *, app: Optional[FastAPI] = None) -> uvicorn.Server:
    """Build the ASGI server. Refuses to start when the config says disabled."""
    if not config.enabled:
        raise SystemExit(
            "brahma_api is disabled. Set \"enabled\": true in config/brahma_api.json "
            "(loopback and a bearer token are still required)."
        )
    application = app or create_app(config)
    uv_config = uvicorn.Config(
        application,
        host=config.host,
        port=config.port,
        log_level="warning",
        access_log=False,
    )
    return uvicorn.Server(uv_config)
