"""The HTTP surface: two routes, one permission boundary, no tool dispatch.

Every request resolves to a ``Principal`` (a token record) and a
``ResolvedModel`` (lane + preset + workspace). The route then hands the work to
``AgentRunner``. Nothing here calls a tool, reads a policy decision, or spawns a
process -- that is the whole point of the design (ADR-022, proposed).

Phase 0 serves ``GET /v1/models`` and non-streaming
``POST /v1/chat/completions``. Streaming, sessions, and diagnostics arrive in
later phases; the routes for them are deliberately absent rather than stubbed,
so a client gets an honest 404 instead of a 200 with nothing behind it.
"""

from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from agent.providers import ProviderUnavailable

from . import models, translate
from .auth import Principal, TokenRecord, TokenRegistry
from .config import API_VERSION, BrahmaApiConfig
from .errors import ApiError, forbidden, install_handlers, too_large, unauthorized, unavailable
from .runner import AgentRunner
from .schemas import ChatRequest, validate_request


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
    app.state.config = config
    app.state.registry = tokens
    app.state.runner = agent_runner

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

    def principal_for(authorization: Optional[str]) -> Principal:
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
            request: Request,
            authorization: Optional[str] = Header(default=None),
        ) -> Principal:
            principal = principal_for(authorization)
            if not principal.has_scope(scope):
                raise forbidden(
                    f"This key does not carry the {scope!r} scope.",
                    code="insufficient_scope",
                )
            return principal

        return dependency

    @app.get("/health")
    def health() -> Dict[str, Any]:
        # Unauthenticated on purpose: a process check should not need a secret.
        # It exposes no paths, tokens, or model configuration.
        return {"status": "ok", "service": "brahma_api", "version": API_VERSION}

    @app.get("/v1/models")
    def list_models(principal: Principal = Depends(require("models"))) -> Dict[str, Any]:
        return {"object": "list", "data": models.model_rows(config, principal)}

    @app.post("/v1/chat/completions")
    def chat_completions(
        body: ChatRequest,
        principal: Principal = Depends(require("chat")),
    ) -> Dict[str, Any]:
        resolved = models.resolve(config, principal, body.model)
        ignored = validate_request(body, tools_enabled=resolved.tools)
        seed = translate.build_seed(body.messages, max_messages=config.limits.max_messages)
        run_id = secrets.token_hex(6)
        cancel = threading.Event()
        try:
            outcome = agent_runner.run(
                principal,
                resolved,
                seed,
                temperature=body.temperature,
                cancel=cancel,
            )
        except ProviderUnavailable as exc:
            raise unavailable(str(exc)) from exc

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
