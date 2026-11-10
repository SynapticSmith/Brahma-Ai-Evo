# Cookbook: Authoring Syntax-Valid Mermaid Architecture Diagrams

## Objective
Author a clean, syntax-valid Mermaid diagram visualizing a system's components, seams, or lifecycles.

## Prerequisites
- System components, API interfaces, and dataflow direction to visualize.

## Execution Recipe
1. **Choose Direction and Layout**:
   - For sequential pipelines: `flowchart LR`.
   - For hierarchical trees: `flowchart TD`.
   - For message exchanges: `sequenceDiagram`.
2. **Define Subgraphs for Logical Seams**:
   Group components into functional clusters:
   ```mermaid
   flowchart TD
       subgraph UI["User Interface Layer"]
           HUD["HUD Window"]
       end
       subgraph CORE["Harness Core"]
           RT["AgentRuntime"]
       end
       HUD -->|dispatch| RT
   ```
3. **Quote All Special Characters**:
   Ensure every label with spaces, brackets, or math symbols is double-quoted (`["..."]`).
4. **Style Key Decision Nodes**:
   Use standard shapes: diamonds (`{"Decision?"}`) for branching, cylinders (`[("Database")]`) for storage.
5. **Verification Gate**:
   Validate Mermaid syntax rendering without parse exceptions.
6. **Remediation**:
   If a rendering syntax error occurs, check for unquoted parentheses `(` `)` or reserved keywords in node IDs.
