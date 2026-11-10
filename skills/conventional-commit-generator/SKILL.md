---
name: conventional-commit-generator
description: Analyzes staged diffs, infers semantic scopes, and crafts atomic Conventional Commits messages compliant with Keep a Changelog and SemVer.
metadata:
  domain: coding
  tags: [git, commit, conventional-commits, changelog, semver, diff-analysis, coding]
triggers:
  - "conventional commit"
  - "generate commit message"
  - "write commit message"
  - "conventional commits"
  - "commit staged changes"
  - "craft git commit"
---

# Conventional Commit Generator Skill

## Overview
Drawn from `OpenMonoAgent.ai`'s automated commit playbook, this skill inspects staged diffs, infers the architectural scope of changes, and produces standardized, human-readable Conventional Commits messages. High-quality commit histories simplify release changelogs, streamline regression bisects (`git bisect`), and clarify PR reviews.

## Conventional Commit Format
```
<type>(<scope>): <subject>

[optional body explaining WHY the change was made]

[optional footer(s) referencing BREAKING CHANGE or issue IDs]
```

### Recognized Types
- `feat`: A new feature for the user or downstream consumer.
- `fix`: A bug fix for the user or downstream consumer.
- `refactor`: A code change that neither fixes a bug nor adds a feature.
- `perf`: A code change that improves execution performance.
- `test`: Adding missing tests or correcting existing tests.
- `docs`: Documentation-only changes.
- `chore`: Changes to build process, dependency updates, or auxiliary tools.
- `style`: Changes that do not affect the meaning of code (whitespace, formatting).

## Rules & Constraints
1. **Subject Line Limit**: Subject line must be imperative, lowercase, and $\le 72$ characters. No trailing period.
2. **Body Justification**: Include a 2-4 line body explaining *why* the change was necessary whenever the context is not self-evident.
3. **No Empty Commits**: If `git status` shows a clean tree, do not attempt to commit.
4. **No Secrets**: Never commit files containing passwords, API tokens, or private keys.
