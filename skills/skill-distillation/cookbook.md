# Cookbook: Distilling Technical Resources into Skills

## Objective
Convert raw technical guides, READMEs, or logs into standardized `SKILL.md` and `cookbook.md` files.

## Prerequisites
- Source technical documentation or execution transcript.

## Execution Recipe
1. **Extract Core Capability and Name**:
   Determine the kebab-case skill identifier (e.g. `ffmpeg-transcoder`).
2. **Assign Academic Domain**:
   Classify into: `cognitive`, `coding`, `architecture`, `devops`, `security`, or `research`.
3. **Identify High-Precision Triggers**:
   List 4 to 8 concrete user query phrases that signal the intent to use this skill.
4. **Draft `SKILL.md`**:
   Write YAML frontmatter followed by overview, procedural steps, and invariants.
5. **Draft `cookbook.md`**:
   Write the 5 mandatory sections:
   - `## Objective`
   - `## Prerequisites`
   - `## Execution Recipe`
   - `## Verification Gate`
   - `## Remediation`
6. **Rebuild Skills Index**:
   ```powershell
   python tools/skills_index.py --build
   python tools/skills_index.py --check
   ```
7. **Verification Gate**:
   Ensure `python -m pytest tests/test_skills_index.py -v` passes cleanly.
8. **Remediation**:
   If index validation fails on YAML or missing sections, fix the frontmatter indentation and re-run `--build`.
