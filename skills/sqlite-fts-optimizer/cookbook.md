# Cookbook: SQLite FTS5 Index Maintenance & BM25 Tuning

## Objective
Optimize SQLite FTS5 full-text search indexes, merge b-tree segments, and tune BM25 weights.

## Prerequisites
- SQLite3 CLI or Python `sqlite3` module.

## Execution Recipe
1. **Verify Index Integrity**:
   ```sql
   INSERT INTO fts_chunks(fts_chunks) VALUES('integrity-check');
   -- Must return 0 rows (no error)
   ```
2. **Execute Segment Merge & Optimization**:
   Merge b-tree leaf segments to accelerate lookup latency:
   ```sql
   INSERT INTO fts_chunks(fts_chunks) VALUES('optimize');
   PRAGMA optimize;
   ```
3. **Query with Weighted BM25 Ranking**:
   Give higher weight to title (column 0) over content (column 1):
   ```sql
   SELECT page_id, chunk_index, rank
   FROM fts_chunks
   WHERE fts_chunks MATCH 'buddhism philosophy'
   ORDER BY bm25(fts_chunks, 10.0, 1.0)
   LIMIT 10;
   ```
4. **Shrink Database Footprint**:
   ```sql
   VACUUM;
   ```
5. **Verification Gate**:
   *Gate: `integrity-check` passes without errors; query execution completes in < 5ms.*
6. **Remediation**:
   If index corruption is reported, rebuild the virtual table using `INSERT INTO fts_chunks(fts_chunks) VALUES('rebuild')`.
