# Cookbook: Constrained JSON Generation via Ollama/Llama.cpp Grammars

## Objective
Enforce strict JSON Schema compliance at model generation time via grammar constraints, eliminating syntax errors.

## Prerequisites
- Ollama or llama.cpp endpoint with grammar/format support.

## Execution Recipe
1. **Define Pydantic Schema Model**:
   ```python
   from pydantic import BaseModel, Field

   class AgentTaskPlan(BaseModel):
       task_name: str = Field(..., description="Short title")
       steps: list[str] = Field(..., min_items=1)
       estimated_seconds: int = Field(..., ge=1)
   ```
2. **Export Canonical JSON Schema**:
   ```python
   schema = AgentTaskPlan.model_json_schema()
   ```
3. **Dispatch to Ollama with `format` Parameter**:
   ```python
   import json, urllib.request

   payload = {
       "model": "lfm3:1.7b",
       "prompt": "Create a task plan for compiling the project.",
       "format": schema, # Enforces grammar-level token masking
       "stream": False
   }
   req = urllib.request.Request("http://localhost:11434/api/generate", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
   resp = urllib.request.urlopen(req)
   result_json = json.loads(json.loads(resp.read())["response"])
   ```
4. **Verification Gate**:
   *Gate: `result_json` deserializes into `AgentTaskPlan` without throwing ValidationError.*
5. **Remediation**:
   If older model backend lacks dynamic JSON schema support, supply GBNF grammar file explicitly.
