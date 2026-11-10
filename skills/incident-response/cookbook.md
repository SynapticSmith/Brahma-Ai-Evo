# Cookbook: 6-Step Incident Response & Recovery

## Objective
Guide an operator through structured emergency triage, non-destructive mitigation, recovery verification, and postmortem authoring.

## Prerequisites
- Access to service logs, process monitoring, or error telemetry.

## Execution Recipe
1. **Step 1: Gather Logs & Error Spikes**:
   Capture the exact UTC timestamp and error signatures from active logs without restarting processes.
2. **Step 2: Determine Blast Radius**:
   Classify severity: P0 (Total outage), P1 (Critical degradation), P2 (Partial feature failure), P3 (Minor defect). Identify affected endpoints and user count.
3. **Step 3: Review Scope & Select Mitigation Strategy**:
   Choose the least destructive intervention:
   - Config flag rollback
   - Service restart with graceful drain
   - Emergency hotfix commit
   - Traffic throttling / circuit breaking
4. **Step 4: Apply Mitigation**:
   Execute the chosen fix. Record the exact command run and return code.
5. **Step 5: Verify Recovery**:
   Query health check endpoints and monitor error rate for 3 minutes:
   ```powershell
   curl -I http://localhost:<port>/health
   ```
   *Gate: Return code must be 200 OK and error rate must drop to baseline.*
6. **Step 6: Generate Postmortem Document**:
   Write `docs/postmortems/INCIDENT-<DATE>.md` detailing: Timeline, Root Cause, Impact, Mitigation, and Action Items.
7. **Remediation**:
   If the initial mitigation fails to resolve the incident, do not attempt second-order tweaks; trigger immediate rollback to the last known stable release.
