---
name: secret-and-leak-scanner
description: Automated pre-commit security audit scanning files, git diffs, and repositories for exposed API keys, high-entropy tokens, certificates, and credentials.
metadata:
  domain: security
  tags: [security, secrets, leak-detection, credentials, pre-commit, audit, entropy, token-scan]
triggers:
  - "secret scanner"
  - "scan for secrets"
  - "detect leaks"
  - "credential scan"
  - "pre-commit security audit"
  - "find exposed keys"
  - "check for leaked tokens"
---

# Secret & Credential Leak Scanner Skill

## Overview
Drawn from `OpenMonoAgent.ai`'s security constraint enforcement and file scanning engine, this skill protects codebases from leaking private keys, cloud credentials, database connection strings, and sensitive API tokens. Leaked secrets lead to catastrophic account takeovers, unauthorized resource consumption, and regulatory breaches. This skill provides automated high-entropy and pattern-based detection across untracked files, staged git diffs, and historical commits.

## Immutable Security Rules
1. **Never Commit Sensitive Extensions**: Immediately block files matching `*.env`, `*.pem`, `*.key`, `*.pfx`, `*.pkcs12`, `id_rsa`, or `credentials.json`.
2. **Never Display Unredacted Secrets in Logs**: Redact all detected tokens in summaries (e.g. `sk_live_...[REDACTED]`).
3. **Fail-Closed on High-Entropy Matches**: Any detected high-entropy string matching known token formats (OpenAI, AWS, GitHub, Slack, Stripe) blocks staging and release.
4. **Mandatory Key Rotation upon History Leak**: If a secret was committed to git history, changing or deleting the file is insufficient; the key must be revoked immediately at the provider.

## Detection Strategy
1. **Filename Patterns**:
   - `.env`, `.env.local`, `.env.production`
   - `id_rsa`, `id_ecdsa`, `id_ed25519`
   - `*.pem`, `*.key`, `*.keystore`, `*.p12`
   - `service_account.json`, `client_secret.json`
2. **High-Entropy Token Signatures**:
   - OpenAI API Key: `sk-[a-zA-Z0-9]{20,}`
   - GitHub Token: `ghp_[a-zA-Z0-9]{36}`, `gho_[a-zA-Z0-9]{36}`, `github_pat_[a-zA-Z0-9_]{82}`
   - AWS Keys: `AKIA[0-9A-Z]{16}`
   - Generic Bearer / Basic Auth headers hardcoded in strings
