---
name: circuit-breaker-resilience
description: Distributed systems fault-tolerance protocol implementing Circuit Breaker, Exponential Backoff with Full Jitter, and Bulkhead concurrency isolation.
metadata:
  domain: devops
  tags: [resilience, circuit-breaker, retry, backoff, jitter, fault-tolerance, devops]
triggers:
  - "circuit breaker resilience"
  - "implement circuit breaker"
  - "retry with exponential backoff"
  - "prevent cascading failures"
  - "resilience pattern"
  - "transient fault handling"
  - "circuit breaker state machine"
---

# Circuit Breaker Resilience Skill

## Overview
Drawn from cloud infrastructure engineering and high-availability distributed systems, this skill guides the implementation of circuit breaker patterns and defensive retry policies. When downstream third-party APIs (LLM endpoints, payment gateways, databases) degrade or fail, naive tight-loop retries flood the failing service, causing catastrophic cascading collapse across the entire system. This skill enforces 3-state finite state machines (Closed, Open, Half-Open), decorrelated jitter backoffs, and concurrency bulkheads.

## State Machine Invariants
1. **Closed State**: Normal operation. All requests pass through. Failures increment an error counter within a sliding window.
2. **Open State**: Trips when error threshold is breached (e.g. 5 failures in 10 seconds). All calls fail immediately without touching the downstream network, shedding load.
3. **Half-Open State**: After a recovery sleep window (e.g. 30 seconds), allows a single canary request through. Success resets to Closed; failure resets the Open timer.
4. **Exponential Backoff with Full Jitter Mandatory**: Retries must use `sleep = random(0, min(cap, base * 2^attempt))` to eliminate synchronization thundering herds.

## Architectural Implementation
1. **Failure Classifier**: Distinguish transient errors (HTTP 429, 502, 503, 504, TCP timeout) from permanent errors (HTTP 400, 401, 404). Never retry permanent client errors.
2. **Concurrency Bulkhead**: Bound maximum concurrent in-flight requests per external dependency via thread pool or semaphore.
3. **Degraded Fallback**: Return cached responses, default degraded outputs, or graceful error banners when breaker is Open.
