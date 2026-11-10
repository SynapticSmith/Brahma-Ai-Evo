---
name: answering-engine-synthesizer
description: Perplexity-style answering engine synthesis protocol combining multi-source web/vault retrieval, factual claim extraction, inline footnote citations, and contradiction auditing.
metadata:
  domain: research
  tags: [answering-engine, search, citations, synthesis, perplexity-style, footnotes, attribution, research]
triggers:
  - "answering engine synthesizer"
  - "perplexity style answer"
  - "citation grounded answer"
  - "synthesize answer with footnotes"
  - "search and answer engine"
  - "grounded research response"
  - "generate answer with citations"
---

# Answering Engine Synthesizer Skill

## Overview
Drawn from `Vane` and modern AI answer engines (Perplexity, Genspark), this skill governs the synthesis of comprehensive, publication-grade technical answers directly grounded in retrieved document snippets. When answering complex user questions, typical LLM outputs mix accurate knowledge with subtly fabricated details. This skill enforces an explicit attribution architecture: decomposing the response into discrete factual propositions, tying each assertion to a numbered footnote citation `[1]`, and verifying that the cited source directly entails the claim.

## Attribution Rules & Invariants
1. **Inline Bracketed Citations Mandatory**: Every paragraph containing factual assertions must include bracketed citations (e.g. `[1]`, `[2]`) pointing to explicit source entries in the trailing bibliography.
2. **Direct Entailment Check**: The cited reference text must directly support the claim; extrapolation or speculative inference cannot cite a document as proof.
3. **Contradiction Reconciliation**: When two sources disagree on metrics, release dates, or benchmark results, present both perspectives side-by-side with citations rather than picking one arbitrarily.
4. **Structured Output Format**:
   - Direct, BLUF (Bottom Line Up Front) 2-sentence summary.
   - Thematic sections with inline citations.
   - Trailing `## References` list containing URL/file path, page/title, and retrieval timestamp.

## Synthesis Workflow
1. **Source Snippet Numbering**: Assign sequential indices `[1]..[N]` to all retrieved context chunks.
2. **Claim Formulation**: Draft analytical response using only facts present in the numbered pool.
3. **Citation Placement**: Insert source index immediately after the asserted clause.
4. **Attribution Audit**: Verify each citation index maps to an actual reference in the bibliography.
