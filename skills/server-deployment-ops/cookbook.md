# Cookbook: Zero-Downtime Server Deployment & Verification

## Objective
Safely pull code updates, rebuild containers, execute rolling restarts, and verify service health probes.

## Prerequisites
- SSH / local shell access to server host.
- Docker & Docker Compose installed.

## Execution Recipe
1. **Sync Target Branch**:
   ```powershell
   git fetch origin main; git pull --ff-only origin main
   ```
2. **Review Deployment Scope**:
   ```powershell
   git log -1 --stat
   docker compose config --services
   ```
3. **Execute Rolling Rebuild & Launch**:
   ```powershell
   docker compose up -d --build --remove-orphans
   ```
4. **Poll Health Check Probe**:
   ```powershell
   curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health
   ```
5. **Inspect Startup Logs**:
   ```powershell
   docker compose logs --tail 30
   ```
6. **Verification Gate**:
   *Gate: Health check probe returns 200 OK and container logs show 0 fatal errors.*
7. **Remediation**:
   If health check returns non-200 or times out, immediately roll back to previous image tag:
   ```powershell
   docker compose rollback || docker compose up -d --build --rollback
   ```
