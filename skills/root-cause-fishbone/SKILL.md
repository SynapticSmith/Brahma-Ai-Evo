---
name: root-cause-fishbone
description: Ishikawa fishbone causal decomposition analyzing defects across 6 dimensions Machine, Method, Material, Manpower, Measurement, and Milieu.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, ishikawa, fishbone, root-cause, failure-analysis, quality]
triggers:
  - "root cause fishbone"
  - "ishikawa diagram"
  - "fishbone failure analysis"
  - "multi factor root cause"
  - "systemic defect breakdown"
---

# Root-Cause Fishbone (Ishikawa) Skill

## Overview
Created by Kaoru Ishikawa at Kawasaki Steel in the 1960s, the Fishbone (or Cause-and-Effect) diagram breaks complex, multi-factor system failures down into 6 core operational branches (the 6 Ms):

1. **Machine (Technology & Runtime)**: OS version, Python interpreter (Store App MSIX vs CPython), hardware drivers, GPU acceleration, PortAudio.
2. **Method (Process & Code)**: Algorithm design, asynchronous event loops, lock acquisition order, prompt engineering.
3. **Material (Data & Schemas)**: Malformed JSON, corrupted SQLite DB, unexpected Unicode characters, BOM headers.
4. **Manpower (Human / Agent Action)**: Stale environment variables, incorrect CLI arguments, misconfigured permissions.
5. **Measurement (Monitoring & Telemetry)**: Uncalibrated thresholds, missing logging levels, false-positive metrics.
6. **Milieu (Environment & Context)**: WebSocket server restart, network latency spikes, file system write locks.
