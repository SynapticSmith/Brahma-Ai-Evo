# Cookbook: Production Release & Version Tagging

## Objective
Orchestrate a verified, reproducible release with changelog generation, semver bump, and annotated git tagging.

## Prerequisites
- Clean git working tree on target release branch (`main` / `release`).
- Full test runner configured and passing.

## Execution Recipe
1. **Pre-Flight Validation**:
   ```powershell
   git status --porcelain
   # Must return 0 lines
   ```
2. **Analyze Commits Since Last Tag**:
   ```powershell
   $lastTag = git describe --tags --abbrev=0 2>$null
   if (-not $lastTag) { git log --oneline -n 20 } else { git log "$lastTag..HEAD" --oneline }
   ```
3. **Draft Changelog & Bump Version**:
   Update `CHANGELOG.md` with Added/Fixed/Changed entries. Bump version in project manifest (`pyproject.toml`, `package.json`, or version file).
4. **Execute Verification Gate**:
   ```powershell
   py -3.11 -m pytest tests -q
   py -3.11 -m ruff check .
   ```
   *Gate: All tests must pass and 0 lint warnings.*
5. **Create Tag & Publish (or Dry Run)**:
   ```powershell
   git commit -am "chore(release): bump version to v<new_version>"
   git tag -a "v<new_version>" -m "Release v<new_version>"
   ```
6. **Remediation**:
   If tests fail during validation, do not tag. Revert version bump edits via `git restore .` and address defects before retrying.
