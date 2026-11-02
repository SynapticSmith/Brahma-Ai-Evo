"""Tool-call execution, lifted out of main.py's BrahmaLive (P4-19 seam).

All native desktop capabilities are now registered as ``ToolSpec``s in
:mod:`core.desktop_tools` and dispatched through :mod:`agent.runtime` (ADR-019
waves 1-3). This module routes registered specs, discovery meta-tools, and
dynamic skills from :class:`core.dynamic_registry.DynamicToolRegistry`.
"""
from __future__ import annotations

import asyncio
import traceback

from google.genai import types

from core import desktop_host

# Bound on how long execute_sync waits for a tool running on the live loop.
TOOL_EXEC_TIMEOUT_SECONDS = 300.0


class _LocalFC:
    def __init__(self, n, a, cid):
        self.name = n
        self.args = a
        self.id = cid
        self.silent_completion = True


class ToolExecutor:
    """Executes one tool call. `host` is the owning BrahmaLive instance."""

    def __init__(self, host, *, ig_gemini_reply, tool_declarations):
        self._host = host
        self._ig_gemini_reply = ig_gemini_reply
        self._tool_declarations = tool_declarations
        self._bridge_depth = 0
        self._recent_calls = []
        from core import desktop_tools

        self._desktop = desktop_tools.attach_host(host)

    def execute_via_bridge(self, name: str, args: dict) -> str:
        """Run a tool on behalf of the tool bridge's call_tool meta-tool."""
        self._bridge_depth += 1
        try:
            key = (name, str(args)[:200])
            self._recent_calls.append(key)
            if len(self._recent_calls) > 100:
                self._recent_calls = self._recent_calls[-30:]
            if self._recent_calls.count(key) > 5:
                return f"Tool '{name}' skipped to prevent loop (repeated calls detected)."
            return self.execute_sync(name, args, call_id="call_bridge_" + name)
        finally:
            self._bridge_depth -= 1

    def execute_sync(self, name: str, args: dict, call_id: str = "call_local") -> str:
        fc = _LocalFC(name, args, call_id)
        loop = self._host._loop
        if loop and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self.execute(fc), loop)
                fr = future.result(timeout=TOOL_EXEC_TIMEOUT_SECONDS)
                res_obj = getattr(fr, "response", {})
                return res_obj.get("result", str(res_obj))
            except Exception as e:
                return "Tool execution failed: " + str(e)
        try:
            fr = asyncio.run(self.execute(fc))
            res_obj = getattr(fr, "response", {})
            return res_obj.get("result", str(res_obj))
        except Exception as e:
            return "Tool execution failed: " + str(e)

    async def execute(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        dispatch_name = name
        if str(name or "").startswith("workspace_"):
            parts = name.split("_", 2)
            if len(parts) > 1:
                args.setdefault("service", parts[1])
            if len(parts) > 2:
                args.setdefault("action", parts[2])
            dispatch_name = "google_workspace"

        try:
            from agent import code_tools  # noqa: F401  (import registers specs)
            from agent import runtime as agent_runtime
            from agent.spec import get as get_spec

            spec = get_spec(dispatch_name)
            if spec is not None:
                call_id = getattr(fc, "id", None) or f"call_{name}"
                text = await asyncio.to_thread(
                    agent_runtime.execute,
                    dispatch_name,
                    args,
                    call_id=call_id,
                    extra={"host": self._desktop.host},
                )
                print(f"[BRAHMA EVO] 🧰 {name} -> {str(text)[:200].replace(chr(10), ' ')}")
                payload = {"result": text}
                if spec.silent:
                    payload["silent"] = True
                return types.FunctionResponse(name=name, id=call_id, response=payload)
        except Exception as exc:
            print(f"[BRAHMA EVO] ⚠️ Harness tool '{name}' unavailable: {exc}")

        try:
            from core import tool_bridge

            if tool_bridge.is_meta_tool(name):
                call_id = getattr(fc, "id", None) or f"call_{name}"
                text = await tool_bridge.execute_meta(name, args)
                print(f"[BRAHMA EVO] 🧭 {name} -> {text[:200].replace(chr(10), ' ')}")
                return types.FunctionResponse(name=name, id=call_id, response={"result": text})
        except Exception as exc:
            print(f"[BRAHMA EVO] ⚠️ Meta-tool '{name}' unavailable: {exc}")

        # Dynamic skills fallback (present_start replaces the legacy self._host.speak(...) banner).
        desktop_host.present_start(self._desktop.host, name, args)
        result = f"Unknown tool: {name}"
        try:
            from core.dynamic_registry import DynamicToolRegistry

            if name and DynamicToolRegistry.has_tool(name):
                loop = asyncio.get_event_loop()
                run_res = await loop.run_in_executor(
                    None, lambda: DynamicToolRegistry.execute_sync(name, args or {})
                )
                out_text = (
                    str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res)
                    if isinstance(run_res, dict)
                    else str(run_res)
                ).strip()
                self._host.ui.write_log(f"Brahma Evo [{name}]:\n{out_text}")
                result = out_text
        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self._host.speak_error(name, e)

        desktop_host.present_end(self._desktop.host, name, args, result)
        return types.FunctionResponse(id=fc.id, name=name, response={"result": result})


__all__ = ["ToolExecutor"]
