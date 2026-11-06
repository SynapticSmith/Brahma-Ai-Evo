"""Device routing seam for BrahmaLive (P4-19): Brahma-Connect handler.

Smart-home is deliberately **not** routed here. It is a `smart_home_control`
tool the model chooses; the old pre-LLM matcher claimed semantic authority it
could not have (see docs/ROUTING-AUDIT.md, Phase 0). The host keeps a thin
delegating wrapper for `_handle_brahma_connect_command` and a lazily-built
`_device_flow` property; `extract_launch_app_name` is cluster-internal.
Free names are imported directly (`json`/`re`/`threading` +
`actions.brahma_connect` symbols); `mobile_autopilot` stays a lazy in-body
import. `ui`/`speak` live on the host.
"""
from __future__ import annotations

import json
import re
import threading

from actions.brahma_connect import connect_execute, connect_list_devices
from core import routing


class DeviceFlow:
    """Brahma-Connect text-command routing; holds a `_host` reference."""

    def __init__(self, host):
        self._host = host

    def extract_launch_app_name(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        if not normalized:
            return ""

        prefixes = (
            "launch app ",
            "open app ",
            "start app ",
            "open the app ",
            "launch the app ",
            "start the app ",
            "open ",
            "launch ",
            "start ",
            "run ",
            "bring up ",
        )

        candidate = normalized
        for prefix in prefixes:
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):]
                break

        candidate = re.sub(r"\s+(?:on|in|to)\s+(?:my\s+)?(?:phone|mobile|tablet|android|device)\b.*$", "", candidate).strip()
        candidate = re.sub(r"\b(?:app|application|please|the)\b", " ", candidate).strip()
        candidate = re.sub(r"\s+", " ", candidate)
        return candidate

    def handle_brahma_connect_command(self, text: str, source: str = "local") -> bool:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        
        # Explicit mobile indicators: only route to phone if user explicitly mentions phone/mobile
        # or asks for a phone-exclusive action (flashlight/torch, ring phone).
        is_flashlight = routing.any_phrase(normalized, ("flashlight", "torch"))
        is_ring_phone = routing.any_phrase(
            normalized,
            ("ring phone", "ring my phone", "find my phone", "find phone", "locate phone"),
        )
        has_phone_ref = routing.any_phrase(
            normalized,
            (
                "phone",
                "mobile",
                "android",
                "tablet",
                "brahma connect",
                "my phone",
                "my mobile",
                "my tablet",
                "my android",
            ),
        )

        # Never hijack local PC commands (e.g. open chrome, volume up, pc battery) unless user says 'on phone'
        if not (has_phone_ref or is_flashlight or is_ring_phone):
            return False

        try:
            devices_json = connect_list_devices(parameters={}, player=self._host.ui)
            devices = json.loads(devices_json).get("devices", [])
        except Exception:
            devices = []

        if not devices:
            return False

        try:
            target = ""
            for device in devices:
                name = str(device.get("name", "")).lower()
                device_id = str(device.get("device_id", "")).lower()
                platform = str(device.get("platform", "")).lower()
                if routing.any_phrase(
                    normalized,
                    (
                        name,
                        device_id,
                        platform,
                        "phone",
                        "mobile",
                        "android",
                        "tablet",
                    ),
                ):
                    target = str(device.get("device_id") or device.get("name") or "").strip()
                    break
            if not target and (has_phone_ref or is_flashlight or is_ring_phone) and len(devices) == 1:
                target = str(devices[0].get("device_id") or devices[0].get("name") or "").strip()
            if not target:
                return False

            action = None
            params: dict[str, object] = {}
            
            # Check for complex multi-step mobile workflow first
            # Examples: "open X and do Y", "search for Z on X", "message X on Y"
            if (
                routing.any_phrase(normalized, ("and", "search", "play", "message", "tell"))
                and routing.any_phrase(normalized, ("open", "launch", "app"))
            ):
                action = "mobile_autopilot"
                params["instruction"] = text
            elif routing.has_phrase(normalized, "flashlight") and (
                routing.any_phrase(normalized, ("turn on", "switch on", "power on"))
                or routing.is_phrase(normalized, "on")
            ):
                action = "flashlight_on"
            elif routing.has_phrase(normalized, "flashlight") and (
                routing.any_phrase(normalized, ("turn off", "switch off", "power off"))
                or routing.is_phrase(normalized, "off")
            ):
                action = "flashlight_off"
            elif routing.any_phrase(normalized, ("battery", "charge")):
                action = "get_battery"
            elif routing.any_phrase(normalized, ("open url", "open website", "go to")):
                action = "open_url"
            elif routing.any_phrase(
                normalized,
                ("launch app", "open app", "start app", "open", "launch", "start", "run", "bring up"),
            ):
                app_name = self.extract_launch_app_name(text)
                if app_name:
                    action = "launch_app"
                    params["app_name"] = app_name
            elif routing.has_phrase(normalized, "volume") and routing.any_phrase(
                normalized, ("set", "change", "to")
            ):
                action = "volume_set"
                match = re.search(r"\b(\d{1,3})\b", normalized)
                if match:
                    params["value"] = int(match.group(1))
            elif routing.has_phrase(normalized, "volume"):
                action = "volume_get"
            elif routing.any_phrase(normalized, ("info", "status")):
                action = "get_device_info"
                
            if not action:
                return False

            if action == "mobile_autopilot":
                # Route complex commands to the autopilot loop
                def _run_autopilot():
                    try:
                        from actions.mobile_autopilot import mobile_autopilot
                        result_json = mobile_autopilot({"target": target, "instruction": params["instruction"]}, player=self._host.ui, speak=self._host.speak)
                        print(f"[Autopilot Result]: {result_json}")
                        try:
                            result_dict = json.loads(result_json)
                            if not result_dict.get("success"):
                                print(f"[Autopilot Error]: {result_dict.get('error') or result_dict}")
                                self._host.speak(f"Autopilot encountered an error: {result_dict.get('error', 'unknown error')}")
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"Autopilot failed: {e}")
                    finally:
                        try:
                            self._host.ui.set_state("IDLE")
                        except Exception:
                            pass
                threading.Thread(target=_run_autopilot, daemon=True).start()
                return True

            if action == "launch_app":
                app_name = str(params.get("app_name") or "").strip()
                if not app_name:
                    return False
            if action == "open_url":
                m = re.search(r"(https?://\S+)", text, re.IGNORECASE)
                if m:
                    params["url"] = m.group(1)
                else:
                    return False

            payload = {
                "device": target,
                "action": action,
                "parameters": params,
            }
            result_json = connect_execute(parameters=payload, player=self._host.ui)
            result = json.loads(result_json)
            if result.get("success", False):
                detail = str(result.get("detail") or result.get("error") or "Device command completed.")
                title = f"Brahma Connect: {action}"
                self._host.ui.update_task_workspace(
                    title=title,
                    command=text,
                    plan=[
                        "Identify the paired phone or device",
                        "Route the command through Brahma Connect",
                        "Verify the device response",
                        "Report the result",
                    ],
                    status="Executing device command",
                    output=detail,
                    percent=100,
                    source=source,
                )
                self._host.ui.write_log(f"Brahma Evo: {detail}")
                self._host.speak(detail)
                if not self._host.ui.muted:
                    self._host.ui.set_state("LISTENING")
                return True

            self._host.ui.write_log(f"ERR: Brahma Connect command failed: {result.get('error') or 'Unknown error'}")
            return False
        except Exception:
            return False

