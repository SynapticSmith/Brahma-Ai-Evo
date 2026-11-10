# Cookbook: Zero-Downtime Safe Database Migrations

## Objective
Execute schema migrations with pre-flight backup snapshots, dry-run SQL inspection, and validated rollback scripts.

## Prerequisites
- Database connection string or SQLite file path.
- Migration script file containing DDL statements.

## Execution Recipe
1. **Create Pre-Flight Backup Snapshot**:
   Create an atomic point-in-time copy:
   ```powershell
   Copy-Item -Path "data.db" -Destination "data.db.pre_migrate_$(Get-Date -Format yyyyMMdd_HHmmss)"
   ```
2. **Execute Schema Dry-Run**:
   Inspect generated SQL statements and explain execution plan without committing changes:
   ```powershell
   python tools/db_migrate.py --dry-run --plan
   ```
3. **Validate Rollback Script in Staging / Temporary DB**:
   Execute forward migration followed by rollback script on a shadow database to confirm clean revert:
   ```powershell
   python tools/db_migrate.py --db-path "data.test.db" --up
   python tools/db_migrate.py --db-path "data.test.db" --down
   ```
4. **Apply Production Migration**:
   Run forward migration in an explicit transaction block:
   ```powershell
   python tools/db_migrate.py --apply
   ```
5. **Run Post-Migration Smoke Tests**:
   Verify row counts, table existence, and index lookups:
   ```powershell
   python -c "import sqlite3; conn = sqlite3.connect('data.db'); print(conn.execute('PRAGMA integrity_check').fetchall())"
   ```
6. **Verification Gate**:
   `PRAGMA integrity_check` returns `[('ok',)]` and all primary application queries return within baseline latency.
7. **Remediation**:
   If schema errors occur or queries fail, immediately restore the pre-flight snapshot:
   ```powershell
   Copy-Item -Path "data.db.pre_migrate_*" -Destination "data.db" -Force
   ```
