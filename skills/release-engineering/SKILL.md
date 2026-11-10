---
name: release-engineering
description: End-to-end production release pipeline enforcing clean working trees, pre-flight checks, changelog compilation, semver bumping, test validation, and git tagging.
metadata:
  domain: devops
  tags: [release, semver, git, changelog, tagging, ci-cd, devops]
triggers:
  - "release engineering"
  - "cut a release"
  - "prepare release"
  - "version bump"
  - "generate changelog"
  - "tag release"
  - "cut new version"
---

# Release Engineering Playbook Skill

## Overview
Drawn from `OpenMonoAgent.ai`'s production release playbook, this skill orchestrates reproducible, high-assurance software releases. A production release is a high-visibility, irreversible event: deploying untested code, omitting changelogs, or releasing with a dirty working tree causes severe regressions and broken downstream builds. This skill enforces strict sequential gating across pre-flight validation, change categorization, semver bumping, full test suite passes, and annotated git tagging.

## Immutable Safety Constraints
1. **Clean Working Tree**: Never cut a release from a dirty working tree or unmerged branch.
2. **Mandatory Test Validation**: Never tag or publish if any unit, integration, or regression test fails.
3. **Dry-Run Guard**: When `--dry-run` is active, generate artifacts and changelogs without pushing tags or publishing packages.
4. **No Secret Leaks**: Audit diffs to ensure no `.env`, certificates (`*.pem`), or credentials are part of the release.
5. **Approval Before Pushing**: Require explicit operator confirmation before pushing git tags or trigger remote deployments.

## Execution Sequence
1. **Pre-flight Check**: Verify git tree cleanliness, target branch alignment, and toolchain readiness.
2. **Change Analysis**: Review commits since the last tag (`git log <last-tag>..HEAD --oneline`) and categorize into SemVer types (`feat`, `fix`, `perf`, `breaking`).
3. **Changelog Generation**: Compile an entry in `CHANGELOG.md` following the Keep a Changelog standard.
4. **SemVer Version Bump**: Update version identifiers in project manifests (`package.json`, `pyproject.toml`, or `*.csproj`).
5. **Full Test Gate**: Run the complete test suite; fail-closed on any failure.
6. **Tagging & Publication**: Create an annotated git tag (`git tag -a v<version> -m "<summary>"`) and push only upon approval.
