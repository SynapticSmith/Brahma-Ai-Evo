# Cookbook: PostgreSQL Query Plan Optimization & Indexing

## Objective
Diagnose slow PostgreSQL queries using `EXPLAIN (ANALYZE, BUFFERS)` and deploy non-blocking index fixes.

## Prerequisites
- Access to PostgreSQL database via `psql` or connection string.

## Execution Recipe
1. **Run Deep Execution Plan**:
   ```sql
   EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS)
   <YOUR_QUERY_HERE>;
   ```
2. **Audit Bottleneck Nodes**:
   - Check for `Seq Scan` on large tables: indicates missing index on filter columns.
   - Check `Buffers: read=...`: indicates physical disk I/O instead of shared memory cache hit.
   - Check `Rows Removed by Filter`: high numbers indicate poor index selectivity.
3. **Deploy Non-Blocking Index**:
   ```sql
   CREATE INDEX CONCURRENTLY idx_orders_customer_created
   ON orders (customer_id, created_at DESC);
   ```
4. **Re-Run & Compare Execution Plan**:
   Verify node converted from `Seq Scan` to `Index Scan` or `Bitmap Index Scan`.
5. **Verification Gate**:
   *Gate: Execution time drops by $\ge 80\%$ and physical disk reads (`read=`) drop to 0.*
6. **Remediation**:
   If planner still chooses sequential scan, verify row estimates via `ANALYZE orders;` or adjust `random_page_cost`.
