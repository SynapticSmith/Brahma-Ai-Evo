# Deferred Tool Proxy Mission

**Status:** Scanning / Investigation  
**Started:** 2026-10-29  
**Goal:** Make Gemini Live able to use deferred/discovered tools (via search_tools -> call_tool) without crashing when the model calls tools directly. Also build a backend-agnostic architecture where tool discovery/execution can be offloaded to the system regardless of gateway (Gemini Live, local LLM, custom endpoint).

## Problem Statement
- Gemini Live has hard limit ~128 tools and cannot swap tools mid-session. Tools are fixed at connect time.
- Brahma uses tool index with discovery meta-tools (search_tools, list_tools, call_tool, get_skill_manual, search_skills, list_skills, load_skill, get_tool_surface). 
- When model does `search_tools` and finds a deferred tool (e.g. `mcp_Synapse_cdp_open_tab`), it sometimes calls it directly as a function call. Since it's not in the advertised declarations, Gemini Live closes the websocket with `1008 (policy violation) Requested entity was not found`.
- We need to make this work without loading all tools up front. The architecture should handle routing/execution so the model doesn't have to strictly format calls - offload to the system where possible.

## Current Architecture (Facts from Scan)

### Tool Index & Bridge (`core/tool_bridge.py`)
- `META_DECLARATIONS` contains: `search_tools`, `list_tools`, `call_tool`, `get_skill_manual`, `get_tool_surface`, `search_skills`, `list_skills`, `load_skill` (8 meta-tools total). Defined in `core/tool_index.py`.
- `build_session_payload(intent)` builds bounded advertised set: pinned core + meta-tools + top-k retrieved tools. Clamped by `budget()` (max GEMINI_MAX_TOOLS=128, default ~24). Everything else deferred. Appends `skill_context` from index resolution.
- `execute_meta(name, args)` routes meta-tools. `call_tool` goes to `_handle_call()` which looks up tool in index via `get_index().get(tool_name)`, checks `_executor` is installed, runs via `await asyncio.to_thread(_executor, tool_name, dict(call_args))`, stringifies result.
- `is_meta_tool(name)` returns True if name in META_DECLARATIONS.
- `configure(executor=..., settings=...)` installs executor. In main.py: `tool_bridge.configure(executor=self._execute_tool_via_bridge)` (line ~2276). Also called with settings elsewhere.
- `sync_all/native/registry/skills` populate index. `prime()` does initial sync. Skills Library synced first.
- Skills Library is peer system - executable skills published to registry/indexed; instructional loaded via `load_skill`.

### Session Lifecycle (`core/session_lifecycle.py`)
- `_build_config()` builds LiveConnectConfig with tools from `tool_bridge.build_session_payload(last_intent)`. Sets system_instruction with memory/time/location/prompt/skill_context/live_extra. 
- Passes `tools=[{"function_declarations": tool_declarations}]` to LiveConnectConfig.
- When connecting: `client.aio.live.connect(model=LIVE_MODEL, config=config)`. On failure may fall back to local voice session.
- Tracks `_last_user_text` to seed intent for next payload build.
- On 1008 error, clears resume handle.

### Audio I/O / Tool Call Handling (`core/audio_io.py`)
- In `_receive_audio()`, when `response.tool_call` comes in: iterates `function_calls`, logs `fc.name` as `[BRAHMA EVO] 📞 {fc.name}`, calls `fr = await self._host._execute_tool(fc)`, collects responses, calls `await self._host.session.send_tool_response(function_responses=fn_responses)`.
- This is where Live tool calls are received and responses sent back.

### Tool Execution (`core/tool_exec.py`, `main.py`)
- `main.py:_execute_tool(self, fc)` is thin delegate to `self._tool_executor().execute(fc)`. Host has `_tool_executor()` property building `ToolExecutor(self, ig_gemini_reply=..., tool_declarations=TOOL_DECLARATIONS)`.
- `ToolExecutor.execute(fc)`: first checks if meta-tool via `tool_bridge.is_meta_tool(name)`. If yes, calls `await tool_bridge.execute_meta(name, args)`, logs it, returns `FunctionResponse` with result. This is correct - meta-tools executed locally without going through the big if-elif chain.
- For non-meta tools, it does HUD/speech updates, then goes through a huge if-elif chain for built-in tools, checks if it's a dynamic tool from registry if not in declarations, etc. Returns FunctionResponse.
- `ToolExecutor.execute_via_bridge(name, args)` calls `execute_sync(name, args, call_id="call_bridge_"+name)`. `execute_sync` creates a fake `_LocalFC`, if host loop running does `asyncio.run_coroutine_threadsafe(self.execute(fc), loop).result(timeout=TOOL_EXEC_TIMEOUT_SECONDS)`, otherwise `asyncio.run(self.execute(fc))`. Returns extracted result string.
- `main.py._execute_tool_via_bridge` is the executor passed to tool_bridge: `tool_bridge.configure(executor=self._execute_tool_via_bridge)`. Correct wiring.

### Prompt
- `core/prompt.txt` rewritten as agent brief (section 16 of log). Teaches discovery loop: `search_tools` finds tool by goal and returns exact name/schema; run it with `call_tool` using that name and arguments exactly as returned. Says "NEVER guess tool names" and describes indirection. Also has TOOL SURFACE section reiterating this.

## The Bug
From logs: model does `search_tools` (finds `mcp_Synapse_cdp_open_tab`), then immediately after tries to call it directly. Gemini Live sees function call for undeclared function name -> closes socket with `1008 Requested entity was not found`. Brahma reconnects and repeats.

Root cause: Model calls deferred tool directly instead of going through `call_tool`. Since it's not in advertised declarations for this Live session, Gemini rejects it at protocol level.

## Analysis
The rejection happens at the Gemini Live protocol level when the model emits a function call for an undeclared function. Looking at the error trace from logs, `google.genai.live._receive()` raises APIError 1008 when websocket closes. This happens before our code can properly handle it as a tool call - the session dies.

So we cannot catch this in our tool handler. We need to prevent the model from making direct calls to undeclared tools.

### Current State Assessment
- Architecture is well-designed for this - meta-tools are advertised, call_tool exists to execute any tool by name
- ToolBridge correctly routes call_tool through the executor
- Prompt already teaches the pattern
- But the model still sometimes ignores it and calls directly

### Possible Approaches

#### 1. Stronger Prompting (Prevention) - HIGHEST PRIORITY
- **System prompt (`core/prompt.txt`)**: Add very explicit, forceful warnings. For example "CRITICAL: Only call tools that are explicitly declared to you. If a tool is found via search_tools but not in your available functions, you MUST call it via call_tool(tool_name='...', arguments={...}). NEVER call undeclared tools directly - doing so will crash the session."
- **Session lifecycle instructions**: In `session_lifecycle._build_config()`, add explicit instructions about tool usage
- **Tool surface section**: Make it even more emphatic

#### 2. Search Results Enhancement
- In `tool_bridge._handle_search()`, make the call_tool instruction much more prominent and emphatic
- Add "⚠️ CRITICAL: You CANNOT call this tool directly. You MUST use call_tool with tool_name=\"{name}\"" 
- Maybe repeat the instruction multiple times

#### 3. System Instruction Enhancement in Session Config
- In `session_lifecycle._build_config()` around line ~475-490, add explicit tool usage rules
- Add to the parts list with strong emphasis

#### 4. Architectural Proxy (if possible) - HARDER
- Try to catch at different levels - but if Gemini rejects at protocol level, hard to catch
- Could try to validate before sending tool responses? Probably not relevant
- Another idea: in the Live config, could we do something different?
- But the user wants to "offload onto the architecture instead of needing the ai to strictly format its response calls"

#### 5. Make call_tool More Prominent
- Ensure call_tool is always advertised (it is - it's a meta-tool)
- Maybe add it to pinned tools? It's already in meta-tools which are always added

### Recommendation
Since the rejection happens at protocol level, **prevention via stronger prompting is the most feasible approach right now**. We need to make it absolutely impossible for the model to misunderstand that undeclared tools must go through call_tool.

Also we should add defensive handling where possible - even if Gemini rejects, maybe we can handle better? But looking at how errors flow, probably not much we can do.

But let us also look up if there might be other approaches - like could we intercept and somehow make the call work? Unclear. But better to focus on prevention first.

Also the user said "this would make the job easier. but please make sure to verify your claim" - so we need to be thorough.

Another thought - in the Live API, when the model tries to call an undeclared function, does it get rejected immediately? Yes, that's why the connection drops. So we cannot intercept it.

So prevention is the only real solution. Let us also consider if we can make the model more likely to follow instructions by making them more prominent.

Also let us look up the full picture again - in the prompt, line ~38 says "run it with call_tool using that name and arguments exactly as returned. list_tools browses the whole catalogue in pages. Prefer a directly-loaded tool when one exists; do not guess tool names." - but it doesn't say "NEVER call undeclared tools directly". We need to add that.

Similarly in line ~73-76.

Also in session_lifecycle, when building config, maybe add explicit instructions.

Let us also look up if there might be a way to make all tools discoverable through call_tool without advertising them - which is already how it works. The issue is the model not using call_tool.

Another architectural approach - could we make the meta-tools include a catch-all that can execute any tool? Like have call_tool be the only way, and make it very easy. Also maybe add instructions to always use call_tool for anything found via search.

But the key is to make the instructions unmissable.