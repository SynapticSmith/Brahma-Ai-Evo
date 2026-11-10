---
name: test-suite-flakiness-hunter
description: Diagnostic protocol for hunting, isolating, reproducing, and eliminating flaky and non-deterministic test suite failures caused by race conditions, leaks, and timing.
metadata:
  domain: coding
  tags: [testing, flakiness, pytest, async, race-conditions, test-runner, debugging, coding]
triggers:
  - "test suite flakiness hunter"
  - "fix flaky test"
  - "debug intermittent test failure"
  - "isolate non-deterministic tests"
  - "flakiness hunt"
  - "race condition test"
  - "reproduce flaky failure"
---

# Test Suite Flakiness Hunter Skill

## Overview
Drawn from `superpowers` and high-reliability CI test engineering, this skill provides a rigorous diagnostic sequence to isolate, reproduce, and permanently eliminate flaky, non-deterministic test failures. Flaky tests erode developer trust in CI, mask genuine regressions, and waste hours of debugging time. This skill systematically classifies the root cause—asynchronous race conditions, shared mutable state leaks, unseeded pseudo-random generators, time/timezone drift, or file system lock contention—and refactors tests to be deterministic.

## Flakiness Taxonomy & Invariants
1. **Never Fix Flakiness with `time.sleep()`**: Adding arbitrary delays merely hides race conditions and slows down test suites. Use condition-polling loops with explicit timeouts instead (`poll_until(condition, timeout=5.0)`).
2. **Order Independence**: Every test must pass regardless of the order in which test files or functions are executed. Tests must never rely on side-effects left by prior runs.
3. **Mock Clock & Time Determinism**: Any code measuring time intervals or comparing timestamps must use frozen clocks (`freezegun` or monkeypatched `time.monotonic`).
4. **Isolated Temp Directories**: Tests touching disk must use fresh, isolated temporary directories (`tmp_path` fixture) that clean up automatically.

## Hunting Sequence
1. **Stress Reproduction**: Execute the failing test in a high-iteration loop (`pytest --count=50` or `pytest-repeat`) to measure failure probability.
2. **Order Shuffling**: Run test suite with randomized execution order (`pytest --random-order`) to detect state leaks across tests.
3. **Root-Cause Isolation**: Inspect async event loop synchronization, unclosed sockets, un-reset singletons, and file handles.
4. **Atomic Refactoring**: Replace sleeps with event callbacks or polling predicates, seed random generators, and verify 100 consecutive clean passes.
