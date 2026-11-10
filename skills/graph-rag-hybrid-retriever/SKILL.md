---
name: graph-rag-hybrid-retriever
description: Advanced Hybrid Graph-RAG retrieval protocol fusing dense vector similarity, BM25 lexical keyword search, and multi-hop entity graph traversal.
metadata:
  domain: architecture
  tags: [graph-rag, knowledge-graph, hybrid-retrieval, vectors, entities, ragflow, semantica, architecture]
triggers:
  - "graph rag hybrid retriever"
  - "hybrid graph retrieval"
  - "entity knowledge graph rag"
  - "combine dense vector and graph rag"
  - "graph based context retrieval"
  - "subgraph retrieval"
  - "entity relation retrieval"
---

# Graph-RAG Hybrid Retriever Skill

## Overview
Drawn from `ragflow` and `semantica`, this skill governs the synthesis of hybrid retrieval-augmented generation pipelines that fuse dense embeddings, sparse lexical search (BM25), and explicit entity-relation knowledge graphs. Standard naive vector RAG retrieves isolated chunks that lack holistic structural context, causing models to miss transitive connections across documents. This skill extracts entities, maps relationship edges, and expands retrieved chunks with 1-hop and 2-hop graph neighborhood subgraphs.

## Tripartite Retrieval Fusion
1. **Dense Vector Scan**: Computes cosine similarity between query embedding and chunk vectors (captures semantic intent and paraphrasing).
2. **Sparse BM25 Index**: Computes exact term frequency-inverse document frequency matches (captures specific acronyms, error codes, and entity names).
3. **Knowledge Graph Subgraph**: Expands top-scoring nodes by traversing relational edges (`depends_on`, `implements`, `authored_by`, `calls`) to pull structural context.
4. **Reciprocal Rank Fusion (RRF)**: Merges ranked candidate lists into a unified score: $RRF(d) = \sum \frac{1}{k + r_i(d)}$.

## Operational Invariants
1. **Bound Subgraph Expansion**: Cap multi-hop graph expansion to at most 2 hops and maximum 15 neighbor nodes to prevent context explosion.
2. **Deterministic Entity Extraction**: Filter extracted entities through an ontological allowlist or strict capitalized-noun heuristics.
3. **Graph Provenance**: Every entity relationship edge must link back to its originating document chunk ID.
