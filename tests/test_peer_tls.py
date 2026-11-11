"""Tests for Peer TLS, self-signed certificate generation, and SPKI pinning."""

from __future__ import annotations

import datetime

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtensionOID
import pytest

from core.peer.crypto import (
    CertificatePinningError,
    PinnedHTTPSConnection,
    compute_spki_sha256,
    public_key_to_spki_der,
    spki_from_certificate_der,
    spki_from_certificate_pem,
    spki_sha256_from_certificate_der,
)
from core.peer.identity import NodeIdentity


def test_self_signed_certificate_attributes_and_sans():
    """Verify generated certificate has SPIFFE SAN, DNS SAN, and 90-day validity."""
    node = NodeIdentity.create_new(hostname="desktop-master")
    cert_pem, key_pem = node.generate_certificate(days_valid=90)
    assert cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")

    cert = x509.load_pem_x509_certificate(cert_pem)

    # Subject and Issuer match self-signed
    assert cert.subject == cert.issuer

    # Validity range
    now = datetime.datetime.now(datetime.timezone.utc)
    assert cert.not_valid_before_utc <= now
    assert (cert.not_valid_after_utc - now).days >= 89

    # SAN extension
    san_ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    san_values = san_ext.value
    expected_uri = f"spiffe://brahma.local/node/{node.node_id}"
    expected_dns = f"node-{node.node_id}.brahma.local"

    uris = [str(name.value) for name in san_values if isinstance(name, x509.UniformResourceIdentifier)]
    dns_names = [str(name.value) for name in san_values if isinstance(name, x509.DNSName)]

    assert expected_uri in uris
    assert expected_dns in dns_names


def test_spki_hash_consistency_across_key_and_cert():
    """Verify SPKI fingerprint derived from key matches fingerprint from certificate."""
    node = NodeIdentity.create_new()
    cert_pem, _ = node.generate_certificate()

    # From key directly
    key_spki_der = public_key_to_spki_der(node.public_key)
    key_spki_sha256 = compute_spki_sha256(key_spki_der)

    # From cert PEM
    cert_spki_der = spki_from_certificate_pem(cert_pem)
    cert_spki_sha256 = compute_spki_sha256(cert_spki_der)

    # From cert DER
    cert = x509.load_pem_x509_certificate(cert_pem)
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    der_spki_der = spki_from_certificate_der(cert_der)
    der_spki_sha256 = spki_sha256_from_certificate_der(cert_der)

    assert key_spki_der == cert_spki_der == der_spki_der
    assert key_spki_sha256 == cert_spki_sha256 == der_spki_sha256 == node.spki_sha256


def test_pinned_https_connection_verification(monkeypatch):
    """Verify real PinnedHTTPSConnection.connect() accepts valid SPKI and raises CertificatePinningError on mismatch."""
    import http.client
    node = NodeIdentity.create_new()
    cert_pem, _ = node.generate_certificate()
    cert = x509.load_pem_x509_certificate(cert_pem)
    cert_der = cert.public_bytes(serialization.Encoding.DER)

    class MockSocket:
        def __init__(self, der: bytes):
            self._der = der
            self.closed = False

        def getpeercert(self, binary_form: bool = False):
            if binary_form:
                return self._der
            return {}

        def close(self):
            self.closed = True

    # Monkeypatch base HTTPSConnection.connect to attach our mock TLS socket
    def mock_base_connect(self):
        self.sock = MockSocket(cert_der)

    monkeypatch.setattr(http.client.HTTPSConnection, "connect", mock_base_connect)

    # Case 1: Valid pinned SPKI matches: conn.connect() succeeds
    conn = PinnedHTTPSConnection("localhost", port=8443, pinned_spki=node.spki_sha256)
    conn.connect()
    assert conn.sock is not None
    assert conn.sock.closed is False

    # Case 2: Mismatching pinned SPKI: conn_bad.connect() raises CertificatePinningError and closes socket
    conn_bad = PinnedHTTPSConnection("localhost", port=8443, pinned_spki="different_pinned_hash_1234567")
    with pytest.raises(CertificatePinningError) as exc_info:
        conn_bad.connect()

    assert "SPKI pin mismatch" in str(exc_info.value)
    assert conn_bad.sock.closed is True


def test_real_tls_server_handshake_and_pinning_enforcement(tmp_path):
    """Verify live uvicorn TLS server with NodeIdentity certificate authenticates via SPKI pinning."""
    import socket
    import threading
    import time
    from fastapi import FastAPI
    import uvicorn
    from core.peer.crypto import build_pinned_opener

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    ident = NodeIdentity.load_or_create(identity_dir=tmp_path, enforce_machine_binding=False)
    cert_path, key_path = ident.ensure_certificate_files(cert_dir=tmp_path)
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"status": "ok", "node_id": ident.node_id}

    uv_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="critical",
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
    )
    server = uvicorn.Server(uv_config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        start_time = time.time()
        while not server.started:
            if time.time() - start_time > 10.0:
                raise TimeoutError("uvicorn TLS test server failed to start within 10s")
            time.sleep(0.02)

        # 1. Pinned opener with matching SPKI succeeds (TLS 1.3 self-signed)
        opener = build_pinned_opener(ident.spki_sha256)
        with opener.open(f"https://127.0.0.1:{port}/ping") as resp:
            assert resp.status == 200

        # 2. Pinned opener with mismatching SPKI raises CertificatePinningError
        bad_opener = build_pinned_opener("mismatched_spki_fingerprint_00000000000000000000000000000000")
        with pytest.raises(CertificatePinningError):
            bad_opener.open(f"https://127.0.0.1:{port}/ping")

        # 3. Raw PinnedHTTPSConnection succeeds without explicit SSL context
        conn = PinnedHTTPSConnection("127.0.0.1", port=port, pinned_spki=ident.spki_sha256)
        conn.request("GET", "/ping")
        r = conn.getresponse()
        assert r.status == 200
        conn.close()
    finally:
        server.should_exit = True
        th.join(timeout=5)
