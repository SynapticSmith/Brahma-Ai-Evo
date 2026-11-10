# Cookbook: Autonomous Multi-Source Web Research & Synthesis

## Objective
Execute multi-query search, triangulate claims across authoritative domains, and author a citation-grounded research note.

## Prerequisites
- Web search / scraper tool or API connectivity.

## Execution Recipe
1. **Generate 3 Search Angles**:
   - Query 1: `<topic> official documentation architecture`
   - Query 2: `<topic> github implementation benchmarks`
   - Query 3: `<topic> limitations pitfalls failure modes`
2. **Execute Searches & Extract Clean Content**:
   Query top 3 distinct domains per angle; extract headings and code snippets.
3. **Triangulate Invariants**:
   Identify claims verified by $\ge 2$ independent sources. Flag conflicting statements as open questions.
4. **Author Research Note**:
   Draft markdown document with:
   - **Executive Summary**: Core definition and purpose.
   - **Technical Deep-Dive**: Invariants, data structures, and APIs.
   - **Trade-offs**: Failure modes and scaling limits.
   - **Citations**: Full URLs and dates.
5. **Verification Gate**:
   *Gate: All factual claims cite a verifiable URL; zero unsourced assertions.*
6. **Remediation**:
   If conflicting claims cannot be resolved, document both positions explicitly with respective citations.
