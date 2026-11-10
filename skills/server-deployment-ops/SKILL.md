---
name: server-deployment-ops
description: Automated host and container server deployment playbook enforcing repo sync, scope review, rolling compose restart, health verification, and rollback guards.
metadata:
  domain: devops
  tags: [deploy, server, docker-compose, hosting, devops, rolling-deploy, healthcheck]
triggers:
  - "server deployment ops"
  - "deploy backend"
  - "update the server"
  - "pull and deploy"
  - "deploy server"
  - "rolling deploy"
  - "host deployment"
---

# Server Deployment Ops Skill

## Overview
Drawn from `OpenMonoAgent.ai`'s `server-deploy` playbook, this skill governs the safe deployment and rolling update of services on bare-metal or containerized host environments. Untracked deployments and unverified restarts cause silent service failures and downtime. This playbook enforces an explicit approval gate before executing restart commands, strictly isolates host-level operations, and verifies HTTP health status codes before declaring success.

## Immutable Safety Rules
1. **Never Deploy Before Scope Approval**: Present the commit hash, changed files, and exact shell command to the operator before restarting services.
2. **Never Expose Secrets**: Redact private keys, database passwords, and environment credentials from all deployment logs.
3. **No Destructive Commands**: Never execute `rm -rf /`, system reboot, or unverified volume purges.
4. **Mandatory Post-Deploy Health Check**: Every deployment must query an HTTP `/health` or status probe. Non-2xx responses immediately trigger an alert and halt rollout.
5. **Non-Interactive Execution**: Run git and docker commands with prompts disabled (`GIT_TERMINAL_PROMPT=0`).

## Deployment Pipeline
1. **Sync Working Tree**: Pull latest changes on target branch (`git pull --ff-only origin <branch>`).
2. **Review Scope**: Summarize incoming commits (`git log @{u}..HEAD --oneline`) and impacted services.
3. **Rolling Build & Up**: Build images with `--no-cache` or cached layers and launch with `docker compose up -d --remove-orphans`.
4. **Health Probe**: Poll the health check endpoint for 30-60 seconds until a `200 OK` is returned.
5. **Log Audit**: Inspect the tail of service container logs for startup exceptions.
