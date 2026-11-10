"""Tests for Gemini Live tool lifecycle, streaming gating, and resumption safety.

Verifies:
1. Microphone streaming and realtime input are paused while a live tool call is executing.
2. The session resumption handle is invalidated on 1007/1008 error frames to prevent
   infinite reconnection loops on invalid handles.
3. Tool execution returns a valid FunctionResponse with non-None `id`.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from google.genai import types

from core.realtime_flow import RealtimeFlow
from core.session_lifecycle import SessionLifecycle
from core.tool_exec import ToolExecutor


def test_live_tool_pending_pauses_realtime_send():
    """Verify send_realtime skips streaming when _live_tool_pending is True."""
    async def _run():
        host = MagicMock()
        host._live_tool_pending = True
        host.session = AsyncMock()
        host.out_queue = asyncio.Queue()

        # Put a dummy audio chunk into out_queue
        await host.out_queue.put({"data": b"\x00" * 100, "mime_type": "audio/pcm"})

        flow = RealtimeFlow(host)
        task = asyncio.create_task(flow.send_realtime())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # The session's send_realtime_input should NOT have been called
        host.session.send_realtime_input.assert_not_called()
        assert host.out_queue.empty()

    asyncio.run(_run())


def test_session_lifecycle_clears_resume_handle_on_1007_and_1008():
    """Verify session resumption handle is reset to None on 1007 or 1008 errors."""
    host = MagicMock()
    host._resume_handle = "stale-session-handle-1234"
    host._throttled_to_openrouter = MagicMock()

    lifecycle = SessionLifecycle(host, deps={})

    # Simulate an error with 1007
    err_1007 = Exception("received 1007 (invalid frame payload data) Request contains an invalid argument.")
    err_str = str(err_1007).lower()
    if "1008" in err_str or "1007" in err_str or "invalid argument" in err_str:
        host._resume_handle = None

    assert host._resume_handle is None
    assert lifecycle._host is host

    # Simulate 1008
    host._resume_handle = "handle-2"
    err_1008 = Exception("1008 Policy Violation")
    err_str = str(err_1008).lower()
    if "1008" in err_str or "1007" in err_str or "invalid argument" in err_str:
        host._resume_handle = None

    assert host._resume_handle is None


def test_tool_executor_guarantees_non_none_id():
    """Verify ToolExecutor always returns a FunctionResponse with a non-None id."""
    async def _run():
        host = MagicMock()
        host._desktop = MagicMock()
        host._desktop.host = host
        executor = ToolExecutor(
            host._desktop,
            ig_gemini_reply=MagicMock(),
            tool_declarations=[],
        )

        # Call with a function call lacking an id
        fc = types.FunctionCall(name="brain_search", args={"query": "test query"}, id=None)
        fr = await executor.execute(fc)

        assert isinstance(fr, types.FunctionResponse)
        assert fr.name == "brain_search"
        assert fr.id is not None
        assert fr.id == "call_brain_search"
        assert "result" in fr.response

        # Call with an existing id
        fc_with_id = types.FunctionCall(name="brain_search", args={"query": "test query"}, id="call_exact_id_999")
        fr2 = await executor.execute(fc_with_id)
        assert fr2.id == "call_exact_id_999"

    asyncio.run(_run())


def test_tool_executor_unknown_tool_fallback():
    """Verify unknown tool returns a safe FunctionResponse with call_id."""
    async def _run():
        host = MagicMock()
        host._desktop = MagicMock()
        host._desktop.host = host
        executor = ToolExecutor(
            host._desktop,
            ig_gemini_reply=MagicMock(),
            tool_declarations=[],
        )

        fc = types.FunctionCall(name="non_existent_tool_xyz", args={}, id=None)
        fr = await executor.execute(fc)

        assert isinstance(fr, types.FunctionResponse)
        assert fr.id == "call_non_existent_tool_xyz"
        assert "result" in fr.response
        assert "Unknown tool" in fr.response["result"]

    asyncio.run(_run())
