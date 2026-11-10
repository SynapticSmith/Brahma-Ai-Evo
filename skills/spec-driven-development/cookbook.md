# Cookbook: Authoring a Software Design Document (SDD)

## Objective
Author a clear, actionable architecture specification that guides autonomous agents or engineers through implementation without design ambiguity.

## Prerequisites
- A target capability, refactor, or subsystem requirement.

## Execution Recipe
1. **Create the Spec Markdown File**:
   Target location: `docs/<FEATURE-NAME>-SPEC.md`.
2. **Document Invariants First**:
   Identify hard constraints before writing functional steps:
   - Dependency rules (e.g., "no external packages at module scope").
   - Safety rules (e.g., "all file writes must be bounded by workspace").
   - Performance budgets (e.g., "turn compilation must complete in < 50ms").
3. **Specify the Component API**:
   Write full Python function/class signatures with type annotations:
   ```python
   def process_payload(data: Dict[str, Any], timeout_s: float = 5.0) -> Tuple[bool, str]:
       ...
   ```
4. **Define Explicit Error Codes**:
   Enumerate all possible error conditions and return types (avoiding bare exceptions).
5. **Draft the Acceptance Criteria Table**:
   Tabulate every feature requirement against its designated test module and function name.
6. **Verification Gate**:
   Review spec against Occam's Razor: prune any speculative features not required for the immediate milestone.
7. **Remediation**:
   If team members or agents encounter ambiguity during implementation, pause and update the spec before editing code.
