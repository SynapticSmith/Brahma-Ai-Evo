---
name: structured-grammar-generator
description: Constrained decoding protocol enforcing mathematically guaranteed schema compliance, JSON Schema masking, regex token filtering, and Pydantic validation.
metadata:
  domain: coding
  tags: [grammar, outlines, json-schema, constrained-decoding, pydantic, regex, parsing, coding]
triggers:
  - "structured grammar generator"
  - "constrained decoding"
  - "json schema constrained output"
  - "outlines structured generation"
  - "guaranteed json generation"
  - "regex constrained decoding"
  - "finite state machine decoding"
---

# Structured Grammar Generator Skill

## Overview
Drawn from `outlines` and formal constrained decoding theory, this skill guarantees that language model outputs conform 100% to strict syntactical rules (JSON Schema, regular expressions, or context-free grammars) without post-processing parsing errors. Prompting models with "please output valid JSON" is probabilistic and brittle; models intermittently omit commas, truncate brackets, or hallucinate conversational prefixes. This skill enforces formal token logit masking and finite-state machine (FSM) index transitions at inference time.

## Mathematical Constraints
1. **FSM Token Logit Masking**: Convert the target regex or JSON Schema into an equivalent deterministic finite automaton (DFA). At each token generation step, mask all vocabulary logits to $-\infty$ except those corresponding to valid DFA transitions.
2. **Zero Post-Hoc JSON Repair Needed**: Because invalid tokens are blocked at generation time, outputs are guaranteed to parse with zero syntax errors.
3. **No Conversational Prefaces**: Prohibit the model from emitting "Sure, here is your JSON:" or trailing markdown ticks (` ```json `).
4. **Strict Enum Enforcement**: Constrain choice fields to exact enumerated strings defined in the specification.

## Implementation Architecture
1. **Model Schema Specification**: Author the target structure using Pydantic or JSON Schema.
2. **Compiler Translation**: Compile schema into token-level regex or context-free grammar rules.
3. **Inference Execution**: Pass schema to local engine (`llama.cpp` grammar or `outlines` generator).
4. **Validation**: Deserialize generated string directly into strongly typed domain objects.
