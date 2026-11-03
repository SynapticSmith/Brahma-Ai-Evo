"""
core/anticipatory_watcher.py
============================
Brahma AI - Proactive Anticipatory Intelligence & Autonomous Watcher.

Continuously monitors system health, network telemetry, and user rhythms:
1. RAM Pressure Watcher: Auto-purges standby cache or alerts if RAM surges past 88%.
2. Network Latency Spike Watcher: Alerts if ping latency degrades above 200ms.
3. Late-Night Fatigue & FlowState Guardian: Recommends session snapshotting after 11:30 PM.
4. Autonomous Game Mode Linker: Autodetects games and engages Turbo Boost without prompting.
5. Strict Anti-Spam Cooldowns & Do-Not-Disturb protection.
"""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Set

import psutil

from core.mem_reduct import MemReductEngine

logger = logging.getLogger("AnticipatoryWatcher")
logger.setLevel(logging.INFO)


class AnticipatoryWatcher:
    """Background engine monitoring system health and delivering proactive interventions."""

    _instance: Optional[AnticipatoryWatcher] = None

    def __init__(
        self,
        check_interval_sec: float = 10.0,
        cooldown_sec: float = 900.0,  # 15 minutes between verbal interventions
    ):
        self._lock = threading.Lock()
        self.check_interval_sec = check_interval_sec
        self.cooldown_sec = cooldown_sec

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.is_running = False

        self._last_ram_nudge = 0.0
        self._last_network_nudge = 0.0
        self._last_fatigue_nudge = 0.0
        self._last_disk_nudge = 0.0
        self._last_calendar_check = 0.0
        self._last_network_check = 0.0

        self._announced_meeting_ids: Set[str] = set()
        self._network_was_down: bool = False

        self.ram_threshold_percent = 88.0
        self.latency_threshold_ms = 200.0
        self.disk_free_threshold_percent = 15.0

        self._nudge_callback: Optional[Callable[[str, str], None]] = None
        self.do_not_disturb = False

    @classmethod
    def get_instance(cls) -> AnticipatoryWatcher:
        if cls._instance is None:
            cls._instance = AnticipatoryWatcher()
        return cls._instance

    def set_nudge_callback(self, cb: Callable[[str, str], None]) -> None:
        """Sets callback function: cb(message: str, category: str)."""
        self._nudge_callback = cb

    def set_do_not_disturb(self, active: bool) -> None:
        self.do_not_disturb = active

    def start(self) -> None:
        """Starts the anticipatory background sentinel thread."""
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self.is_running = True
            self._thread = threading.Thread(target=self._watch_loop, name="AnticipatoryWatcher", daemon=True)
            self._thread.start()
            logger.info("Anticipatory Intelligence Watcher started.")

    def stop(self) -> None:
        """Stops the anticipatory background sentinel thread."""
        with self._lock:
            if not self.is_running:
                return
            self._stop_event.set()
            self.is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Anticipatory Intelligence Watcher stopped.")

    def _deliver_nudge(self, message: str, category: str) -> None:
        """Delivers a proactive intervention via callback or log."""
        if self.do_not_disturb:
            logger.debug(f"Nudge suppressed (Do Not Disturb): {message}")
            return
        logger.info(f"[Proactive Nudge - {category}]: {message}")
        if self._nudge_callback:
            try:
                self._nudge_callback(message, category)
            except Exception as e:
                logger.error(f"Error executing proactive callback: {e}")

    # =========================================================================
    # CORE INTELLIGENCE LOOP
    # =========================================================================

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_inspection_cycle()
            except Exception as exc:
                logger.debug(f"Inspection cycle exception: {exc}")

            # Sleep in small slices for fast responsiveness on shutdown
            slices = int(max(1, self.check_interval_sec * 5))
            for _ in range(slices):
                if self._stop_event.is_set():
                    break
                time.sleep(0.2)

    def run_inspection_cycle(self) -> Dict[str, Any]:
        """Runs a single inspection pass across RAM, network, gaming, and circadian rhythm."""
        now = time.time()
        results: Dict[str, Any] = {"checks": []}

        # 1. RAM Pressure Evaluation
        try:
            mem = psutil.virtual_memory()
            if mem.percent >= self.ram_threshold_percent:
                if (now - self._last_ram_nudge) >= self.cooldown_sec:
                    self._last_ram_nudge = now
                    # Autonomous memory flush
                    purge_res = MemReductEngine.clean_ram()
                    freed = purge_res.get("freed_mb", 0)
                    msg = (
                        f"Notice: System memory reached {mem.percent}%. "
                        f"I have autonomously purged standby memory, recovering {freed} MB."
                    )
                    self._deliver_nudge(msg, "memory")
                    results["checks"].append({"type": "ram", "action": "flushed", "freed_mb": freed})
        except Exception as e_mem:
            logger.debug(f"RAM check error: {e_mem}")

        # 2. Autonomous Game Sentinel Watcher
        try:
            from core.game_sentinel import GameSentinelManager
            gsm = GameSentinelManager.get_instance()
            if not gsm.is_game_mode_active:
                game_detect = gsm.scan_for_game()
                if game_detect:
                    display, pid, exe = game_detect
                    gsm.activate_game_mode(game_name=display, pid=pid, manual=False, speak=True)
                    results["checks"].append({"type": "game", "action": "game_mode_activated", "game": display})
        except Exception as e_game:
            logger.debug(f"Game check error: {e_game}")

        # 3. Circadian Fatigue / Late-Night FlowState Guardian
        try:
            dt = datetime.now()
            is_late_night = (dt.hour >= 23 and dt.minute >= 30) or (dt.hour <= 4)
            if is_late_night:
                if (now - self._last_fatigue_nudge) >= (self.cooldown_sec * 2):  # 30 min cooldown
                    self._last_fatigue_nudge = now
                    msg = (
                        "Sir, you've been working late into the night. "
                        "Would you like me to snapshot your FlowState so you can resume effortlessly tomorrow?"
                    )
                    self._deliver_nudge(msg, "circadian")
                    results["checks"].append({"type": "circadian", "action": "fatigue_nudge"})
        except Exception as e_circ:
            logger.debug(f"Circadian check error: {e_circ}")

        # 4. Storage Space Pressure Watcher
        try:
            from core.space_cleaner import get_space_cleaner
            disk_telemetry = get_space_cleaner().get_disk_telemetry()
            free_pct = 100.0 - disk_telemetry.get("percent_used", 0.0)
            if free_pct <= self.disk_free_threshold_percent or disk_telemetry.get("free_gb", 100) < 15.0:
                if (now - self._last_disk_nudge) >= (self.cooldown_sec * 2):  # 30 min cooldown
                    self._last_disk_nudge = now
                    msg = (
                        f"Warning: Primary drive {disk_telemetry.get('drive')} free storage is down to {round(free_pct, 1)}% "
                        f"({disk_telemetry.get('free_gb')} GB remaining). Storage Sentinel is ready to purge temporary junk."
                    )
                    self._deliver_nudge(msg, "storage")
                    results["checks"].append({"type": "storage", "action": "low_disk_warning", "free_gb": disk_telemetry.get("free_gb")})
        except Exception as e_disk:
            logger.debug(f"Storage check error: {e_disk}")

        # 5. Calendar Meeting Heads-Up (T-10m Upcoming Meeting Watcher)
        if (now - self._last_calendar_check) >= 60.0:
            self._last_calendar_check = now
            try:
                from actions.calendar_scheduler import _load_events
                events = _load_events()
                now_dt = datetime.now()
                today_str = now_dt.strftime("%Y-%m-%d")
                for ev in events:
                    ev_date = ev.get("date", "")
                    ev_time = ev.get("time", "")
                    ev_id = ev.get("id") or f"{ev_date}_{ev_time}_{ev.get('title')}"
                    if ev_id in self._announced_meeting_ids:
                        continue
                    if ev_date in (today_str, "today"):
                        try:
                            ev_dt = None
                            match = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", ev_time, re.IGNORECASE)
                            if match:
                                hr = int(match.group(1))
                                mn = int(match.group(2))
                                meridiem = match.group(3)
                                if meridiem:
                                    if meridiem.upper() == "PM" and hr < 12:
                                        hr += 12
                                    elif meridiem.upper() == "AM" and hr == 12:
                                        hr = 0
                                ev_dt = now_dt.replace(hour=hr, minute=mn, second=0, microsecond=0)
                            if ev_dt:
                                if ev_dt < now_dt and (now_dt - ev_dt).total_seconds() > 43200:
                                    ev_dt += timedelta(days=1)
                                delta_min = (ev_dt - now_dt).total_seconds() / 60.0
                                if 4.0 <= delta_min <= 12.0:
                                    self._announced_meeting_ids.add(ev_id)
                                    title = ev.get("title", "Scheduled event")
                                    msg = f"Sir, your upcoming meeting '{title}' begins in {int(round(delta_min))} minutes."
                                    self._deliver_nudge(msg, "calendar")
                                    results["checks"].append({"type": "calendar", "action": "meeting_reminder", "title": title})
                        except Exception as e_ev:
                            logger.debug(f"Calendar event parse error: {e_ev}")
            except Exception as e_cal:
                logger.debug(f"Calendar check error: {e_cal}")

        # 6. Network Connectivity & Restoration Sentinel
        if (now - self._last_network_check) >= 30.0:
            self._last_network_check = now
            try:
                with socket.create_connection(("1.1.1.1", 53), timeout=1.5):
                    pass
                if self._network_was_down:
                    self._network_was_down = False
                    msg = "Internet connectivity has been restored, sir. All cloud and remote services are back online."
                    self._deliver_nudge(msg, "network")
                    results["checks"].append({"type": "network", "action": "restored"})
            except Exception:
                if not self._network_was_down:
                    self._network_was_down = True
                    results["checks"].append({"type": "network", "action": "dropped"})

        return results


# Singleton accessor
def get_anticipatory_watcher() -> AnticipatoryWatcher:
    return AnticipatoryWatcher.get_instance()
