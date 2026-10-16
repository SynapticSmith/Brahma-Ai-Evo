"""Device routing seam for BrahmaLive (P4-19): smart-home and Brahma-Connect handlers.

Moved verbatim from main.py::BrahmaLive. The host keeps thin delegating wrappers for the
two externally-called handlers (`_handle_smart_home_command`, `_handle_brahma_connect_command`)
and a lazily-built `_device_flow` property; `_extract_launch_app_name` is cluster-internal.
Free names are imported directly (`json`/`re`/`threading` + `actions.brahma_connect` symbols);
`mobile_autopilot` stays a lazy in-body import. `_smart_home`/`ui`/`speak` live on the host.
"""
from __future__ import annotations

import json
import re
import threading

from actions.brahma_connect import connect_execute, connect_list_devices


class DeviceFlow:
    """Smart-home and Brahma-Connect text-command routing; holds a `_host` reference."""

    def __init__(self, host):
        self._host = host

    def handle_smart_home_command(self, text: str, source: str = "local") -> bool:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        
        # Don't touch commands targeted at phone or mobile
        mobile_words = (
            "phone", "mobile", "android", "tablet", "brahma connect",
            "my phone", "my mobile", "my tablet", "my android", "flashlight", "torch"
        )
        if any(word in normalized for word in mobile_words):
            return False

        # Don't hijack PC screen/monitor brightness
        if any(w in normalized for w in ("screen", "monitor", "display", "laptop", "pc")) and "brightness" in normalized:
            return False

        smart_home_words = (
            "fan", "fans", "light", "lights", "lamp", "plug", "socket", "outlet", "bulb",
            "kasa", "atomberg", "bedroom", "living room", "kitchen", "office room",
            "balcony", "bathroom", "hall", "dining", "smart home", "smart-home",
            "ac", "air conditioner", "thermostat"
        )
        has_smart_word = any(word in normalized for word in smart_home_words)
        if not has_smart_word:
            try:
                for d in self._host._smart_home.list_devices():
                    d_name = str(d.get("name", "")).lower()
                    d_room = str(d.get("room", "")).lower()
                    if (d_name and d_name in normalized) or (d_room and d_room in normalized):
                        has_smart_word = True
                        break
            except Exception:
                pass

        if not has_smart_word:
            return False
        try:
            result = self._host._smart_home.execute_command(text)
            detail = str(result.get("detail") or "Smart-home command completed.")
            title = f"Smart Home: {result.get('action', 'control')}"
            plan = [
                "Identify the target device or room",
                "Send the command to the smart-home provider",
                "Verify the new state",
                "Report the result",
            ]
            self._host.ui.update_task_workspace(
                title=title,
                command=text,
                plan=plan,
                status="Executing smart-home command",
                output=detail,
                percent=100,
                source=source,
            )
            self._host.ui.write_log(f"Brahma Evo: {detail}")
            self._host.speak(detail)
            if not self._host.ui.muted:
                self._host.ui.set_state("LISTENING")
            return True
        except Exception as exc:
            message = f"I couldn't control the smart home device: {exc}"
            self._host.ui.write_log(f"ERR: {message}")
            self._host.ui.update_task_workspace(
                title="Smart Home Control",
                command=text,
                plan=[
                    "Identify the target device or room",
                    "Send the command to the smart-home provider",
                    "Verify the new state",
                ],
                status="Smart-home command failed",
                output=message,
                percent=100,
                source=source,
            )
            self._host.speak(message)
            if not self._host.ui.muted:
                self._host.ui.set_state("LISTENING")
            return True

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
        is_flashlight = "flashlight" in normalized or "torch" in normalized
        is_ring_phone = any(p in normalized for p in ("ring phone", "ring my phone", "find my phone", "find phone", "locate phone"))
        has_phone_ref = any(token in normalized for token in (
            "phone", "mobile", "android", "tablet", "brahma connect",
            "my phone", "my mobile", "my tablet", "my android"
        ))

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
                if any(token in normalized for token in (name, device_id, platform, "phone", "mobile", "android", "tablet")):
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
            if (" and " in normalized or " search " in normalized or " play " in normalized or " message " in normalized or " tell " in normalized) and ("open " in normalized or "launch " in normalized or "app" in normalized):
                action = "mobile_autopilot"
                params["instruction"] = text
            elif "flashlight" in normalized and ("turn on" in normalized or "switch on" in normalized or "power on" in normalized or "on" == normalized):
                action = "flashlight_on"
            elif "flashlight" in normalized and ("turn off" in normalized or "switch off" in normalized or "power off" in normalized or "off" == normalized):
                action = "flashlight_off"
            elif "battery" in normalized or "charge" in normalized:
                action = "get_battery"
            elif "open url" in normalized or "open website" in normalized or "go to" in normalized:
                action = "open_url"
            elif any(word in normalized for word in ("launch app", "open app", "start app", "open ", "launch ", "start ", "run ", "bring up ")):
                app_name = self.extract_launch_app_name(text)
                if app_name:
                    action = "launch_app"
                    params["app_name"] = app_name
            elif "volume" in normalized and ("set" in normalized or "change" in normalized or "to " in normalized):
                action = "volume_set"
                match = re.search(r"\b(\d{1,3})\b", normalized)
                if match:
                    params["value"] = int(match.group(1))
            elif "volume" in normalized:
                action = "volume_get"
            elif "info" in normalized or "status" in normalized:
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

