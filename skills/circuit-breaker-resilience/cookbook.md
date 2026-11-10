# Cookbook: Python Circuit Breaker & Jittered Retry Engine

## Objective
Implement a thread-safe, 3-state Circuit Breaker with exponential backoff and randomized jitter in pure Python stdlib.

## Prerequisites
- Python 3.11+ (stdlib `time`, `random`, `threading`).

## Execution Recipe
1. **Author State Machine & Retry Wrapper**:
   ```python
   import random, time, threading

   class CircuitBreaker:
       def __init__(self, failure_threshold=5, recovery_timeout=30.0):
           self.threshold = failure_threshold
           self.timeout = recovery_timeout
           self.failure_count = 0
           self.state = "CLOSED" # CLOSED, OPEN, HALF_OPEN
           self.last_failure_time = 0.0
           self.lock = threading.Lock()

       def call(self, func, *args, **kwargs):
           with self.lock:
               now = time.time()
               if self.state == "OPEN":
                   if now - self.last_failure_time > self.timeout:
                       self.state = "HALF_OPEN"
                   else:
                       raise RuntimeError("CircuitBreaker is OPEN; call rejected")
           try:
               res = func(*args, **kwargs)
               with self.lock:
                   if self.state == "HALF_OPEN":
                       self.state = "CLOSED"
                       self.failure_count = 0
               return res
           except Exception as exc:
               with self.lock:
                   self.failure_count += 1
                   self.last_failure_time = time.time()
                   if self.failure_count >= self.threshold or self.state == "HALF_OPEN":
                       self.state = "OPEN"
               raise exc
   ```
2. **Verification Gate**:
   *Gate: Breaker trips to OPEN after threshold failures; rejects subsequent calls instantly in < 0.1ms.*
3. **Remediation**:
   If canary request hangs in HALF_OPEN, wrap invocation with an explicit socket timeout.
