# User Punchlist &amp; Feedback Ledger — Notes 2

The items below were investigated and resolved; links point to the commits that landed each fix.

- [x] **Proactive Commentary Toggle**: Fixed by gating idle check-ins and startup briefings on the `Identity & Profile` proactive toggle and sharing the identity singleton across speech engines ([`26daa83`](https://github.com/SynapticSmith/Brahma-Ai-Evo/commit/26daa83)).
- [x] **Subagent Library and Index**: Vendored the 607-template corpus into `subagents/` with `catalog.json` and `agent/subagent_library.py`, then exposed the library on first-call and voice surfaces ([`26daa83`](https://github.com/SynapticSmith/Brahma-Ai-Evo/commit/26daa83), [`6439b2e`](https://github.com/SynapticSmith/Brahma-Ai-Evo/commit/6439b2e)).
- [x] **Duplicate Replies**: Fixed by stopping tool-start status speech and task-card chat mirroring ([`26daa83`](https://github.com/SynapticSmith/Brahma-Ai-Evo/commit/26daa83)).
