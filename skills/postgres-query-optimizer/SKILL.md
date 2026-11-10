---
name: postgres-query-optimizer
description: PostgreSQL query performance optimization, EXPLAIN ANALYZE execution plan breakdown, sequential scan elimination, index design, and slow query tuning.
metadata:
  domain: devops
  tags: [postgres, sql, performance, explain-analyze, indexing, query-optimizer, devops]
triggers:
  - "postgres query optimizer"
  - "optimize postgres query"
  - "analyze postgres query plan"
  - "slow sql query"
  - "explain analyze"
  - "fix sequential scan"
---

# PostgreSQL Query Optimizer Skill

## Overview
Drawn from `pgbot`, this skill provides a rigorous diagnostic protocol for identifying, analyzing, and optimizing slow PostgreSQL queries. Unindexed foreign keys, suboptimal JOIN orders, and accidental sequential scans (`Seq Scan`) cripple application throughput and lock database connections. This skill breaks down execution plans, calculates buffer hit ratios, recommends targeted B-tree/GIN/BRIN indexing, and rewrites queries for maximum execution velocity.

## Diagnostic Rules & Invariants
1. **Always Use EXPLAIN (ANALYZE, BUFFERS)**: Raw `EXPLAIN` produces estimates. `EXPLAIN (ANALYZE, BUFFERS)` reveals actual runtime cost, execution loops, and shared buffer cache hits vs disk reads.
2. **Never Add Indexes Blindly**: Every index imposes a write penalty (`INSERT`/`UPDATE`/`DELETE`). Validate index selectivity and ensure the index size fits into shared memory.
3. **Use CREATE INDEX CONCURRENTLY in Production**: Adding an index without `CONCURRENTLY` acquires an `EXCLUSIVE LOCK`, blocking all concurrent writes to the table.
4. **Target High-Impact Cost Nodes**: Focus on nodes with highest cost or row count discrepancy (`actual rows` $\gg$ `estimated rows` indicates stale table statistics; run `ANALYZE`).

## Optimization Strategy
1. **Plan Inspection**: Identify `Seq Scan` on tables $>10,000$ rows, `Hash Join` spilling to disk (`external merge`), or high `Buffers: read=...`.
2. **Table Statistics Check**: Verify last analyze timestamp (`pg_stat_user_tables`). Run `ANALYZE <table>;` if stale.
3. **Index Strategy**: Design compound or partial indexes (`WHERE active = true`) tailored to the query filter and sorting clauses.
4. **Query Rewriting**: Refactor `NOT IN (SELECT ...)` to `NOT EXISTS` or `LEFT JOIN ... WHERE NULL` to prevent Cartesian materialization.
