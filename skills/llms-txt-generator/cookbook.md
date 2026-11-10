# Cookbook: Project LLMs.txt & LLMs-Full.txt Generation

## Objective
Generate standardized `llms.txt` and `llms-full.txt` index manifests from repository markdown documentation.

## Prerequisites
- Repository documentation directory (`docs/` or root markdown files).
- Python 3.11+.

## Execution Recipe
1. **Catalog Markdown Specifications**:
   ```powershell
   Get-ChildItem -Path docs/ -Filter "*.md" | Select-Object Name, FullName
   ```
2. **Compile `llms.txt` Header & Core Links**:
   Format project summary and annotate each primary document:
   ```markdown
   # <Project Name>
   > <Canonical project mission and invariants>

   ## Core Documentation
   - [Architecture Spec](docs/ARCHITECTURE-SPEC.md): System map and invariants.
   - [Harness Guide](docs/AGENTIC-HARNESS.md): Agent runtime and tool protocol.
   ```
3. **Assemble `llms-full.txt`**:
   Concatenate all referenced markdown documents with clear section delimiters into a single offline reference file.
4. **Verification Gate**:
   *Gate: `llms.txt` passes markdown lint; all link targets exist on disk.*
5. **Remediation**:
   If broken relative links are detected, update paths to match actual file system locations.
