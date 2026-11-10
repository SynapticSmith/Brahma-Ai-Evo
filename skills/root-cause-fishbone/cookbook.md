# Cookbook: 6M Fishbone Root-Cause Analysis

## Objective
Isolate complex, non-obvious defects by analyzing contributing factors across the 6 Ishikawa categories.

## Prerequisites
- System defect with intermittent, multi-variable, or platform-specific symptoms.

## Execution Recipe
1. **Define the Defect Spine**:
   Write the exact symptom at the head of the fishbone (e.g. "WebSocket drops with 1007 error during voice turns").
2. **Brainstorm across the 6 Categories**:
   - **Machine**: Windows Store Python 3.12, directsound audio drivers.
   - **Method**: Concurrent microphone audio chunk streaming while tool execution is active.
   - **Material**: Resumption handle string corrupted or rejected by server.
   - **Manpower**: User speaks while model executes a 5-second tool call.
   - **Measurement**: Log trace caught `1007 invalid frame payload data`.
   - **Milieu**: Gemini Live server enforcing strict full-duplex protocol framing.
3. **Isolate Primary vs Secondary Drivers**:
   Circle the intersecting factors causing the failure (e.g., Method + Machine + Milieu).
4. **Draft Multi-Layered Remediation**:
   Implement a fix that addresses both the trigger (pause audio streaming during tool execution) and the recovery (clear resumption handle on 1007).
5. **Verification Gate**:
   Execute simulated defect scenario; verify both layers hold.
6. **Remediation**:
   If defect recurs, inspect the remaining unaddressed 6M branches.
