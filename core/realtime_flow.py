"""Realtime/dashboard seam for BrahmaLive (P4-19).

Moved verbatim from main.py::BrahmaLive: the background-task spawner and the
dashboard/driver coroutines (dashboard serve, remote-command consumer, phone-audio
relay, realtime send loop). The host keeps thin delegating wrappers because
core.session_lifecycle drives them through `self._host.*`. `asyncio`/`traceback`
are imported directly; host state (`_background_tasks`/`_dashboard`/`session`/
`out_queue`/`_phone_active`/`ui`) and host methods (`_on_phone_connected`/
`_on_text_command`) stay on the host.
"""
from __future__ import annotations

import asyncio
import traceback


class RealtimeFlow:
    """Background/dashboard/realtime coroutines; holds a `_host` reference."""

    def __init__(self, host):
        self._host = host

    def spawn_background(self, coro, name: str):
        """Start a loop task that outlives its caller, with a strong ref.

        Fire-and-forget ``asyncio.create_task`` calls keep no reference, so the
        task can be garbage-collected before it finishes and its exception never
        surfaces. Holding it in ``_background_tasks`` until it completes fixes
        both, and the done-callback logs a real failure instead of dropping it.
        """
        task = asyncio.ensure_future(coro)
        self._host._background_tasks.add(task)

        def _done(t: "asyncio.Future") -> None:
            self._host._background_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                try:
                    print(f"[BRAHMA EVO] background task '{name}' failed: {exc!r}")
                    traceback.print_exception(type(exc), exc, exc.__traceback__)
                except Exception:
                    pass

        task.add_done_callback(_done)
        return task

    async def serve_dashboard(self):
        if self._host._dashboard is None:
            self._host.ui.write_log("ERR: Mobile Connect disabled because dashboard dependencies are missing.")
            return
        try:
            self._host._dashboard.set_connect_callback(self._host._on_phone_connected)
            self._host._dashboard.set_wake_callback(lambda: None)
            await self._host._dashboard.serve()
        except Exception as e:
            self._host.ui.write_log(f"ERR: Mobile Connect server failed: {e}")
            traceback.print_exc()

    async def consume_remote_commands(self):
        if self._host._dashboard is None:
            return
        while True:
            text = await self._host._dashboard._command_queue.get()
            if text:
                try:
                    self._host.ui.submit_external_command(text, source="mobile")
                except Exception:
                    self._host._on_text_command(text, source="mobile")

    async def relay_phone_audio(self):
        if self._host._dashboard is None:
            return
        while True:
            frame = await self._host._dashboard._phone_audio_queue.get()
            # Only relay while a live session can actually play it. Queuing into a
            # stale out_queue (no consumer) would just grow without bound while
            # disconnected.
            if self._host.session is None or not self._host.out_queue:
                continue
            self._host._phone_active = True
            try:
                await self._host.out_queue.put(frame)
            finally:
                await asyncio.sleep(0.08)
                if self._host._dashboard._phone_audio_queue.empty():
                    self._host._phone_active = False

    async def send_realtime(self):
        while True:
            msg = await self._host.out_queue.get()
            await self._host.session.send_realtime_input(media=msg)
