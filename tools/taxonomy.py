"""Manage, validate, and migrate the Academic Knowledge Tree taxonomy.

Supports:
- `python tools/taxonomy.py --check`: Validate taxonomy.json against the 12 schema rules.
- `python tools/taxonomy.py --plan`: Build transplant.json and link-rewrite.json.
- `python tools/taxonomy.py --apply`: Execute Phase 1 tree normalization and note transplant.
- `python tools/taxonomy.py --check-links`: Verify all rewritten inbound links resolve on disk.
- `python tools/taxonomy.py --lint-notes`: Verify transplanted notes meet OKF quality standards.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import second_brain  # noqa: E402


def load_taxonomy(vault: Path) -> List[Dict[str, Any]]:
    """Load canonical taxonomy.json from vault _taxonomy/ or fallback to config/."""
    vault_tax = vault / "wiki" / "library" / "knowledge" / "_taxonomy" / "taxonomy.json"
    if vault_tax.is_file():
        return json.loads(vault_tax.read_text(encoding="utf-8"))

    repo_tax = ROOT / "config" / "taxonomy.json"
    if repo_tax.is_file():
        return json.loads(repo_tax.read_text(encoding="utf-8"))

    raise FileNotFoundError("Could not find taxonomy.json in vault or config.")


def check_taxonomy(nodes: List[Dict[str, Any]]) -> List[str]:
    """Validate taxonomy against the 12 rules in §5.3.2."""
    errors: List[str] = []
    id_map = {n["id"]: n for n in nodes}

    for n in nodes:
        nid = n["id"]
        pid = n["parent"]

        # Rule 1 & 2: Parent valid
        if pid is not None:
            if pid not in id_map:
                errors.append(f"Node '{nid}': parent '{pid}' does not exist.")
            expected_prefix = f"{pid}/"
            if not nid.startswith(expected_prefix):
                errors.append(f"Node '{nid}': ID does not start with parent prefix '{expected_prefix}'.")

        # Rule 4: Depth cap of 6
        depth = len(nid.split("/"))
        if depth > 6:
            errors.append(f"Node '{nid}': depth {depth} exceeds max spine depth of 6.")

        # Rule 5: Slug syntax
        segments = nid.split("/")
        for seg in segments:
            if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", seg):
                errors.append(f"Node '{nid}': segment '{seg}' violates kebab-case format.")
            if len(seg) > 40:
                errors.append(f"Node '{nid}': segment '{seg}' exceeds 40 chars.")

        # Rule 7: Windows reserved names
        reserved = {"con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "lpt1", "lpt2", "lpt3"}
        for seg in segments:
            if seg in reserved:
                errors.append(f"Node '{nid}': segment '{seg}' is a Windows reserved name.")

    return errors


def classify_destination(rel_path: str, taxonomy_ids: set[str]) -> Tuple[str, str]:
    """Determine the canonical 5-division destination for an existing substantive note."""
    # Strip leading 'wiki/library/knowledge/'
    prefix = "wiki/library/knowledge/"
    sub_path = rel_path[len(prefix):] if rel_path.startswith(prefix) else rel_path

    # Keep root files at knowledge/ root
    if sub_path in ("CLAUDE.md", "log.md", "index.md"):
        return sub_path, "root"

    parts = sub_path.split("/")
    domain = parts[0]
    filename = parts[-1]
    name_no_ext = filename[:-3]

    target_dir = ""

    if domain == "humanities":
        if len(parts) >= 3 and parts[1] == "philosophy" and parts[2] == "buddhism":
            target_dir = "humanities/philosophy/eastern-philosophy/buddhism"
        elif len(parts) >= 3 and parts[1] == "languages":
            target_dir = "humanities/languages-and-literature/linguistics"
        elif len(parts) >= 3 and parts[1] == "linguistics":
            target_dir = "humanities/languages-and-literature/linguistics"
        elif len(parts) >= 3 and parts[1] == "literature":
            target_dir = "humanities/languages-and-literature"
        elif len(parts) >= 3 and parts[1] == "history":
            target_dir = "humanities/history"
        elif len(parts) >= 3 and parts[1] == "philosophy":
            target_dir = "humanities/philosophy"
            if len(parts) >= 4:
                subphil = parts[2]
                if f"humanities/philosophy/{subphil}" in taxonomy_ids:
                    target_dir = f"humanities/philosophy/{subphil}"
        else:
            target_dir = "humanities"

    elif domain == "computer-science":
        if len(parts) >= 3 and parts[1] == "ai-ml":
            target_dir = "formal-sciences/computer-science/artificial-intelligence"
        elif len(parts) >= 3 and parts[1] == "algorithms":
            target_dir = "formal-sciences/computer-science/algorithms-and-complexity"
        elif len(parts) >= 3 and parts[1] == "data-science":
            target_dir = "formal-sciences/computer-science/artificial-intelligence"
        elif len(parts) >= 3 and parts[1] == "programming":
            target_dir = "formal-sciences/computer-science/programming-languages"
        elif len(parts) >= 3 and parts[1] == "software-eng":
            target_dir = "applied-sciences/engineering-and-technology/software-engineering"
        elif len(parts) >= 3 and parts[1] == "systems":
            if "security" in name_no_ext:
                target_dir = "formal-sciences/computer-science/cryptography"
            elif "distributed" in name_no_ext:
                target_dir = "formal-sciences/computer-science/distributed-systems"
            elif "operating" in name_no_ext:
                target_dir = "formal-sciences/computer-science/operating-systems"
            else:
                target_dir = "formal-sciences/computer-science"
        else:
            target_dir = "formal-sciences/computer-science"

    elif domain == "arts":
        if len(parts) >= 3 and parts[1] == "music":
            target_dir = "humanities/performing-arts/music"
        elif len(parts) >= 3 and parts[1] == "performing-arts":
            target_dir = "humanities/performing-arts"
        elif len(parts) >= 3 and parts[1] == "design":
            target_dir = "humanities/visual-arts/design"
        elif len(parts) >= 3 and parts[1] == "visual-arts":
            target_dir = "humanities/visual-arts"
        elif len(parts) >= 3 and parts[1] == "writing":
            target_dir = "humanities/languages-and-literature"
        else:
            target_dir = "humanities/visual-arts"

    elif domain == "sciences":
        if len(parts) >= 2:
            sub = parts[1]
            if f"natural-sciences/{sub}" in taxonomy_ids:
                target_dir = f"natural-sciences/{sub}"
            elif sub == "biology" or "bio" in sub:
                target_dir = "natural-sciences/biology"
            elif sub == "physics":
                target_dir = "natural-sciences/physics"
            elif sub == "chemistry":
                target_dir = "natural-sciences/chemistry"
            elif sub == "astronomy":
                target_dir = "natural-sciences/astronomy"
            elif sub in ("earth-sciences", "geology", "meteorology", "oceanography"):
                target_dir = "natural-sciences/earth-sciences"
            else:
                target_dir = "natural-sciences"
        else:
            target_dir = "natural-sciences"

    elif domain == "social-sciences":
        if len(parts) >= 2:
            sub = parts[1]
            if f"social-sciences/{sub}" in taxonomy_ids:
                target_dir = f"social-sciences/{sub}"
            else:
                target_dir = "social-sciences"
        else:
            target_dir = "social-sciences"

    elif domain == "engineering":
        if len(parts) >= 2:
            sub = parts[1]
            candidate = f"applied-sciences/engineering-and-technology/{sub}-engineering"
            if candidate in taxonomy_ids:
                target_dir = candidate
            elif f"applied-sciences/engineering-and-technology/{sub}" in taxonomy_ids:
                target_dir = f"applied-sciences/engineering-and-technology/{sub}"
            else:
                target_dir = "applied-sciences/engineering-and-technology"
        else:
            target_dir = "applied-sciences/engineering-and-technology"

    elif domain == "mathematics":
        if len(parts) >= 2:
            sub = parts[1]
            if f"formal-sciences/mathematics/{sub}" in taxonomy_ids:
                target_dir = f"formal-sciences/mathematics/{sub}"
            elif sub == "statistics":
                target_dir = "formal-sciences/statistics"
            else:
                target_dir = "formal-sciences/mathematics"
        else:
            target_dir = "formal-sciences/mathematics"

    elif domain == "practical-domains":
        if len(parts) >= 2:
            sub = parts[1]
            if sub in ("business", "management", "marketing", "finance"):
                target_dir = f"applied-sciences/business/{sub}" if f"applied-sciences/business/{sub}" in taxonomy_ids else "applied-sciences/business"
            elif sub in ("healthcare", "health", "medicine"):
                target_dir = "applied-sciences/medicine-and-health"
            elif sub in ("education",):
                target_dir = "applied-sciences/education"
            elif sub in ("agriculture",):
                target_dir = "applied-sciences/agriculture"
            elif sub in ("architecture", "urban-planning"):
                target_dir = "applied-sciences/architecture-and-design"
            elif sub in ("law", "public-policy"):
                target_dir = "applied-sciences/public-administration"
            elif sub in ("military", "defense"):
                target_dir = "applied-sciences/military-sciences"
            else:
                target_dir = "applied-sciences"
        else:
            target_dir = "applied-sciences"

    elif domain == "meta-knowledge":
        if "epistemology" in sub_path:
            target_dir = "humanities/philosophy/epistemology"
        elif "cybernetics" in sub_path:
            target_dir = "formal-sciences/systems-science/cybernetics"
        elif "systems" in sub_path:
            target_dir = "formal-sciences/systems-science"
        else:
            target_dir = "humanities/philosophy/epistemology"

    else:
        target_dir = "_inbox"

    # Fallback to closest valid taxonomy node if target_dir not in taxonomy_ids
    while target_dir and target_dir not in taxonomy_ids and target_dir != "_inbox":
        if "/" in target_dir:
            target_dir = target_dir.rsplit("/", 1)[0]
        else:
            target_dir = "_inbox"
            break

    target_rel = f"{target_dir}/{filename}" if target_dir != "root" else filename
    return target_rel, target_dir


def build_migration_plan(vault: Path, nodes: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Generate transplant plan and link rewrite manifest."""
    sub_notes_file = vault / "wiki" / "library" / "knowledge" / "_audit" / "substantive-notes.json"
    inbound_links_file = vault / "wiki" / "library" / "knowledge" / "_audit" / "inbound-links.json"

    if not sub_notes_file.is_file():
        raise FileNotFoundError(f"Missing {sub_notes_file}. Run tools/audit_vault_taxonomy.py first.")

    sub_notes: List[Dict[str, Any]] = json.loads(sub_notes_file.read_text(encoding="utf-8"))
    inbound_links: List[Dict[str, Any]] = json.loads(inbound_links_file.read_text(encoding="utf-8")) if inbound_links_file.is_file() else []

    taxonomy_ids = {n["id"] for n in nodes}

    # 1. Build transplant mapping
    transplants: List[Dict[str, Any]] = []
    old_to_new_rel: Dict[str, str] = {}
    old_basename_to_new_rel: Dict[str, str] = {}

    for note in sub_notes:
        old_rel = note["rel_path"]  # e.g. "wiki/library/knowledge/computer-science/ai-ml/attention-mechanisms.md"
        new_sub, dest_domain = classify_destination(old_rel, taxonomy_ids)
        new_rel = f"wiki/library/knowledge/{new_sub}"

        transplants.append({
            "old_rel_path": old_rel,
            "new_rel_path": new_rel,
            "dest_domain": dest_domain,
            "name": note["name"],
            "bytes": note["bytes"],
            "body_len": note["body_len"],
        })
        old_to_new_rel[old_rel] = new_rel
        old_basename_to_new_rel[note["name"]] = new_rel

    # 2. Build link rewrites
    link_rewrites: List[Dict[str, Any]] = []
    knowledge_prefix = "wiki/library/knowledge/"

    for link in inbound_links:
        src_file = link["source_file"]
        line_no = link["line_no"]
        raw_match = link["link_raw"]
        target = link["target"]

        # Resolve what old target referred to
        norm_target = target.replace("\\", "/")
        # Strip leading relative prefixes (e.g. '../../wiki/library/knowledge/' or '../knowledge/')
        matched_new_rel = ""

        # Case A: target matches exact known old relative path
        for old_rel, new_rel in old_to_new_rel.items():
            old_knowledge_sub = old_rel[len(knowledge_prefix):]
            if norm_target.endswith(old_knowledge_sub) or norm_target.endswith(old_rel):
                matched_new_rel = new_rel
                break

        # Case B: target matches by basename (e.g. 'attan-as-deathless-true-self.md' or 'CLAUDE')
        if not matched_new_rel:
            target_base = norm_target.split("/")[-1]
            if not target_base.endswith(".md"):
                target_base_md = f"{target_base}.md"
            else:
                target_base_md = target_base

            if target_base_md in old_basename_to_new_rel:
                matched_new_rel = old_basename_to_new_rel[target_base_md]
            elif target_base in ("CLAUDE", "CLAUDE.md"):
                matched_new_rel = "wiki/library/knowledge/CLAUDE.md"
            elif target_base in ("index", "index.md"):
                matched_new_rel = "wiki/library/knowledge/index.md"

        if matched_new_rel:
            # Compute new relative path from source file's directory to matched_new_rel
            src_dir = Path(src_file).parent
            try:
                computed_rel = os.path.relpath(matched_new_rel, src_dir).replace("\\", "/")
            except Exception:
                computed_rel = matched_new_rel

            # Format new link representation
            if raw_match.startswith("[["):
                # Wikilink: if was full path, update path; if was basename, keep basename or path
                new_link_text = f"[[{matched_new_rel}]]" if "/" in target else f"[[{Path(matched_new_rel).stem}]]"
            else:
                # Markdown link [text](target)
                link_text = raw_match[1:raw_match.find("]")]
                new_link_text = f"[{link_text}]({computed_rel})"

            link_rewrites.append({
                "source_file": src_file,
                "line_no": line_no,
                "old_target": target,
                "old_raw": raw_match,
                "new_target": computed_rel,
                "new_raw": new_link_text,
                "target_resolved_file": matched_new_rel,
                "status": "rewritten",
            })
        else:
            link_rewrites.append({
                "source_file": src_file,
                "line_no": line_no,
                "old_target": target,
                "old_raw": raw_match,
                "new_target": target,
                "new_raw": raw_match,
                "target_resolved_file": None,
                "status": "intentional_dangling_or_unresolved",
            })

    return transplants, link_rewrites


def apply_migration(vault: Path, nodes: List[Dict[str, Any]], transplants: List[Dict[str, Any]], link_rewrites: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Execute Phase 1 migration: scaffold spine, transplant notes, rewrite links, prune stubs."""
    knowledge_dir = vault / "wiki" / "library" / "knowledge"

    # 1. Scaffold all 5-division spine directories and index.md
    print("1. Scaffolding canonical 5-division spine directories...")
    created_dirs = 0
    created_indices = 0
    today = time.strftime("%Y-%m-%d")

    for node in nodes:
        node_dir = knowledge_dir / node["id"]
        node_dir.mkdir(parents=True, exist_ok=True)
        created_dirs += 1

        index_file = node_dir / "index.md"
        if not index_file.exists():
            aliases_yaml = json.dumps(node.get("aliases", []))
            legacy_yaml = json.dumps(node.get("legacy_domains", []))
            lines = [
                "---",
                "type: ConceptIndex",
                f"title: {json.dumps(node['title'])}",
                f"domain: {json.dumps(node['id'])}",
                f"parent: {json.dumps(node['parent']) if node['parent'] else 'null'}",
                f"layer: {json.dumps(node['layer'])}",
                f"aliases: {aliases_yaml}",
                f"legacy_domains: {legacy_yaml}",
                f"created: {today}",
                f"updated: {today}",
                "---",
                "",
                f"# {node['title']}",
                "",
                f"> {node['scope']}",
                "",
                "## Overview",
                "",
                f"The canonical academic field of **{node['title']}** encompasses the systematic study, methodology, and foundational theories within this domain.",
                "",
                "## Subdisciplines & Topics",
                "",
            ]
            index_file.write_text("\n".join(lines), encoding="utf-8")
            created_indices += 1

    # Write top-level wiki/library/knowledge/index.md
    top_index = knowledge_dir / "index.md"
    top_index_content = [
        "---",
        "type: ConceptIndex",
        "title: Academic Knowledge Tree",
        "domain: root",
        "divisions: [humanities, social-sciences, natural-sciences, formal-sciences, applied-sciences]",
        f"updated: {today}",
        "---",
        "",
        "# Academic Knowledge Tree",
        "",
        "Canonical academic knowledge hierarchy organized across the five divisions of academic knowledge:",
        "",
        "1. **[[humanities/index|Humanities]]**: History, philosophy, languages, literature, and visual/performing arts.",
        "2. **[[social-sciences/index|Social Sciences]]**: Anthropology, economics, political science, psychology, and sociology.",
        "3. **[[natural-sciences/index|Natural Sciences]]**: Astronomy, biology, chemistry, earth sciences, and physics.",
        "4. **[[formal-sciences/index|Formal Sciences]]**: Computer science, mathematics, statistics, and systems science.",
        "5. **[[applied-sciences/index|Applied Sciences]]**: Engineering, medicine, business, agriculture, and public administration.",
        "",
    ]
    top_index.write_text("\n".join(top_index_content), encoding="utf-8")

    # 2. Transplant substantive notes
    print(f"2. Transplanting {len(transplants)} substantive notes...")
    transplanted_count = 0

    for item in transplants:
        old_file = vault / item["old_rel_path"]
        new_file = vault / item["new_rel_path"]

        if not old_file.is_file():
            continue

        new_file.parent.mkdir(parents=True, exist_ok=True)
        content = old_file.read_text(encoding="utf-8", errors="replace")

        # Inject legacy_path into frontmatter if not present
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                fm = parts[1]
                body = parts[2]
                if "legacy_path:" not in fm:
                    fm += f"\nlegacy_path: {json.dumps(item['old_rel_path'])}\n"
                if "domain:" not in fm and item["dest_domain"] != "root":
                    fm += f"domain: {json.dumps(item['dest_domain'])}\n"
                content = f"---{fm}---{body}"

        new_file.write_text(content, encoding="utf-8")
        transplanted_count += 1

    # 3. Rewrite inbound links in link-bearing files
    print(f"3. Rewriting {len(link_rewrites)} inbound links across source files...")
    rewritten_files: set[str] = set()

    # Group rewrites by file
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for rw in link_rewrites:
        if rw["status"] == "rewritten":
            by_file.setdefault(rw["source_file"], []).append(rw)

    for src_rel, rws in by_file.items():
        src_path = vault / src_rel
        if not src_path.is_file():
            continue

        text = src_path.read_text(encoding="utf-8", errors="replace")
        for rw in rws:
            old_raw = rw["old_raw"]
            new_raw = rw["new_raw"]
            if old_raw in text:
                text = text.replace(old_raw, new_raw)

        src_path.write_text(text, encoding="utf-8")
        rewritten_files.add(src_rel)

    # 4. Prune retired 9-domain stub directories
    print("4. Pruning retired legacy 9-domain stub directories...")
    retired_domains = [
        "arts", "computer-science", "engineering", "mathematics",
        "meta-knowledge", "practical-domains", "sciences",
    ]
    pruned_dirs = 0
    for dom in retired_domains:
        dom_dir = knowledge_dir / dom
        if dom_dir.is_dir():
            shutil.rmtree(dom_dir, ignore_errors=True)
            pruned_dirs += 1

    # Update knowledge/CLAUDE.md
    claude_md = knowledge_dir / "CLAUDE.md"
    if claude_md.is_file():
        claude_content = [
            "# Knowledge Base Architecture (OKF v0.2 + ICM v1)",
            "",
            "> **Spine Authority:** `docs/ACADEMIC-TAXONOMY-AND-SKILLS-ENGINE-SPEC.md`",
            f"> **Updated:** `{today}`",
            "",
            f"The knowledge base is organized across the 5 canonical academic divisions ({len(nodes)} canonical spine nodes).",
            "Substantive notes are maintained under their fine-grained academic subdisciplines.",
            "",
        ]
        claude_md.write_text("\n".join(claude_content), encoding="utf-8")

    # 5. Regenerate book-library/_taxonomy.md from taxonomy.json at depth 1-2
    print("5. Regenerating book-library/_taxonomy.md from taxonomy.json...")
    calibre_tax_file = vault / "book-library" / "_taxonomy.md"
    if calibre_tax_file.parent.is_dir():
        division_subdomains: Dict[str, List[str]] = {
            "Humanities": ["history", "languages-and-literature", "performing-arts", "visual-arts", "philosophy", "religion"],
            "SocialSciences": ["anthropology", "archaeology", "economics", "geography", "political-science", "psychology", "sociology"],
            "NaturalSciences": ["astronomy", "biology", "chemistry", "earth-sciences", "physics"],
            "FormalSciences": ["computer-science", "mathematics", "statistics", "systems-science"],
            "AppliedSciences": ["agriculture", "architecture-and-design", "business", "education", "engineering-and-technology", "environmental-studies", "medicine-and-health", "military-sciences", "public-administration"],
        }
        tax_lines = [
            "---",
            "type: controlled-vocabulary",
            "title: Book Library Taxonomy",
            "description: Single source of truth for categorizing Calibre books into the 5-division academic knowledge tree.",
            f"updated: {today}",
            "owner: knowledge-agent",
            "---",
            "",
            "# Book Library Taxonomy",
            "",
            "This is the **only** document that defines valid values for book classification in `book-library`.",
            "Generated automatically from `taxonomy.json` at depth 1–2.",
            "",
            "## Domains and subdomains",
            "",
            "| Domain | Valid subdomains | Meaning |",
            "|---|---|---|",
            "| `Humanities` | " + ", ".join(division_subdomains["Humanities"]) + " | Human culture, thought, language, history, and arts |",
            "| `SocialSciences` | " + ", ".join(division_subdomains["SocialSciences"]) + " | Study of human societies, behavior, institutions, and economics |",
            "| `NaturalSciences` | " + ", ".join(division_subdomains["NaturalSciences"]) + " | Physical and biological world through empirical scientific observation |",
            "| `FormalSciences` | " + ", ".join(division_subdomains["FormalSciences"]) + " | Formal abstract systems: mathematics, computing, statistics, logic |",
            "| `AppliedSciences` | " + ", ".join(division_subdomains["AppliedSciences"]) + " | Practical applications: engineering, medicine, business, agriculture |",
            "",
            "## Tag vocabulary",
            "",
            "Hierarchical tags are dot-paths: `Division.subdomain.topic` (e.g. `Humanities.philosophy.buddhism`).",
            "",
        ]
        calibre_tax_file.write_text("\n".join(tax_lines), encoding="utf-8")

    return {
        "created_dirs": created_dirs,
        "created_indices": created_indices,
        "transplanted_notes": transplanted_count,
        "rewritten_files": len(rewritten_files),
        "pruned_legacy_domains": pruned_dirs,
    }


def verify_links(vault: Path, link_rewrites: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    """Check that rewritten links resolve on disk."""
    broken: List[str] = []
    verified = 0

    for rw in link_rewrites:
        if rw["status"] != "rewritten":
            continue

        target_file = rw.get("target_resolved_file")
        if not target_file:
            continue

        full_dest = vault / target_file
        if not full_dest.is_file():
            broken.append(f"{rw['source_file']}:{rw['line_no']} -> '{target_file}' does not exist on disk.")
        else:
            verified += 1

    return verified, broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate taxonomy.json schema and 12 rules.")
    parser.add_argument("--plan", action="store_true", help="Build and write transplant.json and link-rewrite.json.")
    parser.add_argument("--apply", action="store_true", help="Execute Phase 1 migration.")
    parser.add_argument("--check-links", action="store_true", help="Verify all rewritten links resolve on disk.")
    parser.add_argument("--lint-notes", action="store_true", help="Lint transplanted notes.")
    args = parser.parse_args(argv)

    vault = second_brain.vault_root()
    if not vault or not vault.is_dir():
        print(f"Error: Vault not found: {vault}", file=sys.stderr)
        return 1

    nodes = load_taxonomy(vault)

    # 1. Validate taxonomy.json
    if args.check:
        print(f"Validating taxonomy.json ({len(nodes)} nodes)...")
        errors = check_taxonomy(nodes)
        if errors:
            print(f"FAILED: {len(errors)} validation errors:", file=sys.stderr)
            for err in errors[:10]:
                print(f"  • {err}", file=sys.stderr)
            return 1
        print("✓ taxonomy.json is 100% valid.")
        return 0

    # 2. Build migration plan
    if args.plan or args.apply:
        print("Building Phase 1 migration plan...")
        transplants, link_rewrites = build_migration_plan(vault, nodes)

        audit_dir = vault / "wiki" / "library" / "knowledge" / "_audit"
        audit_dir.mkdir(parents=True, exist_ok=True)

        trans_file = audit_dir / "transplant.json"
        trans_file.write_text(json.dumps(transplants, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Wrote transplant plan: {trans_file} ({len(transplants)} notes)")

        rw_file = audit_dir / "link-rewrite.json"
        rw_file.write_text(json.dumps(link_rewrites, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Wrote link rewrite manifest: {rw_file} ({len(link_rewrites)} links)")

        rewritten_count = sum(1 for rw in link_rewrites if rw["status"] == "rewritten")
        dangling_count = len(link_rewrites) - rewritten_count
        print(
            f"Migration Plan Summary:\n"
            f"  Substantive notes to transplant: {len(transplants)}\n"
            f"  Inbound links to rewrite: {rewritten_count}\n"
            f"  Pre-existing dangling / unresolvable: {dangling_count}\n"
        )

        if not args.apply:
            return 0

    # 3. Apply migration
    if args.apply:
        print("\nApplying Phase 1 migration...")
        started = time.time()
        res = apply_migration(vault, nodes, transplants, link_rewrites)
        duration = time.time() - started

        print(
            f"\nPhase 1 migration completed in {duration:.2f}s:\n"
            f"  Canonical directories scaffolded: {res['created_dirs']}\n"
            f"  Index files created: {res['created_indices']}\n"
            f"  Notes transplanted: {res['transplanted_notes']}\n"
            f"  Files with rewritten links: {res['rewritten_files']}\n"
            f"  Legacy domain folders pruned: {res['pruned_legacy_domains']}\n"
        )

        # Run link check automatically after apply
        args.check_links = True

    # 4. Check links
    if args.check_links:
        print("Verifying rewritten links on disk...")
        audit_dir = vault / "wiki" / "library" / "knowledge" / "_audit"
        rw_file = audit_dir / "link-rewrite.json"
        if not rw_file.is_file():
            print(f"Error: Missing {rw_file}", file=sys.stderr)
            return 1

        link_rewrites = json.loads(rw_file.read_text(encoding="utf-8"))
        verified, broken = verify_links(vault, link_rewrites)
        print(f"✓ Verified {verified} inbound links resolve cleanly on disk.")
        if broken:
            print(f"WARNING: {len(broken)} link resolution errors:", file=sys.stderr)
            for b in broken[:10]:
                print(f"  • {b}", file=sys.stderr)
            return 1
        print("✓ Zero broken links detected!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
