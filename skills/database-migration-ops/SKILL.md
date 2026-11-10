---
name: database-migration-ops
description: High-assurance database schema migration playbook enforcing dry-runs, rollback script validation, staging execution, and data smoke-testing.
metadata:
  domain: devops
  tags: [database, migration, schema, sqlite, postgres, dry-run, rollback, smoke-test]
triggers:
  - "database migration ops"
  - "run db migration"
  - "schema migration playbook"
  - "safe database migration"
  - "rollback database migration"
  - "database dry run"
---

# Database Migration Ops Skill

## Overview
Drawn from `OpenMonoAgent.ai`'s db-migrate playbook, this skill enforces a zero-data-loss protocol for schema alterations and table migrations across SQLite, PostgreSQL, and other relational databases. Database migrations are Type 1 (one-way door) operations: once data is corrupted or dropped, recovery is catastrophic. This playbook enforces mandatory dry-run validation, pre-flight backup snapshots, forward-and-reverse migration tests, and post-migration smoke checks.

## Immutable Safety Rules
1. **Never Migrate Without a Verified Snapshot**: Take an atomic database snapshot before running migration scripts.
2. **Never Apply DDL Without a Reversible Down Script**: Every `up.sql` or forward migration must have a matching `down.sql` rollback script that has been executed and verified in staging.
3. **Never Lock Production Tables Unnecessarily**: Use non-blocking column additions (`DEFAULT NULL` or shadow tables); avoid table locks during peak traffic.
4. **Mandatory Post-Migration Smoke Test**: Validate query latency and row counts immediately after schema changes.
