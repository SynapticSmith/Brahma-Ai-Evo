"""Security and functional tests for dashboard/server.py (FIND-007, FIND-028, FIND-039)."""

from __future__ import annotations

import io
from fastapi.testclient import TestClient

import dashboard.server as srv


def test_dashboard_defaults_and_no_uac():
    assert srv.DEFAULT_HOST == "127.0.0.1"
    assert srv.MAX_UPLOAD_MB == 25
    assert ".exe" in srv.BLOCKED_UPLOAD_SUFFIXES
    assert ".ps1" in srv.BLOCKED_UPLOAD_SUFFIXES
    # _ensure_network_access is a safe no-op and never spawns elevated powershell
    assert srv._ensure_network_access() is None


def test_login_rate_limiting_and_one_time_key():
    ds = srv.DashboardServer()
    client = TestClient(ds.app)

    valid_key = ds.new_key()
    # 5 failed attempts trigger rate limit on 6th attempt
    for _ in range(5):
        r = client.post("/login", json={"pin": "WRONG1"})
        assert r.status_code == 401

    r_limited = client.post("/login", json={"pin": valid_key})
    assert r_limited.status_code == 429
    assert "Too many failed attempts" in r_limited.json()["error"]

    # Clear failure log and verify valid one-time key login works once
    ds._login_failures.clear()
    r_ok = client.post("/login", json={"pin": valid_key})
    assert r_ok.status_code == 200
    data = r_ok.json()
    assert data["ok"] is True
    assert data["token"]

    # Reusing the same one-time key fails
    r_reuse = client.post("/login", json={"pin": valid_key})
    assert r_reuse.status_code == 401


def test_web_background_path_traversal_blocked():
    ds = srv.DashboardServer()
    client = TestClient(ds.app)

    # Attempt path traversal outside assets/web_background
    r = client.get("/web_background/../../main.py")
    assert r.status_code == 404


def test_upload_blocks_executable_extensions(tmp_path):
    ds = srv.DashboardServer()
    ds._uploads_dir = tmp_path
    client = TestClient(ds.app)

    key = ds.new_key()
    tok = client.post("/login", json={"pin": key}).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}

    r_bad = client.post(
        "/api/upload",
        headers=headers,
        files={"file": ("payload.ps1", io.BytesIO(b"Write-Host 'bad'"), "text/plain")},
    )
    assert r_bad.status_code == 400
    assert "not permitted" in r_bad.json()["error"]

    r_good = client.post(
        "/api/upload",
        headers=headers,
        files={"file": ("notes.txt", io.BytesIO(b"hello world"), "text/plain")},
    )
    assert r_good.status_code == 200
    assert r_good.json()["ok"] is True
    assert (tmp_path / "notes.txt").read_bytes() == b"hello world"
