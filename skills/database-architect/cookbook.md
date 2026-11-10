# Cookbook: Database Architecture & Zero-Downtime Migration

## Objective
Design, version, and migrate relational and vector database schemas without data loss.

## Prerequisites
- sqlite3

## Execution Recipe
1. **Inspect Current Schema**:
   Extract DDL and table pragma info:
   ```powershell
   python -c "import sqlite3; conn=sqlite3.connect('second_brain.db'); print(conn.execute('PRAGMA user_version').fetchone())"
   ```
2. **Draft Forward Migration**:
   Create SQL statements using `CREATE TABLE IF NOT EXISTS` and transactional updates.
3. **Verify Integrity & Foreign Keys**:
   ```powershell
   python -c "import sqlite3; conn=sqlite3.connect('second_brain.db'); print(conn.execute('PRAGMA integrity_check').fetchall())"
   ```
4. **Verification Gate**:
   Query test row insertions and verify `user_version` counter increments.
5. **Remediation**:
   Rollback transaction immediately if `sqlite3.DatabaseError` is raised.
