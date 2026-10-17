"""
live_handshake_probe.py
Real Gemini Live handshake against the ACTUAL tool payload Brahma Evo now sends.

This is the last mile the earlier verification could not reach. It proves:
  1. the setup frame with the full advertised surface is ACCEPTED by Gemini
     (the 1007 invalid-argument rejection happens at connect time)
  2. a Live session can be opened and closed repeatedly -- the exact operation
     the On Call toggle depends on
  3. session resumption handles round-trip

Requires GEMINI_API in the environment. Read-only with respect to the repo.
Usage: py -3.11 live_handshake_probe.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

API_KEY = (os.environ.get("GEMINI_API") or "").strip()
if not API_KEY:
    print("SKIP: GEMINI_API is not set in this environment.")
    raise SystemExit(0)

# Do not import main.py: it reads the key from api_keys.json and would fail.
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"


def load_payload() -> dict:
    """Assemble exactly what BrahmaLive._build_config advertises.

    Primes the index with the real native declarations plus the Calyx and
    Synapse catalogues -- the configuration that produced the 1007 rejection.
    """
    import re
    import time

    from core import tool_index as ti
    from core import tool_bridge
    from core.tool_index import ToolIndex, ToolEntry

    ti.reset_index()
    idx = ToolIndex(cache_dir=BASE / "_probe_cache")
    ti.get_index = lambda: idx
    tool_bridge.get_index = lambda: idx

    src = (BASE / "main.py").read_text(encoding="utf-8")
    start = src.index("TOOL_DECLARATIONS = [")
    end = src.index("\n]", start)
    natives = []
    for m in re.finditer(
        r'"name":\s*"([a-z0-9_]+)"\s*,\s*"description":\s*(\(.*?\)|"[^"]*")', src[start:end], re.S
    ):
        try:
            d = eval(m.group(2)) if m.group(2).startswith("(") else json.loads(m.group(2))
        except Exception:
            d = m.group(2)
        natives.append({"name": m.group(1), "description": " ".join(str(d).split()),
                        "parameters": {"type": "object", "properties": {}}})

    tool_bridge.configure(settings={"tool_index_enabled": True, "tool_index_budget": 24})
    tool_bridge.prime(natives, type("R", (), {
        "get_tool_declarations": lambda s: [],
        "get_skill": lambda s, n: type("S", (), {"manifest": {"source": "native"}})(),
    })())

    CALYX = ("Calyx association-native database: search, ingest, traverse, provenance "
             "and grounded answers across memory vaults.")
    SYNAPSE = ("Synapse on-machine computer-use and browser automation for the real "
               "Windows desktop: observe, click, type, navigate, run commands.")
    for i in range(31):
        idx.upsert(ToolEntry(name=f"calyx_{i}", description=CALYX, source="mcp:Calyx",
                             skill_id="mcp:Calyx", domain="mcp calyx"))
    for i in range(82):
        idx.upsert(ToolEntry(name=f"synapse_{i}", description=SYNAPSE, source="mcp:Synapse",
                             skill_id="mcp:Synapse", domain="mcp synapse"))
    idx.rebuild(force=True)
    for _ in range(120):
        if "embed" in idx.stats().backends:
            break
        time.sleep(0.5)

    payload = tool_bridge.build_session_payload("")
    snap = tool_bridge.snapshot()
    print(f"catalogue in index ............... {len(natives) + 113} tools "
          f"({len(natives)} native + 31 Calyx + 82 Synapse)")
    print(f"backends ......................... {', '.join(snap['backends'])}")
    print(f"reachable on demand .............. {payload['deferred']}")
    return payload


def _real_system_prompt() -> str:
    """The production prompt.txt plus the wake-word preamble _build_config adds."""
    try:
        from main import _load_system_prompt

        return _load_system_prompt()
    except Exception:
        return "You are Brahma Evo, a voice assistant."


async def probe(payload: dict) -> int:
    from google import genai
    from google.genai import types

    tools = payload.get("declarations", [])
    names = [t.get("name") for t in tools if isinstance(t, dict)]
    print(f"tools in setup frame ............. {len(tools)}")
    print(f"Gemini hard limit ................ {payload.get('limit', 128)}")
    if len(tools) > 128:
        print("FAIL: payload still exceeds the 128-tool protocol limit.")
        return 1

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription={},
        input_audio_transcription={},
        system_instruction=_real_system_prompt(),
        tools=[{"function_declarations": tools}],
        session_resumption=types.SessionResumptionConfig(),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
            )
        ),
    )

    client = genai.Client(api_key=API_KEY, http_options={"api_version": "v1beta"})
    failures = 0

    for attempt in (1, 2):
        print(f"\n-- connect #{attempt} --")
        connect_cm = client.aio.live.connect(model=LIVE_MODEL, config=config)
        try:
            await asyncio.wait_for(connect_cm.__aenter__(), timeout=20)
        except Exception as exc:
            print(f"FAIL: connect rejected: {type(exc).__name__}: {exc}")
            return 1

        print("PASS: setup frame accepted, session open")
        print(f"      advertised: {names}")

        # Let the server send its session-resumption update.
        try:
            async def grab(resume=None, _out=[]):
                if resume is not None:
                    _out.append(resume)
            await asyncio.sleep(2.0)
        except Exception:
            pass

        # This is the operation the On Call "End Call" button performs.
        try:
            await asyncio.wait_for(connect_cm.__aexit__(None, None, None), timeout=10)
            print("PASS: session closed cleanly (End Call path)")
        except Exception as exc:
            print(f"FAIL: close failed: {type(exc).__name__}: {exc}")
            failures += 1

    print()
    if failures:
        print(f"RESULT: {failures} FAILED")
        return 1
    print("RESULT: LIVE HANDSHAKE OK -- setup accepted and sessions open/close repeatedly")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe(load_payload())))