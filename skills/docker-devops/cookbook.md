# Cookbook: Docker Build & Container Testing

## Objective
Containerize python applications using minimal multi-stage builds and verify container launch.

## Prerequisites
- Docker CLI or Podman

## Execution Recipe
1. **Audit Dockerfile**:
   Ensure non-root user, slim base image (e.g. `python:3.11-slim`), and cached dependency layers.
2. **Build Image**:
   ```powershell
   docker build -t app:latest .
   ```
3. **Execute Container Smoke Test**:
   ```powershell
   docker run --rm app:latest python --version
   ```
4. **Verification Gate**:
   Verify container exits with code 0 and image size is within budget (< 500MB).
5. **Remediation**:
   If build fails on missing compiler, add build dependencies in builder stage only.
