"""Shared fixtures for the brahma_api tests.

No network, no model, no Qt: a temp directory is the workspace, the stub
provider is the completer, and tokens are minted through the same registry the
CLI uses. Anything a test needs to fake lives here so the individual files stay
about the contract they assert.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from brahma_api.auth import Principal, TokenRecord, TokenRegistry
from brahma_api.config import BrahmaApiConfig
from brahma_api.runner import AgentRunner
from brahma_api.server import create_app


def make_config(tmp_path: Path, **overrides: Any) -> BrahmaApiConfig:
    """A config whose single workspace alias is ``test``."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    values: Dict[str, Any] = {
        "base_dir": tmp_path,
        "enabled": True,
        "workspaces": {"test": str(workspace)},
        "audit": False,
    }
    values.update(overrides)
    return BrahmaApiConfig(**values)


def mint(
    config: BrahmaApiConfig,
    preset: str = "reader",
    **kwargs: Any,
) -> Tuple[TokenRecord, str]:
    """Mint against a fresh registry, the same way ``token mint`` does."""
    registry = TokenRegistry(config.tokens_path)
    if preset != "chat":
        default_ws = next(iter(config.workspaces.keys()), "test") if config.workspaces else "test"
        kwargs.setdefault("workspace", default_ws)
    kwargs.setdefault("provider", "stub")
    return registry.mint(preset, **kwargs)


def principal(
    profile: str = "reader",
    workspace: Optional[str] = "test",
    **kwargs: Any,
) -> Principal:
    """A Principal without touching disk; useful for pure resolve tests."""
    if profile == "chat":
        workspace = None
    record = TokenRecord(
        id=str(kwargs.pop("id", "tok_test")),
        label="test",
        sha256="a" * 64,
        profile=profile,
        workspace=workspace,
        created_at="2026-01-01T00:00:00+00:00",
        **kwargs,
    )
    return Principal(record)


def build_client(
    config: BrahmaApiConfig,
    factory: Optional[Callable[..., Any]] = None,
    peer_store: Optional[Any] = None,
    peer_identity: Optional[Any] = None,
    peer_registry: Optional[Any] = None,
) -> Any:
    """A TestClient over an app whose runner uses ``factory`` (or the stub)."""
    from fastapi.testclient import TestClient

    registry = TokenRegistry(config.tokens_path)
    runner = AgentRunner(config, completer_factory=factory)
    app = create_app(
        config,
        registry=registry,
        runner=runner,
        peer_store=peer_store,
        peer_identity=peer_identity,
        peer_registry=peer_registry,
    )
    return TestClient(app, raise_server_exceptions=False)


def auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class CaptureCompleter:
    """Records what the loop sent and answers with fixed text."""

    def __init__(self, reply: str = "captured answer") -> None:
        self.reply = reply
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, messages: List[Dict[str, Any]], tools: List[Any]) -> Dict[str, Any]:
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": list(tools or []),
            }
        )
        return {"role": "assistant", "content": self.reply}

    @property
    def system(self) -> str:
        return str(self.calls[0]["messages"][0].get("content") or "") if self.calls else ""
