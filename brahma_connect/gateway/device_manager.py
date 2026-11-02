from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .authentication import generate_device_id, generate_device_secret, hash_secret, constant_time_equals
from .models import DeviceRecord
from .protocol import now_iso


class DeviceManager:
    def __init__(self, registry_path: Path):
        self.registry_path = Path(registry_path)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._devices: dict[str, DeviceRecord] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.registry_path.exists():
                self._devices = {}
                return
            try:
                raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            devices = raw.get("devices", raw) if isinstance(raw, dict) else {}
            self._devices = {
                device_id: DeviceRecord.from_dict(item)
                for device_id, item in (devices or {}).items()
                if isinstance(item, dict)
            }

    def save(self) -> None:
        with self._lock:
            payload = {"devices": {device_id: record.to_dict() for device_id, record in self._devices.items()}}
            self.registry_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return [record.to_dict() for record in sorted(self._devices.values(), key=lambda item: (not item.online, item.name.lower()))]

    def get(self, device_id: str) -> DeviceRecord | None:
        with self._lock:
            return self._devices.get(str(device_id))

    def remove(self, device_id: str) -> bool:
        with self._lock:
            removed = self._devices.pop(str(device_id), None)
            if removed is None:
                return False
            self.save()
            return True

    def rename(self, device_id: str, new_name: str) -> DeviceRecord | None:
        with self._lock:
            record = self._devices.get(str(device_id))
            if record is None:
                return None
            record.name = str(new_name or "").strip() or record.name
            self.save()
            return record

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            record = self._devices.get(str(device_id))
            if record is None:
                return False
            record.revoked = True
            record.online = False
            record.last_seen = now_iso()
            self.save()
            return True

    def create_from_pairing(
        self,
        *,
        name: str,
        platform: str,
        os_version: str = "",
        agent_version: str = "",
        ip: str = "",
        battery: int | None = None,
        capabilities: list[str] | None = None,
        permissions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[DeviceRecord, str]:
        with self._lock:
            device_id = generate_device_id(platform, name)
            secret = generate_device_secret()
            record = DeviceRecord(
                device_id=device_id,
                name=name or "Unknown Device",
                platform=(platform or "unknown").lower(),
                os_version=os_version,
                agent_version=agent_version,
                ip=ip,
                online=False,
                last_seen=now_iso(),
                battery=battery,
                capabilities=list(capabilities or []),
                permissions=list(permissions or []),
                paired_at=now_iso(),
                secret_hash=hash_secret(secret),
                revoked=False,
                identity_fingerprint=hash_secret(f"{name}:{platform}:{os_version}:{secret}")[:16],
                metadata=dict(metadata or {}),
            )
            self._devices[device_id] = record
            self.save()
            return record, secret

    def authenticate(self, device_id: str, secret: str, *, ip: str = "", connection_id: str = "") -> DeviceRecord | None:
        with self._lock:
            record = self._devices.get(str(device_id))
            if record is None or record.revoked:
                return None
            if not constant_time_equals(record.secret_hash, hash_secret(secret)):
                return None
            record.online = True
            record.last_seen = now_iso()
            record.ip = ip or record.ip
            record.connection_id = connection_id or record.connection_id
            self.save()
            return record

    def authenticate_token(self, token: str, *, ip: str = "", connection_id: str = "") -> tuple[DeviceRecord | None, str]:
        """Authenticate a bearer token in either ``device_id:secret`` or ``secret`` form.

        Returns ``(record, "ok")`` on success, ``(record, "revoked")`` if the device
        is known but has been revoked, and ``(None, "missing" | "invalid")`` on failure.
        """
        token = str(token or "").strip()
        if not token:
            return None, "missing"
        with self._lock:
            if ":" in token:
                device_id, _, secret = token.partition(":")
                record = self._devices.get(str(device_id))
                if record is None:
                    return None, "invalid"
                if not constant_time_equals(record.secret_hash, hash_secret(secret)):
                    return None, "invalid"
                if record.revoked:
                    return record, "revoked"
                record.online = True
                record.last_seen = now_iso()
                record.ip = ip or record.ip
                record.connection_id = connection_id or record.connection_id
                self.save()
                return record, "ok"

            # Secret-only token: check all registered records with constant-time equality
            target_hash = hash_secret(token)
            revoked_record: DeviceRecord | None = None
            for record in self._devices.values():
                if constant_time_equals(record.secret_hash, target_hash):
                    if record.revoked:
                        revoked_record = record
                        continue
                    record.online = True
                    record.last_seen = now_iso()
                    record.ip = ip or record.ip
                    record.connection_id = connection_id or record.connection_id
                    self.save()
                    return record, "ok"
            if revoked_record is not None:
                return revoked_record, "revoked"
            return None, "invalid"

    def mark_offline(self, device_id: str) -> None:
        with self._lock:
            record = self._devices.get(str(device_id))
            if record is None:
                return
            record.online = False
            record.last_seen = now_iso()
            record.connection_id = ""
            self.save()

    def touch(self, device_id: str, *, ip: str = "") -> None:
        with self._lock:
            record = self._devices.get(str(device_id))
            if record is None:
                return
            record.last_seen = now_iso()
            record.online = True
            if ip:
                record.ip = ip
            self.save()

    def update_capabilities(self, device_id: str, capabilities: list[str], permissions: list[str] | None = None) -> DeviceRecord | None:
        with self._lock:
            record = self._devices.get(str(device_id))
            if record is None:
                return None
            record.capabilities = list(dict.fromkeys(capabilities or []))
            if permissions is not None:
                record.permissions = list(dict.fromkeys(permissions or []))
            record.last_seen = now_iso()
            self.save()
            return record

    def resolve(self, query: str) -> list[DeviceRecord]:
        normalized = (query or "").strip().lower()
        if not normalized:
            return []
        with self._lock:
            matches = []
            for record in self._devices.values():
                name = record.name.lower()
                device_id = record.device_id.lower()
                platform = record.platform.lower()
                aliases = {name, device_id, platform}
                if any(token in normalized for token in aliases):
                    matches.append(record)
                    continue
                if "phone" in normalized and platform in {"android", "ios"}:
                    matches.append(record)
                elif "tablet" in normalized and "tablet" in name:
                    matches.append(record)
                elif any(term in normalized for term in ("pc", "laptop", "computer", "windows")) and platform in {"windows", "desktop", "pc"}:
                    matches.append(record)
            return matches
