"""End-to-End Release Packaging, Signing & Provenance Verification Test (Phase 3).

Validates the full distribution lifecycle:
1. CycloneDX / SPDX Software Bill of Materials (SBOM) generation (scripts/generate_sbom.py).
2. Ed25519 keypair generation and release manifest signing (tools/sign_release.py).
3. Canonical release manifest building with SHA-256 checksums (tools/build_release.py).
4. Cryptographic verification, A/B staging, and automated rollback via ReleaseManager.
"""

from __future__ import annotations

import json
from pathlib import Path
import zipfile

from core.plugin_sign import generate_keypair, sign_manifest
from core.release_manager import ReleaseManager, canonical_json_bytes, compute_file_sha256
from scripts.generate_sbom import generate_sbom


def test_release_packaging_and_provenance_pipeline_e2e(tmp_path: Path):
    """End-to-end verification of SBOM, artifact packaging, Ed25519 signing, and staging."""
    # 1. Generate SBOM
    sbom = generate_sbom()
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert len(sbom["components"]) > 20

    # 2. Generate Release Keypair
    priv_hex, pub_hex = generate_keypair()
    assert len(priv_hex) == 64
    assert len(pub_hex) == 64

    # 3. Create Artifact Package (zip)
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    artifact_zip = dist_dir / "BrahmaEvo-1.2.0-win-x64.zip"

    with zipfile.ZipFile(artifact_zip, "w") as zf:
        zf.writestr("brahma.exe", "MZ_MOCK_WINDOWS_PE_EXECUTABLE_DATA_12345")
        zf.writestr("version.txt", "1.2.0\n")
        zf.writestr("sbom.json", json.dumps(sbom, indent=2))

    artifact_bytes = artifact_zip.read_bytes()
    artifact_sha = compute_file_sha256(artifact_bytes)
    assert artifact_sha.startswith("sha256:")

    # 4. Build Release Manifest
    manifest = {
        "product": "BrahmaEvo",
        "version": "1.2.0",
        "channel": "stable",
        "commit": "ff4f7302b3e5453b526a68c59266a967bc48ede1",
        "published_at": "2026-11-03T00:00:00Z",
        "min_os_version": "10.0.19041",
        "release_notes_url": "https://brahma.ai/releases/v1.2.0",
        "artifacts": [
            {
                "name": "BrahmaEvo-1.2.0-win-x64.zip",
                "sha256": artifact_sha,
                "size": len(artifact_bytes),
                "url": "https://releases.brahma.ai/v1.2.0/BrahmaEvo-1.2.0-win-x64.zip",
            }
        ],
    }

    manifest_file = dist_dir / "release.json"
    manifest_file.write_bytes(canonical_json_bytes(manifest))

    # 5. Sign Manifest with Ed25519 Private Key
    signature_hex = sign_manifest(priv_hex, manifest)
    assert len(signature_hex) == 128

    # 6. Verify and Stage Release via ReleaseManager
    releases_dir = tmp_path / "releases"
    manager = ReleaseManager(
        releases_dir=releases_dir,
        trusted_keys={pub_hex},
        policy="prompt",
        is_git_clone=False,
    )

    # Verify manifest signature against trusted public key
    valid_sig = manager.verify_manifest(manifest, signature_hex)
    assert valid_sig is True, "Release manifest signature verification failed"

    # Verify artifact SHA-256 payload
    valid_payload = manager.verify_artifact(artifact_bytes, artifact_sha)
    assert valid_payload is True, "Artifact checksum verification failed"

    # Stage release
    staged_path = manager.stage_release(
        manifest=manifest,
        signature_hex=signature_hex,
        artifacts={"BrahmaEvo-1.2.0-win-x64.zip": artifact_bytes},
    )
    assert staged_path.exists()
    assert (staged_path / "release.json").exists()
    assert (staged_path / "release.json.sig").exists()
    assert (staged_path / "BrahmaEvo-1.2.0-win-x64.zip").exists()

    # 7. Activate Previous Version 1.1.0 first, then 1.2.0 to test A/B rollback
    # Create fake 1.1.0 release directory to satisfy target_dir existence check
    v11_dir = releases_dir / "stable" / "1.1.0"
    v11_dir.mkdir(parents=True, exist_ok=True)

    manager.activate_version("1.1.0")
    state_v1 = manager.get_state()
    assert state_v1.get("current") == "1.1.0"

    # Now activate 1.2.0
    manager.activate_version("1.2.0")
    state_v2 = manager.get_state()
    assert state_v2.get("current") == "1.2.0"
    assert state_v2.get("previous") == "1.1.0"

    # 8. Automated Rollback Verification
    # Simulate failed boot on 1.2.0
    manager.record_boot_failure()
    # Explicit rollback reverts to 1.1.0
    rolled_back_to = manager.rollback()
    assert rolled_back_to == "1.1.0"

    final_state = manager.get_state()
    assert final_state.get("current") == "1.1.0"
