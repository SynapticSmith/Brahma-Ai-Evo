# Cookbook: Reciprocal Rank Fusion & 1-Hop Graph RAG Retrieval

## Objective
Retrieve top documents using combined BM25 keyword matching, vector similarity, and 1-hop entity graph expansion.

## Prerequisites
- SQLite database with FTS5 table, vector embeddings, and an `edges` table `(source, relation, target)`.

## Execution Recipe
1. **Query Lexical & Vector Top-K**:
   Fetch top 10 candidates from BM25 and top 10 from vector cosine distance.
2. **Compute Reciprocal Rank Fusion (RRF)**:
   ```python
   def rrf_merge(bm25_ranks, vec_ranks, k=60):
       scores = {}
       for rank, doc_id in enumerate(bm25_ranks):
           scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
       for rank, doc_id in enumerate(vec_ranks):
           scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
       return sorted(scores.items(), key=lambda x: x[1], reverse=True)
   ```
3. **Expand Top 3 Candidates via 1-Hop Graph Relational Edges**:
   ```sql
   SELECT target, relation FROM entity_edges WHERE source IN (?, ?, ?) LIMIT 10;
   ```
4. **Format Context Block**:
   Assemble primary chunk texts followed by the `[KNOWLEDGE GRAPH RELATIONS]` block.
5. **Verification Gate**:
   *Gate: Combined context block contains both raw text and verified entity edges within token budget.*
6. **Remediation**:
   If entity expansion exceeds context budget, prune leaf nodes with edge degree = 1.
