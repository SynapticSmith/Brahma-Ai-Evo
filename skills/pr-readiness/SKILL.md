---
name: pr-readiness
description: Pre-flight pull request audit protocol enforcing upstream sync, clean test passes, lint zero-defect checks, staged commit cleanliness, and structured PR description generation.
metadata:
  domain: devops
  tags: [git, pr, pull-request, review, sync, lint, testing, workflow]
triggers:
  - "pr readiness"
  - "open pr"
  - "prepare pull request"
  - "ready for review"
  - "create pr"
  - "pr pre-flight"
  - "check pr readiness"
---

# PR Readiness Playbook Skill

## Overview
Derived from `OpenMonoAgent.ai`'s `pr-ready` playbook, this skill ensures that feature and bugfix branches are thoroughly vetted before opening or merging a Pull Request. Reviewer fatigue and broken CI builds stem from submitting un-synced branches, failing tests, or unformatted code. This skill enforces rigorous pre-flight validation so every submitted PR passes CI on the first run and provides reviewers with clean, contextual diffs and summaries.

## Immutable Safety Rules
1. **Never Open PR with Failing Tests**: The local test suite must pass completely before submitting the PR.
2. **Never Open PR from Target Branch**: Never create a PR directly from `main`, `master`, or the protected target branch.
3. **No Uncommitted Spurious Files**: Verify all relevant changes are committed and temporary artifacts (`.tmp`, caches, logs) are excluded.
4. **No Secrets or Credentials**: Audit the diff for accidental `.env` files, API keys, or certificates.
5. **Truthful Descriptions Only**: Construct PR descriptions strictly from the commit log and actual diff; do not invent undocumented features.

## Step-by-Step Procedure
1. **Target Sync Check**: Verify whether the branch is up to date with the remote upstream (`git fetch; git log HEAD..origin/main`). Rebase or merge if behind.
2. **Test Suite Execution**: Run all project unit and regression tests locally. Fail-closed on any error.
3. **Static Analysis & Linting**: Run project linter and formatter. Ensure 0 errors and zero warnings.
4. **Inspect Staged Diffs**: Confirm only intentional files are included in the branch history.
5. **Generate PR Summary**: Extract summary from `git log origin/main..HEAD` detailing Context, Changes, Testing Strategy, and Breaking Changes.
6. **Submit PR**: Open PR via CLI (`gh pr create`) or present structured text for manual submission.
