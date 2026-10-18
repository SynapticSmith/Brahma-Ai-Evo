"""Audio I/O and speech, lifted out of main.py's BrahmaLive (P4-19 seam).

Behavior-preserving move: BrahmaLive keeps thin delegating wrappers and hands
this executor a host reference plus the small set of module-level helpers and
constants it needs (the LiveReconnect exception class, _wakeword_detected,
_update_memory_async, and the audio constants). The speak/listen helpers
(_process_local_speech, _fallback/_send/_speak_edge_native/_speak_thread) are
local nested functions inside the moved methods and stay local here. No circular
import introduced.
"""
from __future__ import annotations

import asyncio
import threading
import traceback

from core import audio_devices
from memory import config_manager
import sounddevice as sd


class AudioIO:
    """Encapsulates microphone capture, audio reception/playback, and speech."""

    def __init__(self, host, *,
                 channels: int,
                 send_sample_rate: int,
                 recv_sample_rate: int,
                 chunk_size: int,
                 LiveReconnect,
                 wakeword_detected,
                 update_memory_async):
        self._host = host
        self._channels = channels
        self._send_sample_rate = send_sample_rate
        self._recv_sample_rate = recv_sample_rate
        self._chunk_size = chunk_size
        self._LiveReconnect = LiveReconnect
        self._wakeword_detected = wakeword_detected
        self._update_memory_async = update_memory_async


    async def _listen_audio(self):
        print("[BRAHMA EVO] 🎤 Mic started")
        loop = asyncio.get_event_loop()
        import numpy as np

        _mic_name = config_manager.get_input_device()
        _mic_dev = audio_devices.resolve(_mic_name, "input") if _mic_name else None
        available_inputs = audio_devices.list_devices("input")
        if _mic_dev is None and available_inputs:
            _mic_name = available_inputs[0]
            _mic_dev = audio_devices.resolve(_mic_name, "input")
        if _mic_dev is None:
            self._host.ui.write_log(
                "SYS: No usable microphone is available at the configured sample rate. "
                "Voice input is paused; text and mobile remote remain available."
            )
            while True:
                await asyncio.sleep(60)

        speech_buffer = bytearray()
        silence_chunks = 0

        def callback(indata, frames, time_info, status):
            nonlocal silence_chunks
            with self._host._speaking_lock:
                brahma_speaking = self._host._is_speaking
            if self._host._phone_active:
                return

            if getattr(self, "_ptt_enabled", False) and not getattr(self, "_ptt_held", False):
                data = np.zeros_like(indata).tobytes()
                loop.call_soon_threadsafe(
                    self._host.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )
                return
            
            if not self._host.ui.muted or getattr(self._host.ui, "_wakeword_listening", False):
                lvl = float(np.sqrt(np.mean(np.square(indata, dtype=np.float32))))
                
                # Handle Local AI voice input whenever the session backend is
                # the local loop. This is a routing decision for the mic path,
                # not a session bypass -- session_lifecycle decides the backend,
                # this only decides who transcribes. Asking the same helper (so
                # Local, offline, Custom and OpenRouter all answer here) keeps
                # the two from disagreeing, which is how a Custom endpoint kept
                # feeding the Gemini socket.
                try:
                    local_backend = self._host._voice_backend() == "local_loop"
                except Exception:
                    app_cfg = config_manager.load_settings()
                    local_backend = (
                        app_cfg.get("default_ai_provider") == "Local"
                        or bool(app_cfg.get("offline_mode_enabled", False))
                    )
                if local_backend:
                    if not brahma_speaking and not self._host.ui.muted:
                        if lvl > 22.0:
                            speech_buffer.extend(indata.tobytes())
                            silence_chunks = 0
                        elif len(speech_buffer) > 0:
                            silence_chunks += 1
                            # ~0.7s of silence (each chunk is ~30ms -> 20 chunks)
                            if silence_chunks > 18:
                                captured = bytes(speech_buffer)
                                speech_buffer.clear()
                                silence_chunks = 0
                                if len(captured) > (self._send_sample_rate * 2 * 0.5):
                                    def _process_local_speech(pcm_bytes):
                                        try:
                                            # Pluggable STT: cloud Google by
                                            # default, faster-whisper when the
                                            # user selects it. Same PCM in,
                                            # same command string out.
                                            from core import speech_stt

                                            text_cmd = speech_stt.transcribe_pcm(
                                                pcm_bytes, self._send_sample_rate
                                            )
                                            if text_cmd and len(text_cmd.strip()) > 1:
                                                print(f"[Local AI Voice] 🎙️ Heard: {text_cmd}")
                                                self._host._on_text_command(text_cmd, source="mic")
                                        except Exception:
                                            pass
                                    threading.Thread(target=_process_local_speech, args=(captured,), daemon=True).start()

                if brahma_speaking:
                    if self._host._echo.is_user_speech(indata, self._send_sample_rate, lvl) and lvl > 28.0:
                        loop.call_soon_threadsafe(self._host.trigger_barge_in)
                        data = indata.tobytes()
                    else:
                        data = np.zeros_like(indata).tobytes()
                else:
                    if not self._host.ui.muted:
                        try:
                            self._host.ui.set_audio_level(min(1.0, lvl / 1200.0))
                        except Exception:
                            pass
                    if self._host._echo._hist:
                        self._host._echo.reset()
                    if lvl > 10.0:
                        data = indata.tobytes()
                    else:
                        data = np.zeros_like(indata).tobytes()
                    
                loop.call_soon_threadsafe(
                    self._host.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=self._send_sample_rate,
                channels=self._channels,
                dtype="int16",
                blocksize=self._chunk_size,
                device=_mic_dev,
                callback=callback,
            ):
                print(f"[BRAHMA EVO] 🎤 Mic stream open ({_mic_name or 'Default'})")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[BRAHMA EVO] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[BRAHMA EVO] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self._host.session.receive():
                    _go = getattr(response, "go_away", None)
                    if _go is not None:
                        # The server is rotating the session (e.g. before a
                        # planned restart). Reconnect on our terms now instead of
                        # waiting for the socket to be cut mid-turn and surfacing
                        # as a bare 1006.
                        _left = getattr(_go, "time_left", None)
                        raise self._LiveReconnect(
                            f"server GoAway (time_left={_left})"
                            if _left is not None else "server GoAway"
                        )
                    _sru = getattr(response, "session_resumption_update", None)
                    if _sru is not None:
                        if getattr(_sru, "resumable", False) and getattr(_sru, "new_handle", None):
                            self._host._resume_handle = _sru.new_handle

                    if response.data:
                        self._host.set_speaking(True)
                        self._host.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self._host.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                try:
                                    from actions.attention_monitor import stop_native_speech
                                    stop_native_speech()
                                except Exception:
                                    pass
                                in_buf.append(txt)
                                # Seed the next session's tool selection with what
                                # the user is actually asking about.
                                self._host._last_user_text = txt
                                if self._host.ui.muted and self._wakeword_detected(txt):
                                    try:
                                        self._host.ui.set_muted_state(False, wakeword=True)
                                        self._host.ui.write_log("SYS: Wake word detected. Mic active.")
                                    except Exception:
                                        pass

                        if sc.turn_complete:
                            self._host.set_speaking(False)

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self._host.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                # The model emits its mood as a leading
                                # [EMOTION] tag on the injected live layer. This
                                # path used to log the turn verbatim and drop
                                # the tag, so in the default voice mode the
                                # avatar never reacted at all. Strip it, deliver
                                # it, and log the clean turn.
                                full_out, emotion = self._host._split_emotion_tag(full_out)
                                self._host._deliver_avatar_emotion(emotion)
                                if full_out:
                                    self._host.ui.write_log(f"Brahma Evo: {full_out}")
                            out_buf = []

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=self._update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True
                                ).start()

                    if response.tool_call:
                        self._host.ui.set_state("EXECUTING")
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[BRAHMA EVO] 📞 {fc.name}")
                            fr = await self._host._execute_tool(fc)
                            fn_responses.append(fr)
                        self._host.ui.set_state("THINKING")
                        await self._host.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            print(f"[BRAHMA EVO] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[BRAHMA EVO] 🔊 Play started")
        import numpy as np

        _spk_name = config_manager.get_output_device()
        _spk_dev = audio_devices.resolve(_spk_name, "output") if _spk_name else None

        stream = sd.RawOutputStream(
            samplerate=self._recv_sample_rate,
            channels=self._channels,
            dtype="int16",
            blocksize=self._chunk_size,
            device=_spk_dev,
        )
        stream.start()
        try:
            while True:
                chunk = await self._host.audio_in_queue.get()
                try:
                    pcm = np.frombuffer(chunk, dtype=np.int16)
                    lvl = float(np.sqrt(np.mean(np.square(pcm, dtype=np.float32))))
                    self._host._echo.note_output(pcm, self._recv_sample_rate, lvl)
                    self._host.ui.set_audio_level(min(1.0, lvl / 2500.0))
                except Exception:
                    pass
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[BRAHMA EVO] ❌ Play: {e}")
            raise
        finally:
            self._host.set_speaking(False)
            stream.stop()
            stream.close()

    def speak(self, text: str, proactive: bool = False):
        text = (text or "").strip()
        if not text:
            return

        # Check if we should use Gemini Live for speech - only if backend is gemini_live
        backend = "local_loop"
        try:
            if hasattr(self._host, '_voice_backend'):
                backend = self._host._voice_backend()
        except Exception:
            pass

        if self._host.session and self._host._loop and backend == "gemini_live":
            # Route text through Gemini Live API for the unified native Charon voice
            import asyncio
            async def _speak_send():
                try:
                    prompt = f"System Alert / Context: {text}\n\nPlease relay this information to me naturally now."
                    await self._host.session.send_client_content(turns={"parts": [{"text": prompt}]}, turn_complete=True)
                except Exception as e:
                    print(f"[BRAHMA EVO] Unified Speak (Charon) err: {e}")
                    def _speak_fallback():
                        try:
                            self._host.set_speaking(True)
                            from actions.attention_monitor import _speak_edge_native
                            _speak_edge_native(text)
                        except Exception as exc:
                            print(f"[Brahma Speak] Fallback TTS failed: {exc}")
                        finally:
                            self._host.set_speaking(False)
                    threading.Thread(target=_speak_fallback, daemon=True).start()
            asyncio.run_coroutine_threadsafe(_speak_send(), self._host._loop)
        else:
            # No live Gemini session: this is the modular speech path, so the
            # user-selectable engine applies. edge-tts stays the default and the
            # pre-existing offline_mode -> SAPI routing is preserved inside
            # speech_tts. The Gemini branch above is deliberately untouched.
            #
            # use_sink=False is required, not cosmetic: this method IS the
            # installed speech sink (set_speech_sink(self._host.speak) at init), and
            # the modular engine honours an installed sink when one is present.
            # Passing the default (True) made speak() re-enter through the sink
            # (itself) on a fresh thread, forever: an unbounded thread-spawning
            # loop. With no live session that is the only path, so every
            # offline/local reply froze the UI (not responding) and pinned
            # CPU/RAM while the speech engines flooded the audio device.
            # Skipping the sink here breaks the re-entry; the engine selection
            # and the offline_mode -> SAPI routing below are unchanged.
            def _speak_thread():
                try:
                    self._host.set_speaking(True)
                    from core import speech_tts

                    speech_tts.speak(text, use_sink=False)
                except Exception as exc:
                    print(f"[Brahma Speak] Unified TTS failed: {exc}")
                finally:
                    self._host.set_speaking(False)
            threading.Thread(target=_speak_thread, daemon=True).start()

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self._host.ui.write_log(f"ERR: {tool_name} — {short}")
        self._host.speak(f"Sir, {tool_name} encountered an error. {short}")

__all__ = ["AudioIO"]
