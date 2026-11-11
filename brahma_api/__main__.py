"""Headless entry point: ``python -m brahma_api``.

Two jobs, and they never run together:

* ``serve`` starts the loopback HTTP port. It refuses when the config says
  ``enabled: false``; that flag is the only switch, and a command line flag must
  not be able to flip it by accident.
* ``token`` mints, lists, and revokes keys. Minting is a human action at a
  terminal, so it lives here rather than on an HTTP route (design doc §6.6: no
  endpoint changes the token registry).

Nothing is imported for its side effects. ``serve`` imports uvicorn through
``server``; ``token`` never imports the web framework at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from agent.providers import PROVIDERS

from .auth import TokenRecord, TokenRegistry
from .config import API_VERSION, LOOPBACK_HOSTS, PROFILES, BrahmaApiConfig

#: Shown once, on stdout, so a shell can capture it. Warnings go to stderr so
#: ``--json`` output stays parseable.
_TOKEN_WARNING = (
    "Store this token now. It is not recoverable: only its SHA-256 is written "
    "to config/brahma_api/tokens.json."
)


# -- helpers ---------------------------------------------------------------


def _base_dir(raw: Optional[str]) -> Path:
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()


def _load_config(raw_base: Optional[str]) -> BrahmaApiConfig:
    config = BrahmaApiConfig.load(_base_dir(raw_base))
    for problem in config.problems:
        print(f"config: {problem}", file=sys.stderr)
    return config


def _load_registry(config: BrahmaApiConfig) -> TokenRegistry:
    registry = TokenRegistry(config.tokens_path)
    for problem in registry.problems:
        print(f"token: {problem}", file=sys.stderr)
    return registry


def _active(registry: TokenRegistry) -> List[TokenRecord]:
    return [record for record in registry.records() if not record.revoked]


def _apply_overrides(config: BrahmaApiConfig, args: argparse.Namespace) -> Optional[str]:
    """Apply --host/--port with the same rules as the config loader."""
    if args.host:
        host = str(args.host).strip()
        if host.lower() not in LOOPBACK_HOSTS and not config.allow_remote_bind:
            return (
                f"host {host!r} is not loopback and allow_remote_bind is false; "
                "set it in config/brahma_api.json if that is really intended"
            )
        config.host = host
    if args.port is not None:
        port = int(args.port)
        if port < 1024 or port > 65535:
            return f"port {port} is outside 1024..65535"
        config.port = port
    if getattr(args, "peer_port", None) is not None:
        peer_port = int(args.peer_port)
        if peer_port < 1024 or peer_port > 65535:
            return f"peer-port {peer_port} is outside 1024..65535"
        config.peer_port = peer_port
    if hasattr(args, "peer_tls") and args.peer_tls is not None:
        config.peer_tls = bool(args.peer_tls)
    if hasattr(args, "insecure_peer") and args.insecure_peer is not None:
        config.insecure_peer = bool(args.insecure_peer)
    if config.host.lower() not in LOOPBACK_HOSTS and not config.peer_tls and not config.insecure_peer:
        return (
            "non-loopback peer surface requires TLS (ADR-033). "
            "Pass --insecure-peer only if running behind a trusted reverse proxy terminating TLS."
        )
    return None


def _workspace_error(config: BrahmaApiConfig, alias: str) -> str:
    known = ", ".join(sorted(config.workspaces)) or "(none configured)"
    return (
        f"workspace alias {alias!r} is not configured; known aliases: {known}. "
        "Add it to config/brahma_api.json:workspaces."
    )


def _record_row(record: TokenRecord) -> Dict[str, Any]:
    """Everything about a key except the thing that would let you use it."""
    return {
        "id": record.id,
        "label": record.label,
        "profile": record.profile,
        "scopes": list(record.scopes),
        "workspace": record.workspace,
        "provider": record.provider,
        "upstream_model": record.upstream_model,
        "node_id": record.node_id,
        "spki_sha256": record.spki_sha256,
        "created_at": record.created_at,
        "last_used_at": record.last_used_at,
        "revoked": record.revoked,
    }


# -- commands --------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    config = _load_config(args.base_dir)
    problem = _apply_overrides(config, args)
    if problem:
        print(f"brahma_api: {problem}", file=sys.stderr)
        return 2
    if not config.enabled:
        print(
            "brahma_api: disabled. Set \"enabled\": true in "
            f"{config.config_path} (loopback and a bearer token are still required).",
            file=sys.stderr,
        )
        return 2

    registry = _load_registry(config)
    if config.auth_required and not _active(registry):
        print(
            "brahma_api: no active tokens. Mint one first:\n"
            "  python -m brahma_api token mint --preset reader --workspace "
            f"{next(iter(config.workspaces), '<alias>')} --label local",
            file=sys.stderr,
        )
        return 2

    from .server import serve

    server = serve(config)
    peer_app_state = getattr(getattr(server.config, "app", None), "state", None)
    resolved_svc = getattr(peer_app_state, "peer_service", None) if peer_app_state else None
    if getattr(args, "enable_peer_discovery", False):
        if not resolved_svc:
            print(
                "brahma_api: error: --enable-peer-discovery requires peer service, "
                "but PeerService failed to initialize.",
                file=sys.stderr,
            )
            return 2
        resolved_svc.start_discovery()
        print("  peer discovery: ENABLED (UDP beacon port 8444)")
    elif resolved_svc:
        print(f"  peer federation: node_id={resolved_svc.identity.node_id}")

    print(f"brahma_api {API_VERSION} listening on http://{config.host}:{config.port}")
    if config.peer_port != config.port:
        tls_label = "TLS 1.3 SPKI-pinned" if config.peer_tls else "INSECURE cleartext"
        print(f"  peer endpoint: {config.peer_endpoint} ({tls_label})")
    print(
        f"  auth: {'required' if config.auth_required else 'DISABLED'} "
        f"({len(_active(registry))} active key(s))"
    )
    for alias, path in sorted(config.workspaces.items()):
        print(f"  workspace: {alias} -> {path}")
    try:
        server.run()
    except KeyboardInterrupt:
        print("brahma_api: stopped", file=sys.stderr)
    finally:
        if resolved_svc:
            resolved_svc.close()
    return 0


def cmd_token_mint(args: argparse.Namespace) -> int:
    config = _load_config(args.base_dir)
    registry = _load_registry(config)

    preset = str(args.preset).strip().lower()
    if preset == "operator" and not args.confirm_operator:
        print(
            "brahma_api: the operator preset is equivalent to local shell access. "
            "Re-run with --confirm-operator if that is what you mean.",
            file=sys.stderr,
        )
        return 2

    workspace: Optional[str] = None
    if preset == "chat":
        if args.workspace:
            print("brahma_api: the chat preset has no workspace.", file=sys.stderr)
            return 2
    else:
        workspace = str(args.workspace or "").strip()
        if not workspace:
            known = ", ".join(sorted(config.workspaces)) or "(none configured)"
            print(
                f"brahma_api: the {preset} preset needs --workspace <alias>; "
                f"known aliases: {known}",
                file=sys.stderr,
            )
            return 2
        if workspace not in config.workspaces:
            print(f"brahma_api: {_workspace_error(config, workspace)}", file=sys.stderr)
            return 2

    provider = str(args.provider).strip().lower() if args.provider else None
    if provider is not None and provider not in PROVIDERS:
        print(
            f"brahma_api: provider {provider!r} is not one of {PROVIDERS}",
            file=sys.stderr,
        )
        return 2

    try:
        node_id = str(args.node_id).strip().lower() if getattr(args, "node_id", None) else None
        spki_sha256 = (
            str(getattr(args, "spki_sha256", None) or getattr(args, "spki", None) or "").strip().lower()
            or None
        )
        record, plaintext = registry.mint(
            preset,
            label=args.label or "",
            workspace=workspace,
            scopes=args.scope or None,
            provider=provider,
            upstream_model=args.upstream_model or None,
            confirm="operator" if args.confirm_operator else None,
            node_id=node_id,
            spki_sha256=spki_sha256,
        )
    except ValueError as exc:
        print(f"brahma_api: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {"token": plaintext, "record": _record_row(record)},
                indent=2,
            )
        )
    else:
        print(f"token: {plaintext}")
        print(f"id: {record.id}")
        print(f"profile: {record.profile}")
        print(f"workspace: {record.workspace or '-'}")
        if record.node_id:
            print(f"node_id: {record.node_id}")
        if record.spki_sha256:
            print(f"spki_sha256: {record.spki_sha256}")
        print(f"scopes: {', '.join(record.scopes)}")
        if record.label:
            print(f"label: {record.label}")
    print(_TOKEN_WARNING, file=sys.stderr)
    return 0


def cmd_token_list(args: argparse.Namespace) -> int:
    config = _load_config(args.base_dir)
    registry = _load_registry(config)
    records = registry.records()
    if args.json:
        print(json.dumps({"tokens": [_record_row(record) for record in records]}, indent=2))
        return 0
    if not records:
        print("no tokens. Mint one with: python -m brahma_api token mint --preset ...")
        return 0
    header = f"{'id':<14} {'preset':<9} {'workspace':<14} {'label':<20} {'created':<20} state"
    print(header)
    for record in records:
        state = "revoked" if record.revoked else "active"
        print(
            f"{record.id:<14} {record.profile:<9} {(record.workspace or '-'):<14} "
            f"{(record.label or '-'):<20} {record.created_at:<20} {state}"
        )
    return 0


def cmd_token_revoke(args: argparse.Namespace) -> int:
    config = _load_config(args.base_dir)
    registry = _load_registry(config)
    if not registry.revoke(args.token_id):
        print(f"brahma_api: no token with id {args.token_id!r}", file=sys.stderr)
        return 1
    print(f"revoked {args.token_id}")
    return 0


def cmd_config_show(args: argparse.Namespace) -> int:
    config = _load_config(args.base_dir)
    payload = config.to_dict()
    payload["config_path"] = str(config.config_path)
    payload["tokens_path"] = str(config.tokens_path)
    payload["problems"] = list(config.problems)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    for key, value in payload.items():
        print(f"{key}: {value}")
    return 0


# -- parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m brahma_api",
        description="Brahma Evo's OpenAI-compatible loopback port.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the HTTP port (requires enabled: true)")
    serve.add_argument("--base-dir", default=None, help="repository root holding config/")
    serve.add_argument("--host", default=None, help="loopback host override")
    serve.add_argument("--port", type=int, default=None, help="port override (1024-65535)")
    serve.add_argument("--peer-port", type=int, default=None, help="peer federation port override (1024-65535)")
    serve.add_argument(
        "--no-peer-tls",
        dest="peer_tls",
        action="store_false",
        default=True,
        help="disable TLS on the peer surface (loopback only unless --insecure-peer)",
    )
    serve.add_argument(
        "--insecure-peer",
        action="store_true",
        default=False,
        help="allow cleartext HTTP on peer surface even when bound to non-loopback address",
    )
    serve.add_argument(
        "--enable-peer-discovery",
        action="store_true",
        help="enable LAN peer UDP discovery beacon and listener on port 8444",
    )
    serve.set_defaults(func=cmd_serve)

    token = sub.add_parser("token", help="mint, list, or revoke keys")
    token_sub = token.add_subparsers(dest="token_command", required=True)

    mint = token_sub.add_parser("mint", help="create a key and print it once")
    mint.add_argument("--preset", required=True, choices=PROFILES)
    mint.add_argument("--workspace", default=None, help="workspace alias from the config")
    mint.add_argument("--label", default="", help="human note, e.g. 'twitch bot'")
    mint.add_argument(
        "--scope",
        action="append",
        default=None,
        help="repeatable; defaults to chat+models",
    )
    mint.add_argument("--provider", default=None, choices=PROVIDERS)
    mint.add_argument("--upstream-model", default=None, help="provider model id")
    mint.add_argument(
        "--confirm-operator",
        action="store_true",
        help="required for --preset operator",
    )
    mint.add_argument("--node-id", default=None, help="bind token to peer origin node_id")
    mint.add_argument("--spki", "--spki-sha256", dest="spki_sha256", default=None, help="bind token to TLS SPKI SHA-256 fingerprint")
    mint.add_argument("--base-dir", default=None)
    mint.add_argument("--json", action="store_true")
    mint.set_defaults(func=cmd_token_mint)

    listing = token_sub.add_parser("list", help="show keys (never their hashes)")
    listing.add_argument("--base-dir", default=None)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_token_list)

    revoke = token_sub.add_parser("revoke", help="disable a key by id")
    revoke.add_argument("token_id")
    revoke.add_argument("--base-dir", default=None)
    revoke.set_defaults(func=cmd_token_revoke)

    config = sub.add_parser("config", help="inspect the effective configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    show = config_sub.add_parser("show", help="print what the loader accepted")
    show.add_argument("--base-dir", default=None)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_config_show)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    # `python -m brahma_api --port 8010` means serve, matching the design doc.
    if not args_list or args_list[0].startswith("-"):
        args_list.insert(0, "serve")
    parser = build_parser()
    args = parser.parse_args(args_list)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
