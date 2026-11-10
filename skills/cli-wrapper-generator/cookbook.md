# Cookbook: Authoring an Agent-Native CLI Wrapper

## Objective
Convert an external executable or shell script into a robust Python wrapper complying with Brahma's `ToolSpec` and AST safety standards.

## Prerequisites
- The target binary or CLI utility (e.g. `ffmpeg`, `ripgrep`, `git`, `docker`).

## Execution Recipe
1. **Define CLI Argument Parsing**:
   Create `tools/<name>.py` using `argparse` with explicit types and defaults. Include `--json` flag.
2. **Execute via Safe Process Runner**:
   Invoke the binary via `agent.process.run_captured` to enforce stdin closure and timeout boundaries:
   ```python
   from agent.process import run_captured
   result = run_captured(["binary_name", "--flag", arg], cwd=repo_root, timeout=30.0)
   ```
3. **Format Output as JSON**:
   Parse stdout lines or capture structured output:
   ```python
   if args.json:
       print(json.dumps({"ok": result.returncode == 0, "output": result.stdout}, indent=2))
       return 0 if result.returncode == 0 else 4
   ```
4. **Register in Harness Scripts Index**:
   Re-index verified tools:
   ```powershell
   python tools/scripts_index.py --build
   python tools/scripts_index.py --check
   ```
5. **Verification Gate**:
   Verify the script passes static AST analysis and is registered in `config/scripts.json`.
6. **Remediation**:
   If the tool leaks child processes on timeout, ensure it uses `run_captured` which binds executions to Win32 Job Objects.
