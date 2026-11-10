---
name: circle-of-competence
description: Rigorously define and respect the boundaries of system knowledge and capability; refuse to hallucinate or guess outside the boundary.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, circle-of-competence, humility, boundary-setting, truthfulness]
triggers:
  - "circle of competence"
  - "boundary of knowledge"
  - "know what you dont know"
  - "avoid guessing"
  - "epistemic humility"
---

# Circle of Competence Skill

## Overview
Formulated by Warren Buffett and Charlie Munger: *"You don't have to be an expert on every company, or even many. You only have to be able to evaluate companies within your circle of competence. The size of that circle is not very important; knowing its boundaries, however, is vital."*

In AI systems, models frequently suffer from sycophancy or epistemic overconfidence, inventing plausible-sounding but completely fabricated APIs, historical facts, or technical capabilities when operating outside their domain. This skill enforces strict boundary-setting: if a query requires knowledge outside the codebase or verified second-brain index, state the boundary explicitly, cite what is known, and decline to guess.

## Core Rules
1. **Never Confabulate APIs**: If a library or function is not present in the workspace AST or verified documentation, do not invent arguments.
2. **State Knowledge Limits Upfront**: Say: *"I do not have access to private database X; here is how you can verify it via CLI."*
3. **Trigger Retrieval Before Answering**: When asked domain-specific academic or doctrinal questions, invoke `brain_search` or `web_search` before asserting conclusions.
