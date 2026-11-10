#!/usr/bin/env python3
"""Taxonomized Skills Indexer and OpenMonoAgent Cookbook Engine.

Scans the skills/ library, validates YAML frontmatter, extracts progressive
disclosure layers (L0 metadata -> L1 triggers -> L2 executable cookbooks -> L3 SOP),
and compiles config/skills_index.json for efficient small-model (e.g. lfm3:1.7b) JIT injection.

CLI Contract:
  tools/skills_index.py --build               # Compile config/skills_index.json
  tools/skills_index.py --check               # Verify skill frontmatter, domains, & cookbooks
  tools/skills_index.py --domain <domain>     # List skills belonging to a taxonomy domain
  tools/skills_index.py --cookbook <skill>    # Emit small-model executable recipe
  tools/skills_index.py --json                # Machine-readable catalogue
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
SKILLS_INDEX_PATH = REPO_ROOT / "config" / "skills_index.json"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

CANONICAL_DOMAINS = {
    "cognitive",
    "architecture",
    "coding",
    "devops",
    "security",
    "research",
    "multimodal",
    "methodology",
}


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML-like frontmatter from markdown without third-party dependencies."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    fm_raw = match.group(1)
    body = text[match.end():]
    data: Dict[str, Any] = {}

    current_key: Optional[str] = None
    current_list: Optional[List[str]] = None

    for line in fm_raw.splitlines():
        line_strip = line.strip()
        if not line_strip or line_strip.startswith("#"):
            continue

        # Sub-list item: "  - value"
        if line_strip.startswith("- ") and current_key:
            val = line_strip[2:].strip().strip("\"'")
            if current_list is None:
                current_list = []
                data[current_key] = current_list
            current_list.append(val)
            continue

        # Key-value: "key: value"
        if ":" in line:
            parts = line.split(":", 1)
            key = parts[0].strip()
            val = parts[1].strip()

            current_key = key
            current_list = None

            if val == "":
                data[key] = {}
            elif val.startswith("[") and val.endswith("]"):
                # Inline array: [a, b, c]
                items = [x.strip().strip("\"'") for x in val[1:-1].split(",") if x.strip()]
                data[key] = items
            else:
                data[key] = val.strip("\"'")

    return data, body


def extract_cookbook(skill_dir: Path, skill_body: str) -> str:
    """Extract or load the executable cookbook recipe for small models."""
    cookbook_file = skill_dir / "cookbook.md"
    if cookbook_file.is_file():
        return cookbook_file.read_text(encoding="utf-8").strip()

    # Fall back to extracting ## Procedure or ## Cookbook section from SKILL.md
    lines = skill_body.splitlines()
    in_section = False
    extracted_lines = []

    for line in lines:
        if line.startswith("## ") and any(term in line.lower() for term in ("procedure", "cookbook", "recipe", "workflow")):
            in_section = True
            extracted_lines.append(line)
            continue
        elif in_section and line.startswith("## ") and not any(term in line.lower() for term in ("procedure", "cookbook", "recipe", "workflow")):
            break
        elif in_section:
            extracted_lines.append(line)

    if extracted_lines:
        return "\n".join(extracted_lines).strip()

    return "No cookbook recipe provided."


def scan_skills(skills_dir: Path = SKILLS_DIR) -> List[Dict[str, Any]]:
    """Scan all skill directories and parse metadata and cookbooks."""
    if not skills_dir.is_dir():
        return []

    records = []
    for skill_path in sorted(skills_dir.glob("**/SKILL.md")):
        skill_dir = skill_path.parent
        content = skill_path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(content)

        name = fm.get("name") or skill_dir.name
        desc = fm.get("description") or ""
        metadata = fm.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = {}

        tags = fm.get("tags") or metadata.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]

        raw_domain = fm.get("domain") or metadata.get("domain") or "coding"
        # Determine canonical domain category
        domain_tag = "coding"
        domain_str = f"{raw_domain} {' '.join(tags)} {name}".lower()
        if any(term in domain_str for term in ("cognitive", "reasoning", "mental-model", "thinking")):
            domain_tag = "cognitive"
        elif any(term in domain_str for term in ("research", "scrape", "scraper", "book", "extract", "knowledge")):
            domain_tag = "research"
        elif any(term in domain_str for term in ("architecture", "scaffold", "hierarchy", "filesystem")):
            domain_tag = "architecture"
        elif any(term in domain_str for term in ("security", "vuln", "audit", "secret")):
            domain_tag = "security"
        elif any(term in domain_str for term in ("devops", "docker", "github", "workflow", "git", "ci")):
            domain_tag = "devops"

        triggers = fm.get("triggers") or []
        if isinstance(triggers, str):
            triggers = [triggers]

        cookbook = extract_cookbook(skill_dir, body)
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        try:
            rel_path = skill_path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel_path = skill_path.name

        records.append({
            "name": name,
            "directory": skill_dir.name,
            "path": rel_path,
            "domain": domain_tag,
            "academic_domain": str(raw_domain),
            "description": desc,
            "triggers": triggers,
            "tags": tags,
            "has_dedicated_cookbook": (skill_dir / "cookbook.md").is_file(),
            "cookbook": cookbook,
            "sha256": sha256,
        })

    return records


def build_skills_index(skills_dir: Path = SKILLS_DIR, output_path: Path = SKILLS_INDEX_PATH) -> Tuple[List[Dict[str, Any]], int]:
    """Build and save config/skills_index.json."""
    records = scan_skills(skills_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    catalog = {
        "version": "1.0",
        "total_skills": len(records),
        "domains": sorted(list(set(r["domain"] for r in records))),
        "skills": records,
    }

    output_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return records, len(records)


def check_skills(skills_dir: Path = SKILLS_DIR) -> Tuple[bool, List[str]]:
    """Validate skill completeness, valid YAML frontmatter, and non-empty cookbooks."""
    errors = []
    records = scan_skills(skills_dir)

    if not records:
        return False, ["No skills found in skills directory."]

    seen_names = set()
    for r in records:
        name = r["name"]
        if name in seen_names:
            errors.append(f"Duplicate skill name detected: {name}")
        seen_names.add(name)

        if not r["description"]:
            errors.append(f"Skill '{name}' is missing a description.")
        if not r["triggers"]:
            errors.append(f"Skill '{name}' has no triggers defined.")
        if r["cookbook"] == "No cookbook recipe provided.":
            errors.append(f"Skill '{name}' is missing an actionable cookbook or procedure section.")

    return len(errors) == 0, errors


def format_small_model_recipe(skill_record: Dict[str, Any]) -> str:
    """Format an executable cookbook recipe specifically for lfm3:1.7b context injection."""
    lines = [
        f"=== SKILL RECIPE: {skill_record['name']} ===",
        f"Domain: {skill_record['domain']}",
        f"Summary: {skill_record['description']}",
        "",
        "--- EXECUTABLE PROCEDURE (FOLLOW SEQUENTIALLY) ---",
        skill_record["cookbook"],
        "================================================",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Taxonomized Skills Indexer and OpenMonoAgent Cookbook Engine.")
    parser.add_argument("--build", action="store_true", help="Compile config/skills_index.json")
    parser.add_argument("--check", action="store_true", help="Validate all skills and cookbooks")
    parser.add_argument("--domain", type=str, default=None, help="Filter skills by domain category")
    parser.add_argument("--cookbook", type=str, default=None, help="Emit small-model cookbook for a specific skill")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON catalogue")
    parser.add_argument("--skills-dir", type=str, default=str(SKILLS_DIR), help="Path to skills directory")
    parser.add_argument("--out", type=str, default=str(SKILLS_INDEX_PATH), help="Path to output skills index")

    args = parser.parse_args()
    skills_dir = Path(args.skills_dir).resolve()
    out_path = Path(args.out).resolve()

    if args.build:
        records, count = build_skills_index(skills_dir, out_path)
        if args.json:
            print(json.dumps({"ok": True, "count": count, "skills": records}, indent=2))
        else:
            print(f"[skills_index] Built index with {count} skills across {len(set(r['domain'] for r in records))} domains -> {out_path}")
        return 0

    if args.check:
        ok, errors = check_skills(skills_dir)
        if args.json:
            print(json.dumps({"ok": ok, "errors": errors}, indent=2))
        else:
            if ok:
                print(f"[skills_index] OK: All {len(scan_skills(skills_dir))} skills validated with actionable recipes.")
            else:
                print(f"[skills_index] FAILED: {len(errors)} error(s) detected:")
                for err in errors:
                    print(f"  - {err}")
        return 0 if ok else 4

    if args.cookbook:
        records = scan_skills(skills_dir)
        match = next((r for r in records if r["name"] == args.cookbook or r["directory"] == args.cookbook), None)
        if not match:
            print(f"Error: Skill '{args.cookbook}' not found.", file=sys.stderr)
            return 2
        print(format_small_model_recipe(match))
        return 0

    if args.domain:
        records = scan_skills(skills_dir)
        matched = [r for r in records if r["domain"].lower() == args.domain.lower()]
        if args.json:
            print(json.dumps(matched, indent=2))
        else:
            print(f"Skills in domain '{args.domain}' ({len(matched)}):")
            for r in matched:
                print(f"  - {r['name']}: {r['description']}")
        return 0

    # Default action
    if out_path.is_file():
        print(out_path.read_text(encoding="utf-8"))
        return 0
    else:
        records, _ = build_skills_index(skills_dir, out_path)
        print(json.dumps(records, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
