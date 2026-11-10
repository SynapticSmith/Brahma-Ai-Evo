---
name: confirmation-bias-inversion
description: Karl Popper falsification protocol actively seeking disconfirming evidence, contrary test cases, and edge-case attacks to invalidate engineering hypotheses.
metadata:
  domain: cognitive
  tags: [mental-model, confirmation-bias, falsification, red-teaming, critical-thinking, cognitive]
triggers:
  - "confirmation bias inversion"
  - "invert confirmation bias"
  - "hunt for disconfirming evidence"
  - "falsification testing"
  - "disprove hypothesis"
  - "seek contrary evidence"
  - "popperian falsification"
---

# Confirmation Bias Inversion Skill

## Overview
Drawn from `cc-thinking-skills` and Karl Popper's Philosophy of Science, this skill counters the natural cognitive inclination to search exclusively for evidence that confirms one's pre-existing belief or desired conclusion. When engineers write tests, they naturally author "happy path" assertions verifying that their code behaves properly under ideal inputs, unconsciously avoiding input combinations that break their logic. This skill enforces Popperian Falsification: a hypothesis can never be proven true by 1,000 positive examples, but can be definitively disproven by a single counterexample.

## Falsification Invariants
1. **The Falsification Goal**: The primary objective of testing is not to prove that the code works; it is to actively attempt to break the code. A test suite that never fails during authoring has tested nothing.
2. **Negative Space Exploration**: For every valid input test case, author at least 2 adversarial counter-tests:
   - Malformed boundary types (None, empty string, negative integer, NaN, infinity, massive payload).
   - Concurrent race attempts (simultaneous writes to the same key).
   - System resource exhaustion (zero disk space, network socket timeout).
3. **Seek Disconfirming Production Evidence**: When investigating a bug report, actively search for logs that contradict your favored explanation before committing to a fix.

## Falsification Workflow
1. **Formulate Explicit Hypothesis**: State: "We believe defect X is caused by component Y under condition Z."
2. **Design Falsification Test**: Create an experiment specifically designed to refute the hypothesis (e.g. trigger condition Z without component Y).
3. **Run Adversarial Fuzzing**: Subject inputs to randomized permutation and edge case injection.
4. **Accept Counter-Evidence**: If the counter-test passes or refutes the claim, discard the hypothesis immediately without rationalization.
