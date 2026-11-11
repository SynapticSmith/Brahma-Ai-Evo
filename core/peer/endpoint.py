"""Announced-endpoint resolution for peer federation (ADR-034, Invariant S-07).

A node may bind a wildcard address (``0.0.0.0`` / ``::``) to listen on every
interface, but the wildcard itself is not routable: a pairing URI, an A2A agent
card or a discovery beacon that carries ``https://0.0.0.0:8443`` points nowhere
for the peer that reads it. This module is the single place that turns a bind
host into an address another machine can dial, so the card, the offer, the
beacon and the UI cannot disagree.

Stdlib only; no Qt, no provider SDK.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional
import urllib.parse

#: Values that mean "every interface", never "dial me here".
WILDCARD_HOSTS = ("", "*", "0.0.0.0", "::", "[::]")


def is_wildcard_host(host: Optional[str]) -> bool:
    """True when ``host`` is a bind-everything placeholder, not a dialable host."""
    if host is None:
        return True
    text = str(host).strip().strip("[]").lower()
    if not text:
        return True
    if text in WILDCARD_HOSTS:
        return True
    try:
        return ipaddress.ip_address(text).is_unspecified
    except ValueError:
        return False


def primary_lan_ip() -> Optional[str]:
    """Best-effort IPv4 address of the interface that reaches the LAN.

    A UDP ``connect`` sends no packet; it only asks the routing table which
    local address would be used, which works with no server on the far side.
    Returns ``None`` when the machine has no usable address.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # TEST-NET-1: reserved for documentation, never routed.
            probe.connect(("192.0.2.1", 9))
            addr = probe.getsockname()[0]
        if addr and not is_wildcard_host(addr):
            return addr
    except OSError:
        pass
    try:
        addr = socket.gethostbyname(socket.gethostname())
        if addr and not is_wildcard_host(addr):
            return addr
    except OSError:
        pass
    return None


def announced_host(bind_host: Optional[str], announced: Optional[str] = None) -> str:
    """Return the host to advertise for a node bound to ``bind_host``.

    ``announced`` (operator configuration) wins when set; otherwise a wildcard
    bind is replaced by this machine's primary LAN address, and a specific bind
    address is advertised unchanged. Never returns a wildcard.
    """
    if announced is not None and str(announced).strip():
        return str(announced).strip().strip("[]")
    host = str(bind_host or "").strip().strip("[]")
    if host and not is_wildcard_host(host):
        return host
    return primary_lan_ip() or "127.0.0.1"


def format_host(host: str) -> str:
    """Bracket an IPv6 literal so it can sit in a URL authority."""
    text = str(host).strip()
    if ":" in text and not text.startswith("["):
        return f"[{text}]"
    return text


def normalize_endpoint(
    endpoint: Optional[str],
    bind_host: Optional[str] = None,
    announced: Optional[str] = None,
) -> str:
    """Rewrite a wildcard host inside ``endpoint`` to a dialable one.

    A non-wildcard endpoint is returned byte-for-byte; only the authority is
    touched, so a path or query survives.
    """
    text = str(endpoint or "").strip()
    if not text:
        return text
    parsed = urllib.parse.urlsplit(text)
    if parsed.hostname and not is_wildcard_host(parsed.hostname):
        return text
    host = announced_host(
        bind_host if bind_host is not None else parsed.hostname, announced
    )
    netloc = f"{format_host(host)}:{parsed.port}" if parsed.port else format_host(host)
    return urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )
