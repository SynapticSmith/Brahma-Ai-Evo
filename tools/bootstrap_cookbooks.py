"""Populate OpenMonoAgent-style cookbooks and canonical domains across skills."""
from pathlib import Path

SKILLS = {
    "api-mock-engine": {
        "domain": "coding",
        "title": "API Mock Engine & Fixture Generator",
        "cookbook": """# Cookbook: API Mock Engine & Fixture Generation

## Objective
Generate a lightweight, standalone mock API server with simulated endpoints and synthetic data fixtures.

## Prerequisites
- python 3.11+
- fastapi or flask (optional for server launch)

## Execution Recipe
1. **Define Schema**:
   Identify required endpoints, methods (GET/POST), status codes, and JSON response models.
2. **Scaffold Mock Server Script**:
   Generate `mock_server.py` using standard library `http.server` or `FastAPI`:
   ```powershell
   python -c "import http.server, socketserver; print('Ready to serve mock endpoints')"
   ```
3. **Generate Synthetic Fixtures**:
   Create mock JSON data payloads under `tests/fixtures/<endpoint>.json`.
4. **Verification Gate**:
   Query the mock endpoint with a curl or python script:
   ```powershell
   python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
   ```
5. **Remediation**:
   If port is in use, bind to dynamic port `8080` or increment port number.
"""
    },
    "book-to-knowledge": {
        "domain": "research",
        "title": "Book to Knowledge Ingestion",
        "cookbook": """# Cookbook: Book to Knowledge Ingestion

## Objective
Convert an unprocessed book (EPUB, PDF, DOCX) into clean Markdown, extract atomic knowledge units, and route them to the academic taxonomy spine with Calyx anchoring.

## Prerequisites
- python 3.11+
- tools/ingest_multimodal.py
- tools/extract_knowledge.py

## Execution Recipe
1. **Convert Raw Book to Markdown**:
   ```powershell
   python tools/ingest_multimodal.py --source <path-to-book> --slug <book-slug>
   ```
2. **Extract Atomic Knowledge Units**:
   ```powershell
   python tools/extract_knowledge.py --source book-library/fulltext/<book-slug>.md --taxonomy taxonomy.json
   ```
3. **Commit Calyx Anchors**:
   Record cryptographic ledger constellations for all extracted units:
   ```powershell
   python tools/calyx_sync.py
   ```
4. **Verification Gate**:
   Check that units are placed in `wiki/library/knowledge/` and index is refreshed:
   ```powershell
   python tools/brain_sync.py
   ```
5. **Remediation**:
   If low confidence occurs, review orphaned notes staged in `_inbox/` and manually tag appropriate parent node.
"""
    },
    "browser-automation": {
        "domain": "devops",
        "title": "Browser Automation & Navigation",
        "cookbook": """# Cookbook: Headless Browser Automation

## Objective
Automate web interactions, DOM extraction, and visual screenshot verification.

## Prerequisites
- playwright or selenium installed in environment

## Execution Recipe
1. **Pre-flight Check**:
   Verify browser driver availability.
2. **Execute Headless Task**:
   Launch automation script targeting URL with explicit selectors and wait states:
   ```powershell
   python -c "print('Navigating to target and awaiting DOM content')"
   ```
3. **Extract Content / Capture State**:
   Save HTML snapshot to `cache/browser_dump.html` and screenshot to `.assets/`.
4. **Verification Gate**:
   Verify output file size > 500 bytes and screenshot PNG signature exists.
5. **Remediation**:
   If selector timeout occurs, increase explicit wait timeout from 5s to 15s.
"""
    },
    "code-review": {
        "domain": "coding",
        "title": "Automated Code Review & Quality Audit",
        "cookbook": """# Cookbook: Automated Code Review

## Objective
Perform a structured, line-by-line architectural and syntax review on git diffs before merging.

## Prerequisites
- git
- python 3.11+

## Execution Recipe
1. **Inspect Staged Diffs**:
   ```powershell
   git diff --staged
   ```
2. **Run Linter & Static Analysis**:
   ```powershell
   python -m ruff check .
   ```
3. **Execute Targeted Regression Tests**:
   ```powershell
   python -m pytest tests -q
   ```
4. **Verification Gate**:
   Ensure 0 lint errors, 0 test failures, and no unprotected secret keys or temporary debug statements.
5. **Remediation**:
   Use `ruff check --fix .` for automated fixes; revert manual regressions if tests fail.
"""
    },
    "database-architect": {
        "domain": "architecture",
        "title": "Database Schema Design & Migration",
        "cookbook": """# Cookbook: Database Architecture & Zero-Downtime Migration

## Objective
Design, version, and migrate relational and vector database schemas without data loss.

## Prerequisites
- sqlite3

## Execution Recipe
1. **Inspect Current Schema**:
   Extract DDL and table pragma info:
   ```powershell
   python -c "import sqlite3; conn=sqlite3.connect('second_brain.db'); print(conn.execute('PRAGMA user_version').fetchone())"
   ```
2. **Draft Forward Migration**:
   Create SQL statements using `CREATE TABLE IF NOT EXISTS` and transactional updates.
3. **Verify Integrity & Foreign Keys**:
   ```powershell
   python -c "import sqlite3; conn=sqlite3.connect('second_brain.db'); print(conn.execute('PRAGMA integrity_check').fetchall())"
   ```
4. **Verification Gate**:
   Query test row insertions and verify `user_version` counter increments.
5. **Remediation**:
   Rollback transaction immediately if `sqlite3.DatabaseError` is raised.
"""
    },
    "desloppify": {
        "domain": "coding",
        "title": "Code Desloppification & Refactoring",
        "cookbook": """# Cookbook: Code Desloppification & Clean Refactoring

## Objective
Purge code slop, eliminate dead branches, enforce strict type hints, and align with project architecture.

## Prerequisites
- python 3.11+
- ruff

## Execution Recipe
1. **Audit Complexity & Unused Code**:
   ```powershell
   python -m ruff check . --select F,E,W,UP,I
   ```
2. **Format & Sort Imports**:
   ```powershell
   python -m ruff check --fix .
   ```
3. **Enforce Type Invariants & Clean Signatures**:
   Remove wildcard imports (`from x import *`), add return annotations, and eliminate duplicate logic.
4. **Verification Gate**:
   ```powershell
   python -m ruff check .
   python -m pytest tests -q
   ```
5. **Remediation**:
   If refactoring breaks tests, revert via `git checkout -p` and perform targeted edits.
"""
    },
    "docker-devops": {
        "domain": "devops",
        "title": "Containerization & Multi-Stage Docker Builds",
        "cookbook": """# Cookbook: Docker Build & Container Testing

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
"""
    },
    "first-principles-thinking": {
        "domain": "cognitive",
        "title": "First-Principles Deconstruction & Synthesis",
        "cookbook": """# Cookbook: First-Principles Problem Solving

## Objective
Deconstruct a difficult engineering or conceptual problem down to bedrock truths and build up an optimal solution.

## Prerequisites
- None

## Execution Recipe
1. **Identify and Clarify the Challenge**:
   Write down the surface problem and identify existing assumptions.
2. **Deconstruct to Indisputable Truths**:
   Strip away analogies, conventions, and received wisdom. Ask: *What physical, computational, or mathematical facts are impossible to dispute?*
3. **Synthesize from Fundamentals**:
   Rebuild the architecture from those foundational truths. Ignore "how it has always been done."
4. **Verification Gate**:
   Validate that the new design introduces fewer dependencies, lowers complexity, and solves the root bottleneck.
5. **Remediation**:
   If an assumption creeps back in, re-apply the "5-Whys" test to challenge it.
"""
    },
    "fullstack-scaffolder": {
        "domain": "architecture",
        "title": "Fullstack Application Scaffolding",
        "cookbook": """# Cookbook: Fullstack Scaffolding

## Objective
Scaffold a modular frontend and backend repository with clean configuration and dev tooling.

## Prerequisites
- python 3.11+ / node.js

## Execution Recipe
1. **Generate Root Structure**:
   Scaffold `backend/`, `frontend/`, `tests/`, and `config/`.
2. **Configure Package Managers**:
   Generate `pyproject.toml` or `package.json` with pinned development scripts.
3. **Scaffold Entrypoints**:
   Create minimal `main.py` and healthcheck test.
4. **Verification Gate**:
   Run the health test and ensure server starts cleanly.
5. **Remediation**:
   If dependencies conflict, lock versions in a virtual environment.
"""
    },
    "git-workflow": {
        "domain": "devops",
        "title": "Git Workflow & Atomic Commit Protocol",
        "cookbook": """# Cookbook: Git Branching & Atomic Commits

## Objective
Manage git branches, worktrees, and atomic commits safely without dirtying working trees.

## Prerequisites
- git

## Execution Recipe
1. **Check Status & Branch Cleanliness**:
   ```powershell
   git status
   ```
2. **Create Feature Branch or Worktree**:
   ```powershell
   git checkout -b feature/<task-name>
   ```
3. **Stage Specific Files & Commit**:
   ```powershell
   git add <path/to/file>
   git commit -m "type(scope): clear description of change"
   ```
4. **Verification Gate**:
   ```powershell
   git log -n 1 --stat
   ```
5. **Remediation**:
   If incorrect files are staged, unstage with `git restore --staged <file>`.
"""
    },
    "github-automation": {
        "domain": "devops",
        "title": "GitHub Actions CI/CD Automation",
        "cookbook": """# Cookbook: GitHub Actions Workflow Creation

## Objective
Configure GitHub Actions workflows for automated testing, linting, and continuous integration.

## Prerequisites
- git

## Execution Recipe
1. **Scaffold Workflow Directory**:
   Create `.github/workflows/` if not present.
2. **Draft CI Workflow (`.github/workflows/ci.yml`)**:
   Include triggers (`on: [push, pull_request]`), OS matrix, Python setup, and test runner steps.
3. **Verify YAML Syntax**:
   ```powershell
   python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
   ```
4. **Verification Gate**:
   Verify workflow file exists, passes YAML parser, and references valid branch names.
5. **Remediation**:
   Fix indentation errors if YAML parsing fails.
"""
    },
    "icm-architect": {
        "domain": "architecture",
        "title": "Interpretable Context Methodology Scaffolder",
        "cookbook": """# Cookbook: ICM Workspace Structuring

## Objective
Organize problem spaces into deterministic, filesystem-backed agent architectures.

## Prerequisites
- python 3.11+

## Execution Recipe
1. **Analyze Topology**:
   Map input sources, intermediate staging zones, and permanent asset stores.
2. **Scaffold Directory Layout**:
   Create `input/`, `staging/`, `canonical/`, and `config/` directories.
3. **Define Routing Rules**:
   Write declarative JSON or YAML index defining transitions between stages.
4. **Verification Gate**:
   Verify that each stage contains a README or index.md defining its contracts.
5. **Remediation**:
   If notes linger in staging, trigger extraction router to resolve destination.
"""
    },
    "performance-profiler": {
        "domain": "coding",
        "title": "Python Performance & Memory Profiling",
        "cookbook": """# Cookbook: Performance Profiling & Optimization

## Objective
Profile execution time and memory allocations to identify and remediate bottlenecks.

## Prerequisites
- python 3.11+
- cProfile

## Execution Recipe
1. **Run cProfile on Target Code**:
   ```powershell
   python -m cProfile -s cumulative <script.py>
   ```
2. **Analyze Hot Paths**:
   Identify functions consuming > 80% of execution time.
3. **Apply Targeted Optimization**:
   Replace repeated allocations with generators, vectorize math, or add caching.
4. **Verification Gate**:
   Re-run profile and verify execution time reduction (> 20%).
5. **Remediation**:
   If optimization changes outputs, run unit test suite to detect regressions.
"""
    },
    "security-auditor": {
        "domain": "security",
        "title": "Security & Dependency Vulnerability Audit",
        "cookbook": """# Cookbook: Security Audit & Secret Scanning

## Objective
Scan codebase for hardcoded credentials, API keys, unsafe subprocess calls, and known vulnerabilities.

## Prerequisites
- git
- python 3.11+

## Execution Recipe
1. **Scan for Hardcoded Secrets**:
   Search for common key prefixes (`AIza`, `sk-`, `ghp_`, `Bearer`):
   ```powershell
   git grep -E "(AIza|sk-[a-zA-Z0-9]{20,}|ghp_)"
   ```
2. **Audit Shell Invocations**:
   Check for `shell=True` without sanitization in python files:
   ```powershell
   git grep "shell=True"
   ```
3. **Verify File Permissions & Sandbox Policies**:
   Inspect `config/agent_permissions.example.json` to ensure least-privilege defaults.
4. **Verification Gate**:
   Verify 0 exposed secrets and all subprocess executions use array arguments (`shell=False`).
5. **Remediation**:
   Move detected keys to environment variables and scrub git history if needed.
"""
    },
    "test-driven-development": {
        "domain": "coding",
        "title": "Test-Driven Development (Red-Green-Refactor)",
        "cookbook": """# Cookbook: Test-Driven Development Loop

## Objective
Implement features using strict test-driven development: write failing test, write minimal passing code, refactor.

## Prerequisites
- python 3.11+
- pytest

## Execution Recipe
1. **Write Failing Test (Red)**:
   Create `tests/test_<feature>.py` asserting expected input/output behavior.
2. **Run Test to Verify Failure**:
   ```powershell
   python -m pytest tests/test_<feature>.py -q
   ```
3. **Implement Minimal Code (Green)**:
   Write the simplest implementation that satisfies the test assertion.
4. **Run Test to Verify Success**:
   ```powershell
   python -m pytest tests/test_<feature>.py -q
   ```
5. **Refactor & Verify**:
   Clean up code, run full test suite, and check linting:
   ```powershell
   python -m ruff check .
   python -m pytest tests -q
   ```
6. **Remediation**:
   If test passes without code changes, refine assertion to test boundary conditions.
"""
    },
    "web-scraper-stealth": {
        "domain": "research",
        "title": "Stealth Web Scraping & Data Extraction",
        "cookbook": """# Cookbook: Web Scraping & Content Ingestion

## Objective
Fetch, sanitize, and structure web content with polite rate limiting and fallback headers.

## Prerequisites
- python 3.11+

## Execution Recipe
1. **Check robots.txt**:
   Inspect target site's robots.txt for disallowed routes and crawl delays.
2. **Execute Polite Fetch**:
   Use standard User-Agent header, timeout clamping, and exponential backoff.
3. **Sanitize & Convert HTML to Markdown**:
   Strip scripts, styles, and navigation elements. Extract article body.
4. **Verification Gate**:
   Check that extracted text is non-empty, contains article headers, and has no raw HTML tags.
5. **Remediation**:
   If 403 Forbidden is returned, rotate User-Agent or fall back to cached copy.
"""
    }
}

def main():
    skills_root = Path("skills")
    updated = 0
    for name, meta in SKILLS.items():
        skill_dir = skills_root / name
        if not skill_dir.is_dir():
            continue

        # Write cookbook.md
        cb_path = skill_dir / "cookbook.md"
        cb_path.write_text(meta["cookbook"].strip() + "\n", encoding="utf-8")

        # Update SKILL.md frontmatter with domain
        skill_file = skill_dir / "SKILL.md"
        content = skill_file.read_text(encoding="utf-8")
        if "domain:" not in content:
            # Inject domain into frontmatter
            parts = content.split("---", 2)
            if len(parts) >= 3:
                fm = parts[1]
                body = parts[2]
                new_fm = f"domain: {meta['domain']}\n" + fm.strip() + "\n"
                skill_file.write_text(f"---\n{new_fm}---\n{body}", encoding="utf-8")

        updated += 1
        print(f"[+] Configured cookbook and domain for: {name}")

    print(f"Successfully bootstrapped {updated} skill cookbooks.")

if __name__ == "__main__":
    main()
