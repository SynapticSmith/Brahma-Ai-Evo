

"""Session state machine and lifecycle for BrahmaLive (P4-19 seam B).

Extracted verbatim from main.py's BrahmaLive class. The host owns all state
(session, queues, control flags, ui, loop); this module owns the connect /
standby / wake / idle / local-voice control flow.

Dependency injection: main-local helpers that live in main.py but not on the
host (module-level functions and constants) are supplied once as ``deps`` and
bound as module globals, so the moved bodies stay byte-identical to the
originals apart from the documented rewrite that rebinds every self.NAME
attribute reference (except the nine methods moved here) to the host.
"""
from __future__ import annotations

def _get_live_voice_name() -> str:
    try:
        from memory import config_manager
        val = str(config_manager.get_setting("gemini_live_voice", "Charon") or "Charon").strip()
        return val if val in ("Charon", "Puck", "Aoede", "Fenrir", "Kore") else "Charon"
    except Exception:
        return "Charon"


import asyncio
import threading
import time
import traceback

from core import audio_devices
from memory import config_manager
from google import genai
from google.genai import types
from memory.memory_manager import load_memory, format_memory_for_prompt


# Capture-level energy gate shared by the local-voice and standby wake-word
# loops: a block louder than this counts as speech. Kept module-level so the
# acceptance harness that drives the REAL microphone can force phrase sealing
# (a quiet room never crosses it) instead of depending on ambient noise.
_SPEECH_ENERGY_THRESHOLD = 300.0


class SessionLifecycle:
    """Session state machine / lifecycle seam of :class:`main.BrahmaLive`."""

    def __init__(self, host, *, deps):
        self._host = host
        globals().update(deps)

    def set_live_mode(self, mode: str):
        """Change ONLY the mode preference.

        This used to also overwrite the connection gate, which made the mode
        selector and the Connect/Disconnect button fight each other: selecting
        Always On forced the gate open, and selecting On Call forced it shut.
        Mode and connection are separate controls, so this touches only the mode
        and then applies the transition the new mode implies.
        """
        mode = "on_call" if str(mode) == "on_call" else "always_on"
        with self._host._control_lock:
            was_on_call = self._host._live_mode == "on_call"
            self._host._live_mode = mode
            if mode == "on_call":
                # On Call keeps no live socket unless a call is actually placed.
                self._host._manual_call_active = False

        if self._host._should_standby():
            # Reflect the change immediately rather than waiting for the session
            # teardown to reach the loop, so the indicator never sits on
            # LISTENING while the socket is on its way down.
            self._host.ui.set_state("STANDBY")
            self._host._disconnect_live()
        elif was_on_call:
            self._host._stop_wakeword_listener()
            self._host._request_connect()
        # Abort any in-flight reconnect backoff so the new mode takes effect now.
        self._host._wake_control()
    async def _pause_until(self, seconds: float) -> None:
        """Sleep up to ``seconds``, returning early on a control change.

        Order matters: clear the event *before* sampling the sequence, so a
        control change that lands between the two is either seen in the sequence
        (and we return now) or sets the event after the clear (and ``wait``
        returns now). Sampling first would let a change be cleared away unseen.
        """
        event = getattr(self._host, "_control_wake", None)
        if event is None:
            await asyncio.sleep(seconds)
            return
        event.clear()
        with self._host._control_lock:
            seen = getattr(self._host, "_control_seq", 0)
        if seen != getattr(self._host, "_pause_seen", 0):
            # A control change arrived while we were connecting; honour it now
            # instead of sleeping through the whole backoff.
            self._host._pause_seen = seen
            return
        try:
            await asyncio.wait_for(event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        with self._host._control_lock:
            self._host._pause_seen = getattr(self._host, "_control_seq", 0)
    async def _run_local_voice_session(self):
        """Voice session for a local provider: mic -> whisper STT -> local model -> TTS.

        Honours the same connect/disconnect contract as the Gemini socket, so
        Always On, On Call, Connect and Disconnect all mean the same thing
        whichever provider is selected.
        """
        import sounddevice as sd

        self._host.session = None
        self._host._loop = None
        mic_name = config_manager.get_input_device()
        mic_dev = audio_devices.resolve(mic_name, "input") if mic_name else None
        available = audio_devices.list_devices("input")
        if mic_dev is None and available:
            mic_name = available[0]
            mic_dev = audio_devices.resolve(mic_name, "input")
        if mic_dev is None:
            self._host.ui.write_log(
                "SYS: No usable microphone at the configured sample rate. "
                "Local voice is paused; text input still works."
            )
            while not self._host._take_disconnect_request():
                await asyncio.sleep(1.0)
            return

        from core import speech_stt

        engine = speech_stt.resolve_available()
        self._host.ui.set_state("LISTENING")
        self._host._report_voice_status(True, f"mic via {engine}")
        self._host.ui.write_log(
            f"SYS: Local voice active (STT: {engine}). "
            "Always On / On Call / Connect / Disconnect apply as usual."
        )
        print(f"[BRAHMA EVO] 🎤 Local voice session started (STT={engine}).")

        speech_buffer = bytearray()
        speaking = {"on": False}
        # Owned by the enclosing scope and shared with the callback via
        # nonlocal: every sounddevice callback invocation is a fresh frame, so
        # a bare ``silence_chunks`` assigned only inside the branches below
        # would be a local that is unbound on the first silence chunk
        # (UnboundLocalError -> phrase never sealed -> local mic dead).
        # core.audio_io.callback already uses this exact pattern.
        silence_chunks = 0

        def _callback(indata, frames, time_info, status):
            nonlocal silence_chunks
            # A raise here is swallowed by cffi ("Exception ignored from cffi
            # callback") and the stream then goes silent forever, which is what
            # a bad buffer conversion looks like from the outside. Nothing in
            # this callback is allowed to propagate.
            try:
                if self._host._take_disconnect_request():
                    return
                # sd.InputStream, NOT RawInputStream: with RawInputStream + dtype="int16"
                # sounddevice hands the callback a raw C buffer, and `.tobytes()`
                # raised AttributeError. Inside a cffi callback that exception is
                # swallowed ("Exception ignored from cffi callback"), so the mic
                # went permanently silent with no visible cause. InputStream
                # converts to a numpy array - which is exactly what the working
                # Gemini path already uses.
                raw, level = _pcm_block(indata)
                try:
                    self._host.ui.set_audio_level(min(1.0, level / 1200.0))
                except Exception:
                    pass
                if speaking["on"]:
                    # While a reply is being spoken, ignore the mic so we do not
                    # transcribe our own voice.
                    speech_buffer.clear()
                    silence_chunks = 0
                    return
                if level > _SPEECH_ENERGY_THRESHOLD:
                    speech_buffer.extend(raw)
                    silence_chunks = 0
                elif speech_buffer:
                    silence_chunks += 1
                    if silence_chunks >= 3:
                        captured = bytes(speech_buffer)
                        speech_buffer.clear()
                        silence_chunks = 0
                        if len(captured) > (SEND_SAMPLE_RATE * 2 * 0.5):
                            threading.Thread(
                                target=self._host._local_voice_turn,
                                args=(captured, speaking),
                                daemon=True, name="local-voice-turn",
                            ).start()
            except Exception as exc:
                # Keep this ASCII and guarded: an exception while reporting an
                # exception is exactly what cffi cannot survive.
                try:
                    print(f"[BRAHMA EVO] Local mic callback error: {exc}")
                except Exception:
                    pass

        try:
            # InputStream (not RawInputStream) so `indata` is a numpy int16
            # array and .tobytes() exists. The Gemini path has always used
            # InputStream for this reason.
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                blocksize=int(SEND_SAMPLE_RATE * 0.1),
                device=mic_dev, dtype="int16", channels=1, callback=_callback,
            ):
                while not self._host._take_disconnect_request():
                    await asyncio.sleep(0.1)
        finally:
            self._host.set_speaking(False)
            self._host._report_voice_status(False)
            try:
                self._host.ui.set_audio_level(0.0)
            except Exception:
                pass
            print("[BRAHMA EVO] Local voice session ended.")
    def _wakeword_loop(self):
        """Local-only microphone listener used while On Call is in standby.

        In standby the Gemini socket is down, so the in-session wake word in
        _listen_audio can never fire -- this thread is the only path that can
        bring the session back up. It owns the microphone exclusively while
        disconnected, which is exactly when the live InputStream is not open,
        so the two never contend for the device.

        Capture is sounddevice -- the same backend the live and local-voice
        paths use -- so this thread needs no PyAudio C extension. Frames are
        sealed into phrases by the same energy gate the local-voice loop uses,
        then wrapped in an ``sr.AudioData`` so ``_transcribe_wakeword`` (and its
        pluggable STT engine) keeps its exact contract.
        """
        stop = self._host._wake_stop
        try:
            import queue
            import sounddevice as sd
            import speech_recognition as sr
        except Exception:
            try:
                self._host.ui.write_log(
                    "SYS: On Call wake word unavailable "
                    "(pip install SpeechRecognition sounddevice); use the Connect button."
                )
            except Exception:
                pass
            return

        recognizer = sr.Recognizer()
        blocks: "queue.Queue" = queue.Queue(maxsize=200)

        def _callback(indata, frames, time_info, status):
            # A raise here is swallowed by cffi ("Exception ignored from cffi
            # callback") and the stream then goes silent forever. _pcm_block
            # already tolerates every buffer shape sounddevice may hand us, and
            # nothing in this callback is allowed to propagate. Drop on a full
            # queue instead of growing it without bound while a phrase is being
            # transcribed.
            try:
                blocks.put_nowait(_pcm_block(indata))
            except Exception:
                pass

        # Resolve the configured microphone the way the local-voice path does;
        # an unset/renamed device falls back to the system default (device=None),
        # exactly what sr.Microphone() used to select.
        mic_name = config_manager.get_input_device()
        mic_dev = audio_devices.resolve(mic_name, "input") if mic_name else None

        print("[BRAHMA EVO] ON-CALL: wake word armed.")
        try:
            while (
                stop is not None and not stop.is_set()
                and self._host._wake_stop is stop
            ):
                try:
                    with sd.InputStream(
                        samplerate=SEND_SAMPLE_RATE,
                        blocksize=int(SEND_SAMPLE_RATE * 0.1),
                        device=mic_dev, dtype="int16", channels=1,
                        callback=_callback,
                    ):
                        speech_buffer = bytearray()
                        silence_chunks = 0
                        while (
                            stop is not None and not stop.is_set()
                            and self._host._wake_stop is stop
                        ):
                            try:
                                raw, level = blocks.get(timeout=0.1)
                            except queue.Empty:
                                continue
                            if level > _SPEECH_ENERGY_THRESHOLD:
                                speech_buffer.extend(raw)
                                silence_chunks = 0
                            elif speech_buffer:
                                silence_chunks += 1
                            # A phrase ends on ~0.3 s of silence, or is cut at 4 s
                            # so a noisy room cannot grow the buffer forever.
                            if speech_buffer and (
                                silence_chunks >= 3
                                or len(speech_buffer) >= SEND_SAMPLE_RATE * 2 * 4
                            ):
                                captured = bytes(speech_buffer)
                                speech_buffer.clear()
                                silence_chunks = 0
                                if len(captured) > (SEND_SAMPLE_RATE * 2 * 0.3):
                                    audio = sr.AudioData(captured, SEND_SAMPLE_RATE, 2)
                                    transcript = self._host._transcribe_wakeword(
                                        recognizer, audio
                                    )
                                    if _standby_wakeword_detected(transcript):
                                        print("[BRAHMA EVO] ON-CALL: wake word heard - connecting.")
                                        self._host.set_live_call(True)
                                        return
                except Exception as e:
                    if stop is None or stop.is_set():
                        return
                    print(f"[BRAHMA EVO] ON-CALL: wake word listener retrying: {e}")
                    stop.wait(1.0)
        finally:
            # Only clear the slot if we are still the current listener, so a
            # freshly started one is never clobbered by a slow predecessor.
            if self._host._wake_thread is threading.current_thread():
                self._host._wake_thread = None
                self._host._wake_stop = None
    async def _serve_live_session(self, session):
        """Hold one Gemini Live session open until it errors or is cancelled."""
        async with asyncio.TaskGroup() as tg:
            self._host.session        = session
            self._host._loop          = asyncio.get_running_loop()
            self._host.audio_in_queue = asyncio.Queue()
            self._host.out_queue      = asyncio.Queue()  # Fix: removed maxsize=10 to prevent dropping packets

            print("[BRAHMA EVO] ✅ Connected.")
            try:
                self._host.ui.boot_set_step_status("Connect AI backend", "done")
                self._host.ui.boot_set_progress(75, "AI backend connected")
            except Exception:
                pass
            self._host.ui.set_state("LISTENING")
            self._host.ui.write_log("SYS: Brahma Evo online.")

            tg.create_task(self._host._send_realtime())
            tg.create_task(self._host._listen_audio())
            tg.create_task(self._host._receive_audio())
            tg.create_task(self._host._play_audio())
            if not self._host._startup_briefing_started:
                self._host._startup_briefing_started = True
                # The startup briefing is unprompted speech, so it follows the
                # same Identity & Profile toggle as idle check-ins. The panel
                # and log line still render through `_speak_daily_briefing`.
                threading.Thread(
                    target=_speak_daily_briefing,
                    args=(
                        self._host.ui,
                        lambda text: self._host.speak(text, proactive=True),
                    ),
                    daemon=True,
                    name="daily-briefing",
                ).start()
            try:
                self._host.ui.boot_set_step_status("Initialize audio", "done")
                self._host.ui.boot_set_progress(92, "Audio subsystems online")
            except Exception:
                pass
            # finalize
            try:
                self._host.ui.boot_set_step_status("Finalize startup", "done")
                self._host.ui.boot_set_progress(100, "Startup complete")
            except Exception:
                pass
    def _idle_speech_loop(self):
        from core.identity import identity
        try:
            from actions.proactive import ProactiveEngine
            engine = ProactiveEngine(min_silence_secs=300, check_cooldown=600)  # Shorter defaults for testing
        except ImportError:
            engine = None

        while True:
            time.sleep(60.0)
            if not engine:
                continue

            try:
                if self._host.ui.muted or self._host._is_speaking or self._host._meeting_active or self._host._pending_attention:
                    continue

                # Check if it should trigger using the engine's time monotonic logic
                if not engine.should_trigger(self._host._last_activity):
                    continue

                # The "Proactive Spoken Interjections" toggle in Identity &
                # Profile must gate every proactive voice path, including this
                # idle check-in. It sends straight to the live socket (there is
                # no speak(proactive=True) call to enforce it), so an unchecked
                # toggle used to keep starting `[PROACTIVE_CHECK]` turns.
                if not identity.is_proactive():
                    continue

                engine.mark_triggered()
                prompt = engine.build_prompt(memory={})

                if self._host.session and self._host._loop:
                    import asyncio
                    async def _send():
                        try:
                            await self._host.session.send_client_content(turns={"parts": [{"text": prompt}]}, turn_complete=True)
                        except Exception as e:
                            print(f"[Proactive] Error: {e}")
                    asyncio.run_coroutine_threadsafe(_send(), self._host._loop)
                    self._host._reset_idle_activity()

            except Exception as e:
                print(f"[Proactive] Error: {e}")
    def _connect_tool_voice(self, name: str, raw_result: str) -> str | None:
        from core.desktop_host import connect_tool_voice

        return connect_tool_voice(name, raw_result)
    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        loc_ctx = ""
        try:
            from core.device_location import get_device_city
            dev_city = get_device_city(default="")
            if dev_city and dev_city != "Local Area":
                loc_ctx = (
                    f"[DEVICE PHYSICAL LOCATION]\n"
                    f"Current device location: {dev_city}\n"
                    f"Use this location for local weather, time zone, and neighborhood context.\n\n"
                )
        except Exception:
            pass

        parts = [time_ctx]
        if loc_ctx:
            parts.append(loc_ctx)
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)
        parts.append(
            "🚨 ABSOLUTELY CRITICAL TOOL USAGE RULE 🚨: You only have a LIMITED set of functions declared to you. "
            "Tools found via search_tools/list_tools/get_skill_manual that are NOT in your declared functions "
            "MUST be called through call_tool(tool_name='exact_name', arguments={...}). "
            "NEVER call undeclared tools directly - doing so will cause the session to crash with 'Requested entity was not found'. "
            "When in doubt, use search_tools to find the right tool, then use call_tool to run it. THIS IS NON-NEGOTIABLE."
        )
        parts.append(
            "Wake-word mode: if the microphone is muted, still listen for the words 'Brahma Evo', 'hey', 'hi', and 'hello'. "
            "When you hear one of these activation cues, keep the session friendly and concise, "
            "and wait for the user's next command. "
            "IMPORTANT: Do NOT speak an unprompted generic greeting (like 'Thank you, how can I help you?') upon connecting. "
            "Remain completely silent until the user speaks to you or asks a question."
        )

        # Workspace-scoped harness prompt: the same AGENTS.md/BRAHMA.md walk and
        # project-skills catalogue the CLI gets from
        # ``agent.prompts.build_system_prompt``. This is the only place the Live
        # instruction is assembled, so without it a voice session never sees
        # either block (and ``load_project_skill`` has no catalogue to use).
        project_ctx = ""
        try:
            from agent.prompts import project_context
            from agent.runtime import current as current_agent_runtime

            project_ctx = project_context(current_agent_runtime().workspace)
        except Exception:
            project_ctx = ""
        if project_ctx:
            print(
                f"[BRAHMA EVO] \U0001f4c4 live prompt += {len(project_ctx)} "
                "chars of project context"
            )
            parts.append(project_ctx)

        # Resident map of the user's second brain. The Live socket cannot swap
        # its system instruction mid-session, so voice gets the map plus the
        # advertised brain_search tool; text surfaces additionally get the
        # per-turn retrieval block injected by AgentLoop.
        brain_ctx = ""
        try:
            from agent.prompts import brain_prompt_block

            brain_ctx = brain_prompt_block(automatic=False)
        except Exception:
            brain_ctx = ""
        if brain_ctx:
            print(
                f"[BRAHMA EVO] \U0001f9e0 live prompt += {len(brain_ctx)} "
                "chars of second-brain map"
            )
            parts.append(brain_ctx)

        # Specialist-template index. main.py advertises ``list_subagents`` on the
        # Live surface but never the dispatch tools, so this block must be part
        # of the voice instruction or the model cannot know the library exists.
        # ``dispatch_available=False`` keeps the wording honest: a lookup is
        # possible here, a child loop is not.
        subagent_ctx = ""
        try:
            from agent.subagent_library import prompt_block as subagent_prompt_block

            subagent_ctx = subagent_prompt_block(dispatch_available=False)
        except Exception:
            subagent_ctx = ""
        if subagent_ctx:
            print(
                f"[BRAHMA EVO] \U0001f9e9 live prompt += {len(subagent_ctx)} "
                "chars of subagent library index"
            )
            parts.append(subagent_ctx)

        # Optional instruction blocks for the LIVE layer. This is the only place
        # Gemini Live's system instruction is assembled, so anything a voice
        # session is meant to follow has to be composed here.
        live_extra = _injected_instructions("live")
        if live_extra:
            print(f"[BRAHMA EVO] 💉 live prompt += {len(live_extra)} chars of instructions")
            parts.append(live_extra.strip())

        # --- Tool index: advertise a bounded, relevant tool surface ---------
        # Every tool used to be sent on the setup frame. With Calyx + Synapse
        # connected that was 173 declarations, past Gemini's hard 128 limit, and
        # the handshake failed with 1007 / Invalid Argument. Gemini Live also
        # cannot swap tools mid-session, so the surface must be small *and* the
        # rest reachable through search_tools / call_tool.
        # The last utterance lives on the host (core/audio_io.py writes it when
        # input transcription arrives); this seam is a separate object, so the
        # old ``getattr(self, ...)`` always read "" and every reconnect started
        # cold. Reading it here is still connect-time state: the first socket
        # has no prior turn, a reconnect is seeded with what the user asked
        # before the drop. It never re-seeds an open live session.
        last_intent = (getattr(self._host, "_last_user_text", "") or "")
        try:
            from core import tool_bridge

            payload = tool_bridge.build_session_payload(last_intent)
        except Exception as exc:
            print(f"[BRAHMA EVO] ⚠️ Tool index unavailable ({exc}); using legacy tool list.")
            payload = {
                "declarations": list(TOOL_DECLARATIONS),
                "skill_context": "",
                "deferred": 0,
                "indexed": False,
            }

        tool_declarations = payload["declarations"]
        if payload.get("skill_context"):
            # Just-in-time context: usage rules for the tool groups actually
            # advertised this session, and nothing else.
            parts.append(payload["skill_context"])

        print(
            f"[BRAHMA EVO] 🧰 Advertising {len(tool_declarations)} tool(s); "
            f"{payload.get('deferred', 0)} deferred and reachable via search_tools."
        )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": tool_declarations}],
            session_resumption=types.SessionResumptionConfig(handle=getattr(self._host, '_resume_handle', None)),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=_get_live_voice_name()
                    )
                )
            ),
        )
    async def run(self):
        # announce boot steps to UI overlay (thread-safe wrappers)
        try:
            self._host.ui.boot_add_step("Load configuration")
            self._host.ui.boot_add_step("Start attention monitor")
            self._host.ui.boot_add_step("Start dashboard server")
            self._host.ui.boot_add_step("Initialize audio")
            self._host.ui.boot_add_step("Connect AI backend")
            self._host.ui.boot_add_step("Finalize startup")
            self._host.ui.boot_set_progress(3, "Preparing startup...")
        except Exception:
            pass

        self._host._attention_monitor.start()
        try:
            self._host.ui.boot_set_step_status("Start attention monitor", "done")
            self._host.ui.boot_set_progress(12, "Attention monitor online")
        except Exception:
            pass
        if self._host._dashboard is not None:
            if not self._host._dashboard_started:
                self._host._dashboard_started = True
                self._host._spawn_background(self._host._serve_dashboard(), "dashboard")
                try:
                    self._host.ui.boot_set_step_status("Start dashboard server", "done")
                    self._host.ui.boot_set_progress(22, "Mobile connect server running")
                except Exception:
                    pass
            self._host._spawn_background(self._host._consume_remote_commands(), "remote-commands")
            self._host._spawn_background(self._host._relay_phone_audio(), "phone-audio-relay")
        try:
            self._host.ui.boot_set_progress(36, "Initializing AI client")
        except Exception:
            pass

        # Built lazily inside the Gemini branch. A Local/offline provider must
        # not require a Gemini key, and a missing key must surface as a bounded
        # retry instead of killing the voice loop before its first iteration.
        client = None
        api_key_warned = False

        self._host._ui_loop = asyncio.get_running_loop()
        # Built on the loop that awaits it. A control change fires it to cut a
        # reconnect pause short, so On Call / Disconnect apply without waiting
        # out the backoff.
        self._host._control_wake = asyncio.Event()
        self._host._reconnect_delay = 0.0

        while True:
            # --- DISCONNECT CONSTANT BACKGROUND CONNECTION TO GEMINI LIVE FOR OFFLINE MODE---
            # Backend choice is an axis of the session, not a bypass of it: a local
            # provider runs a local voice loop instead of the Gemini socket, so
            # Always On / On Call / Connect / Disconnect keep working. Previously
            # this `continue` meant the mic handler that pipes whisper STT into the
            # local model never ran when Local was selected.
            backend = self._host._voice_backend()
            if backend == "local_loop":
                try:
                    if self._host._should_standby():
                        await self._host._wait_for_connect_request()
                        continue
                    self._host._clear_disconnect_request()
                    try:
                        await self._host._session_task_guard(
                            self._run_local_voice_session()
                        )
                    except asyncio.CancelledError:
                        # CancelledError is a BaseException, so the `except
                        # Exception` below cannot see it. Swallow it only when
                        # this was our own On Call / Disconnect teardown; a real
                        # shutdown must still propagate and end run(). The Gemini
                        # path does exactly this; without it, disconnecting in
                        # Local mode killed the whole voice loop.
                        if not self._host._take_disconnect_request():
                            raise
                        print("[BRAHMA EVO] 📴 Local session closed by On Call control.")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"[BRAHMA EVO] ⚠️ Local voice session error: {e}")
                self._host.set_speaking(False)
                self._host.ui.set_state("STANDBY" if self._host._should_standby() else "LISTENING")
                if not self._host._should_standby():
                    print("[BRAHMA EVO] 🔄 Re-attaching local mic in 5s...")
                    await self._pause_until(5)
                continue
            # -------------------------

            try:
                if self._host._should_standby():
                    await self._host._wait_for_connect_request()
                    continue

                self._host._clear_disconnect_request()
                print("[BRAHMA EVO] 🔌 Connecting...")
                self._host._report_voice_status(False, "connecting")
                self._host.ui.set_state("THINKING")
                if client is None:
                    try:
                        client = genai.Client(
                            api_key=_get_api_key(),
                            http_options={"api_version": "v1beta"},
                        )
                    except Exception as e:
                        if not api_key_warned:
                            api_key_warned = True
                            msg = f"Gemini client unavailable: {e}"
                            print(f"[BRAHMA EVO] ⚠️ {msg}")
                            _startup_log(msg)
                        self._host._report_voice_status(False, "no-api-key")
                        self._host.ui.set_state("STANDBY")
                        await self._pause_until(10)
                        continue

                config = self._build_config()

                session = None
                try:
                    connect_cm = client.aio.live.connect(model=LIVE_MODEL, config=config)
                    session = await asyncio.wait_for(connect_cm.__aenter__(), timeout=LIVE_CONNECT_TIMEOUT)
                except Exception as live_exc:
                    # If the Gemini Live handshake fails, keep voice alive on the
                    # local loop instead. The first version of this fallback
                    # `break`-ed out of the session loop: once the local session
                    # stopped, run() returned and the app could never connect or
                    # stand by again without a restart. Falling through to the
                    # normal tail below keeps the contract intact -- Disconnect
                    # parks in standby, an error backs off and retries the socket.
                    print(f"[BRAHMA EVO] ⚠️ Gemini Live connection failed ({live_exc}), falling back to local voice session...")
                    if self._host._should_standby():
                        # Disconnect / On Call arrived while the handshake was in
                        # flight; do not arm a mic the user just dismissed.
                        print("[BRAHMA EVO] 📴 Connect cancelled during handshake.")
                    else:
                        try:
                            await self._host._session_task_guard(self._run_local_voice_session())
                        except asyncio.CancelledError:
                            # CancelledError is a BaseException: swallow only our
                            # own On Call / Disconnect teardown (same contract as
                            # the local_loop branch above); a real shutdown must
                            # still propagate and end run().
                            if not self._host._take_disconnect_request():
                                raise
                            print("[BRAHMA EVO] 📴 Local fallback session closed by On Call control.")
                if session is not None:
                    try:
                        if self._host._should_standby():
                            # The user hit Disconnect / switched to On Call while the
                            # handshake was in flight. _disconnect_live() had no task
                            # to cancel yet, so without this guard the session would
                            # arm anyway and the socket the user just dismissed stays
                            # up. Drop it here instead.
                            print("[BRAHMA EVO] 📴 Connect cancelled during handshake.")
                        else:
                            # The session runs as its own task so _disconnect_live() has
                            # something to cancel. Awaiting it inline would leave no
                            # handle at all and "End Call" would silently do nothing.
                            self._host._session_task = asyncio.create_task(
                                self._serve_live_session(session)
                            )
                            self._host._report_voice_status(True)
                            self._host._reconnect_delay = 0.0  # a good connect resets backoff
                            try:
                                await self._host._session_task
                            except asyncio.CancelledError:
                                # CancelledError is a BaseException, so `except Exception`
                                # below cannot see it. Swallow it only when this was our
                                # own On Call teardown; a real shutdown must still
                                # propagate and end run().
                                if not self._host._take_disconnect_request():
                                    raise
                                print("[BRAHMA EVO] 📴 Session closed by On Call control.")
                            finally:
                                self._host._session_task = None
                    finally:
                        try:
                            await connect_cm.__aexit__(None, None, None)
                        except Exception:
                            pass

            except Exception as e:
                if _is_transient_live_error(e):
                    # A dropped link / DNS blip is expected, not a crash: log one
                    # line instead of dumping a TaskGroup ExceptionGroup.
                    print(f"[BRAHMA EVO] 🔌 Live link dropped ({_live_error_reason(e)}); will retry.")
                else:
                    print(f"[BRAHMA EVO] ⚠️ {e}")
                    traceback.print_exc()
                if _is_gemini_limit_error(e):
                    self._host._throttled_to_openrouter()
                if "1008" in str(e):
                    self._host._resume_handle = None
                self._host.session = None
                self._host._loop = None
            self._host.set_speaking(False)
            if self._host._should_standby():
                # An explicit end of session (Disconnect / On Call parking)
                # also forgets what search_tools taught. A 1008 reconnect does
                # not pass through here, so it keeps its learned-tool pins.
                from core import tool_bridge

                tool_bridge.reset_session_learning()
                # No reconnect delay, and never a LISTENING flash on a UI that
                # is no longer listening -- that flash is what made the status
                # indicator look stuck.
                self._host._report_voice_status(False, "standby")
                self._host.ui.set_state("STANDBY")
                continue
            self._host._report_voice_status(False, "reconnecting")
            self._host.ui.set_state("LISTENING")
            # Exponential backoff with a cap: fast first retry, then settle down
            # so a broken network is not hammered. Reset to 0 on a good connect.
            delay = self._host._next_reconnect_delay()
            print(f"[BRAHMA EVO] 🔄 Reconnecting in {delay:.0f}s...")
            await self._pause_until(delay)

    def set_live_call(self, active: bool):
        """Connect / Disconnect. Authoritative in BOTH modes."""
        active = bool(active)
        with self._host._control_lock:
            self._host._manual_call_active = active
            self._host._live_suspended = not active
        if active:
            self._host._stop_wakeword_listener()
            self._host._request_connect()
        else:
            self._host.ui.set_state("STANDBY")
            self._host._disconnect_live()
        self._host._wake_control()

    def should_standby(self) -> bool:
        """True when the session loop must idle instead of holding a session."""
        with self._host._control_lock:
            if self._host._live_suspended:
                return True
            return self._host._live_mode == "on_call" and not self._host._manual_call_active

    def request_connect(self):
        """Wake the standby wait. Safe to call before run() has started."""
        loop, event = self._host._ui_loop, self._host._connect_event
        if loop is None or event is None:
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass

    def disconnect_live(self):
        """Drop the live session. No-op when nothing is connected."""
        loop, task = self._host._ui_loop, self._host._session_task
        with self._host._control_lock:
            self._host._disconnect_requested = True
        if loop is None or task is None or task.done():
            return
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass

    def wake_control(self):
        """Abort an in-progress reconnect pause so a mode change is immediate."""
        loop = getattr(self._host, "_ui_loop", None)
        event = getattr(self._host, "_control_wake", None)
        with self._host._control_lock:
            self._host._control_seq = getattr(self._host, "_control_seq", 0) + 1
        if loop is None or event is None:
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass

    def next_reconnect_delay(self) -> float:
        """Exponential backoff for the reconnect pause: 2, 4, 8 ... capped at 30."""
        self._host._reconnect_delay = (
            2.0
            if self._host._reconnect_delay <= 0.0
            else min(self._host._reconnect_delay * 2.0, 30.0)
        )
        return self._host._reconnect_delay

    def report_voice_status(self, live: bool, detail: str = ""):
        """Tell the UI which voice backend is driving the session and its state."""
        backend = self._host._voice_backend()
        if backend == "local_loop":
            label = "Local voice"
        elif backend == "gemini_live":
            label = "Gemini Live"
        else:
            label = backend.title()
        try:
            self._host.ui.set_voice_status(label, bool(live), detail)
        except Exception:
            pass

    def session_task_guard(self, coro):
        """Register the running local session so _disconnect_live() can cancel it."""
        async def _runner():
            try:
                await coro
            finally:
                with self._host._control_lock:
                    self._host._session_task = None

        self._host._session_task = asyncio.ensure_future(_runner())
        return self._host._session_task

    def voice_backend(self) -> str:
        """Which engine drives the voice session: 'gemini_live' or 'local_loop'."""
        try:
            cfg = config_manager.load_settings()
        except Exception:
            cfg = {}
        if cfg.get("offline_mode_enabled", False):
            return "local_loop"
        try:
            from core import provider_router
            if not provider_router.is_enabled("gemini"):
                return "local_loop"
        except Exception:
            pass
        provider = str(cfg.get("default_ai_provider", "") or "").strip().lower()
        if provider in ("local", "custom", "openrouter"):
            return "local_loop"
        return "gemini_live"

    def local_voice_turn(self, pcm_bytes: bytes, speaking: dict):
        """One local voice turn: transcribe, answer, speak. Runs off-loop."""
        try:
            from core import speech_stt

            text = speech_stt.transcribe_pcm(pcm_bytes, globals().get("SEND_SAMPLE_RATE", 16000))
            if not text or len(text.strip()) <= 1:
                return
            print(f"[BRAHMA EVO] 🎙️ Heard: {text}")
            wake_fn = globals().get("_wakeword_detected")
            if self._host.ui.muted and callable(wake_fn) and wake_fn(text):
                try:
                    self._host.ui.set_muted_state(False, wakeword=True)
                except Exception:
                    pass
            speaking["on"] = True
            try:
                self._host._on_text_command(text, source="mic")
            finally:
                speaking["on"] = False
        except Exception as exc:
            print(f"[BRAHMA EVO] ⚠️ Local voice turn failed: {exc}")

    async def wait_for_connect_request(self):
        """Idle in On Call standby until the button or the wake word asks."""
        self._host._connect_event = asyncio.Event()
        self._host.set_speaking(False)
        self._host.ui.set_state("STANDBY")
        self._host._start_wakeword_listener()
        try:
            if not self._host._should_standby():
                return
            self._host.ui.write_log("SYS: On Call standby - say 'Brahma Evo' or press Connect.")
            await self._host._connect_event.wait()
        finally:
            self._host._connect_event = None
            self._host._stop_wakeword_listener()

    def start_wakeword_listener(self):
        if self._host._wake_thread is not None and self._host._wake_thread.is_alive():
            return
        self._host._wake_stop = threading.Event()
        self._host._wake_thread = threading.Thread(
            target=self._host._wakeword_loop, daemon=True, name="oncall-wakeword"
        )
        self._host._wake_thread.start()

    def stop_wakeword_listener(self):
        stop = self._host._wake_stop
        if stop is not None:
            stop.set()

    def set_push_to_talk(self, enabled: bool) -> str:
        from core.hotkey import PushToTalk

        self._host._ptt_enabled = bool(enabled)
        self._host._ptt_held = False
        if not enabled:
            if self._host._ptt is not None:
                self._host._ptt.stop()
                self._host._ptt = None
            return "off"
        if self._host._ptt is None:
            self._host._ptt = PushToTalk(self._host._on_ptt)
        scope = self._host._ptt.start()
        try:
            self._host.ui.write_log(
                f"SYS: Push-to-talk on — hold {self._host._ptt.label}"
                + ("." if scope == "global" else " (works while window focused).")
            )
        except Exception:
            pass
        return scope

    def set_speaking(self, value: bool):
        with self._host._speaking_lock:
            self._host._is_speaking = value
            if not value:
                self._host._server_turn_active = False
                self._host._discard_server_audio = True
            else:
                self._host._discard_server_audio = False
        if value:
            self._host.ui.set_state("SPEAKING")
        elif not self._host.ui.muted:
            self._host.ui.set_state("LISTENING")

    def trigger_barge_in(self):
        """Immediately interrupts AI speech playback and switches state to LISTENING."""
        with self._host._speaking_lock:
            if not self._host._is_speaking and not getattr(self._host, "_server_turn_active", False):
                return
            self._host._is_speaking = False
            self._host._server_turn_active = False
            self._host._discard_server_audio = True

        try:
            from actions.attention_monitor import stop_native_speech

            stop_native_speech()
        except Exception:
            pass

        try:
            if self._host.audio_in_queue:
                while not self._host.audio_in_queue.empty():
                    try:
                        self._host.audio_in_queue.get_nowait()
                    except Exception:
                        break
        except Exception:
            pass

        try:
            from sound_manager import SoundManager

            SoundManager.instance().play_listening_start()
        except Exception:
            pass

        if hasattr(self._host, "ui") and self._host.ui and not self._host.ui.muted:
            self._host.ui.set_state("LISTENING")
