# Cookbook: NGINX Dynamic Traffic Splitting & Canary Rollout

## Objective
Configure an NGINX reverse proxy with weighted traffic splitting for safe canary verification and instant rollback.

## Prerequisites
- NGINX or reverse proxy with `split_clients` or `upstream weight`.

## Execution Recipe
1. **Configure Weighted Upstream Ingress**:
   ```nginx
   upstream app_backend {
     server 127.0.0.1:8001 weight=98; # Stable baseline
     server 127.0.0.1:8002 weight=2;  # Canary instance
   }
   server {
     listen 80;
     location / {
       proxy_pass http://app_backend;
       proxy_set_header X-Canary $upstream_addr;
     }
   }
   ```
2. **Reload NGINX Non-Disruptively**:
   ```powershell
   nginx -s reload
   ```
3. **Monitor Error Rates for 5 Minutes**:
   ```powershell
   # Sample canary responses:
   1..100 | ForEach-Object { curl -s -I http://localhost/health } | Select-String "HTTP/1.1 200"
   ```
4. **Promote Canary or Roll Back**:
   - Promote: Set canary weight to 100, reload.
   - Rollback: Set canary weight to 0, reload.
5. **Verification Gate**:
   *Gate: Canary handles requests; error rate $\le 0.01\%$; P99 latency within baseline.*
6. **Remediation**:
   If errors detected, immediately revert upstream weight=0 and reload NGINX in < 1 second.
