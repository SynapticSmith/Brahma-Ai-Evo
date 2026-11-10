---
name: five-whys-root-cause
description: Iterative interrogative technique to drill past superficial symptoms down to systemic root causes of defects and failures.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, 5-whys, root-cause, debugging, causal-analysis]
triggers:
  - "five whys"
  - "5 whys"
  - "root cause analysis"
  - "find the root cause"
  - "why did this fail"
---

# 5 Whys Root-Cause Analysis Skill

## Overview
Based on Sakichi Toyoda's method originating in the Toyota Production System. When a software bug, test failure, or crash occurs, superficial fixes patch only the symptom (e.g. adding `if x is not None:`). By asking "Why?" at least five times, engineers navigate the causal chain to identify and fix the underlying procedural, architectural, or invariant breakdown.

## Procedure
1. **Formulate the Observable Incident**:
   - State the exact failure: "Test `test_session_resume` raised `ConnectionClosedError: 1007`."
2. **Execute Causal Chaining (The 5 Whys)**:
   - *Why 1*: Why did the connection close? Because the server received invalid frame payload data.
   - *Why 2*: Why was invalid frame payload data sent? Because audio frames were piped over WebSocket while a tool call was pending execution.
   - *Why 3*: Why were audio frames sent while the tool call was pending? Because the microphone callback had no awareness of tool execution state.
   - *Why 4*: Why was there no awareness? Because the audio streaming subsystem and tool dispatch lifecycle were completely decoupled.
   - *Why 5*: Why were they decoupled? Because the harness lacked an explicit synchronization latch (`_live_tool_pending`) to gate audio ingestion during function execution.
3. **Target Systemic Remedies**:
   - Implement the fix at Level 4 and Level 5, not merely Level 1.
   - Install automated assertions or type checks to prevent regression.
