#!/usr/bin/env python3
"""Static analyzer and indexer for verified harness tools and scripts.

Extracts docstrings, argument signatures, dependencies, and candidate safety
classes via static AST analysis (never executing the scripts). Enforces safety
reviews, detects file drift (via source SHA-256), and generates the scripts index.

CLI Contract (Spec §C, §9.9):
  tools/scripts_index.py --build     # scan tools/ and write config/scripts.json
  tools/scripts_index.py --check     # verify no script is missing, stale, or unclassified
  tools/scripts_index.py --json      # emit machine-readable scripts index
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
SCRIPTS_INDEX_PATH = REPO_ROOT / "config" / "scripts.json"

VALID_SAFETY_CLASSES = {
    "read_only",
    "read_write_local",
    "workspace_write",
    "network",
    "destructive",
}

NETWORK_MODULES = {"urllib", "requests", "httpx", "aiohttp", "socket", "http"}
DESTRUCTIVE_FUNCTIONS = {"rmtree", "unlink", "remove", "system", "taskkill"}
WRITE_MODULES = {"sqlite3", "open", "write_text", "write_bytes"}


def compute_sha256(path: Path) -> str:
    """Compute hex SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class ScriptASTAnalyzer(ast.NodeVisitor):
    """Statically inspects a Python script without importing or executing it."""

    def __init__(self, filename: str):
        self.filename = filename
        self.docstring: str = ""
        self.args: List[Dict[str, Any]] = []
        self.imports: Set[str] = set()
        self.calls: Set[str] = set()

    def analyze(self, source_code: str) -> None:
        tree = ast.parse(source_code, filename=self.filename)
        self.docstring = ast.get_docstring(tree) or ""
        self.visit(tree)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.add(alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add(node.module.split(".")[0])
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Detect function call names
        if isinstance(node.func, ast.Name):
            self.calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.add(node.func.attr)

        # Detect add_argument calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            arg_info = self._parse_add_argument(node)
            if arg_info:
                self.args.append(arg_info)

        self.generic_visit(node)

    def _parse_add_argument(self, node: ast.Call) -> Optional[Dict[str, Any]]:
        names = []
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.append(arg.value)

        if not names:
            return None

        # Prefer long option name
        primary_name = next((n for n in names if n.startswith("--")), names[0])
        arg_type = "string"
        default_val = None

        for kw in node.keywords:
            if kw.arg == "type":
                if isinstance(kw.value, ast.Name):
                    arg_type = kw.value.id
                elif isinstance(kw.value, ast.Attribute):
                    arg_type = kw.value.attr
            elif kw.arg == "action":
                if isinstance(kw.value, ast.Constant) and kw.value.value in ("store_true", "store_false"):
                    arg_type = "bool"
            elif kw.arg == "default":
                if isinstance(kw.value, ast.Constant):
                    default_val = kw.value.value

        return {
            "name": primary_name,
            "type": arg_type,
            "default": default_val,
        }

    def infer_safety(self) -> str:
        """Derive candidate safety classification from detected AST imports and calls."""
        if any(m in self.imports for m in NETWORK_MODULES):
            return "network"
        if any(c in self.calls for c in DESTRUCTIVE_FUNCTIONS):
            return "destructive"
        if any(m in self.imports for m in ("sqlite3", "shutil", "subprocess")) or any(
            c in self.calls for c in ("write_text", "write_bytes", "mkdir", "commit")
        ):
            return "workspace_write"
        return "read_only"


def analyze_script(path: Path, repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    """Parse and inspect a script statically."""
    content = path.read_text(encoding="utf-8", errors="replace")
    analyzer = ScriptASTAnalyzer(path.name)
    analyzer.analyze(content)

    summary = analyzer.docstring.strip().split("\n\n")[0].replace("\n", " ").strip()
    if not summary:
        summary = f"CLI tool {path.name}"

    try:
        rel_path = path.relative_to(repo_root).as_posix()
    except ValueError:
        rel_path = path.name
    script_id = path.stem.replace("_", "-")
    sha256 = compute_sha256(path)
    inferred_safety = analyzer.infer_safety()

    return {
        "id": script_id,
        "name": path.name,
        "path": rel_path,
        "summary": summary,
        "description": summary,
        "domain": "applied-sciences/engineering-and-technology",
        "tags": [script_id, "tool", "cli"],
        "invoke": {"argv": ["{python}", rel_path], "cwd": "repo"},
        "args": analyzer.args,
        "safety": inferred_safety,
        "requires": sorted(list(analyzer.imports)),
        "exit_codes": {"0": "ok", "2": "invalid input", "4": "verification failed"},
        "source_sha256": sha256,
        "status": "active",
    }


def load_existing_index(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load existing index keyed by script id/name."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            res = {}
            for item in data:
                key = item.get("id") or item.get("name") or Path(item.get("path", "")).name
                if key:
                    res[str(key)] = item
            return res
        elif isinstance(data, dict):
            return data.get("scripts", {})
    except Exception:
        return {}
    return {}


def scan_tools(tools_dir: Path) -> List[Path]:
    """Find all executable python CLI scripts in tools/ directory."""
    if not tools_dir.is_dir():
        return []
    scripts = []
    for p in sorted(tools_dir.glob("*.py")):
        if p.name.startswith("__"):
            continue
        scripts.append(p)
    return scripts


def build_index(tools_dir: Path = TOOLS_DIR, output_path: Path = SCRIPTS_INDEX_PATH) -> Tuple[List[Dict[str, Any]], int]:
    """Build or refresh scripts.json from tools/."""
    existing = load_existing_index(output_path)
    script_files = scan_tools(tools_dir)
    built_records = []
    updated_count = 0

    for script_file in script_files:
        analyzed = analyze_script(script_file)
        sid = analyzed["id"]
        sname = analyzed["name"]

        # Merge reviewed overrides if existing
        prev = existing.get(sid) or existing.get(sname)
        if prev:
            # Preserve human-reviewed metadata
            if prev.get("safety") in VALID_SAFETY_CLASSES:
                analyzed["safety"] = prev["safety"]
            if prev.get("domain"):
                analyzed["domain"] = prev["domain"]
            if prev.get("tags"):
                analyzed["tags"] = sorted(list(set(analyzed["tags"] + prev["tags"])))
            if prev.get("summary"):
                analyzed["summary"] = prev["summary"]
                analyzed["description"] = prev["summary"]
            if prev.get("exit_codes"):
                analyzed["exit_codes"] = prev["exit_codes"]

        built_records.append(analyzed)
        updated_count += 1

    # Write primary config/scripts.json
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(built_records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return built_records, updated_count


def check_index(tools_dir: Path = TOOLS_DIR, index_path: Path = SCRIPTS_INDEX_PATH) -> Tuple[bool, List[str]]:
    """Verify that all tools are registered, safety classes are valid, and hashes match."""
    errors: List[str] = []
    if not index_path.is_file():
        return False, [f"Missing scripts index file: {index_path}"]

    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        records = raw if isinstance(raw, list) else list(raw.get("scripts", {}).values())
    except Exception as exc:
        return False, [f"Malformed JSON in {index_path}: {exc}"]

    indexed_by_path = {r.get("path"): r for r in records if r.get("path")}
    indexed_by_name = {r.get("name"): r for r in records if r.get("name")}

    for script_file in scan_tools(tools_dir):
        try:
            rel_path = script_file.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel_path = script_file.name
        name = script_file.name
        record = indexed_by_path.get(rel_path) or indexed_by_name.get(name)

        if not record:
            errors.append(f"Unindexed tool: {rel_path} is not registered in {index_path.name}")
            continue

        safety = record.get("safety")
        if not safety or safety not in VALID_SAFETY_CLASSES:
            errors.append(f"Invalid or missing safety class '{safety}' for {rel_path}")

        current_hash = compute_sha256(script_file)
        stored_hash = record.get("source_sha256")
        if stored_hash and stored_hash != current_hash:
            errors.append(f"Stale script record for {rel_path}: SHA256 mismatch ({stored_hash[:8]} != {current_hash[:8]})")

    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Static analyzer and indexer for verified tools and scripts.")
    parser.add_argument("--build", action="store_true", help="Scan tools/ and regenerate config/scripts.json")
    parser.add_argument("--check", action="store_true", help="Verify index consistency, freshness, and safety classes")
    parser.add_argument("--json", action="store_true", help="Output machine-readable scripts index")
    parser.add_argument("--tools-dir", type=str, default=str(TOOLS_DIR), help="Path to tools directory")
    parser.add_argument("--out", type=str, default=str(SCRIPTS_INDEX_PATH), help="Path to output index file")

    args = parser.parse_args()
    tools_dir = Path(args.tools_dir).resolve()
    out_path = Path(args.out).resolve()

    if args.build:
        records, count = build_index(tools_dir, out_path)
        if args.json:
            print(json.dumps({"ok": True, "count": count, "scripts": records}, indent=2))
        else:
            print(f"[scripts_index] Built index with {count} verified tools -> {out_path}")
        return 0

    if args.check:
        ok, errors = check_index(tools_dir, out_path)
        if args.json:
            print(json.dumps({"ok": ok, "errors": errors}, indent=2))
        else:
            if ok:
                print(f"[scripts_index] OK: All {len(scan_tools(tools_dir))} tools verified and current.")
            else:
                print(f"[scripts_index] FAILED: {len(errors)} error(s) detected:")
                for err in errors:
                    print(f"  - {err}")
        return 0 if ok else 4

    # Default action: emit index JSON
    if out_path.is_file():
        print(out_path.read_text(encoding="utf-8"))
        return 0
    else:
        records, _ = build_index(tools_dir, out_path)
        print(json.dumps(records, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
