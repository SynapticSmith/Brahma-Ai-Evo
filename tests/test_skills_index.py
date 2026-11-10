"""Unit tests for tools/skills_index.py taxonomized skills indexer and cookbook engine."""
from pathlib import Path

from tools.skills_index import (
    build_skills_index,
    check_skills,
    format_small_model_recipe,
    parse_frontmatter,
    scan_skills,
)


def test_parse_frontmatter_extracts_fields():
    doc = """---
name: sample-skill
description: A sample skill for testing.
domain: cognitive
tags: [test, reasoning]
triggers:
  - "run sample"
---

# Sample Skill Content
"""
    fm, body = parse_frontmatter(doc)
    assert fm["name"] == "sample-skill"
    assert fm["description"] == "A sample skill for testing."
    assert fm["domain"] == "cognitive"
    assert "test" in fm["tags"]
    assert "run sample" in fm["triggers"]
    assert "Sample Skill Content" in body


def test_scan_and_build_skills_index(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skill1_dir = skills_dir / "my-test-skill"
    skill1_dir.mkdir(parents=True)

    skill_md = skill1_dir / "SKILL.md"
    skill_md.write_text(
        """---
name: my-test-skill
description: Test skill.
domain: architecture
tags: [architecture, test]
triggers:
  - "test trigger"
---

# Test
""",
        encoding="utf-8",
    )

    cookbook_md = skill1_dir / "cookbook.md"
    cookbook_md.write_text(
        """# Cookbook: My Test Skill

## Objective
Execute test.

## Execution Recipe
1. Run step 1.
2. Verify step 2.
""",
        encoding="utf-8",
    )

    records = scan_skills(skills_dir)
    assert len(records) == 1
    assert records[0]["name"] == "my-test-skill"
    assert records[0]["domain"] == "architecture"
    assert records[0]["has_dedicated_cookbook"] is True

    out_file = tmp_path / "config" / "skills_index.json"
    recs, count = build_skills_index(skills_dir=skills_dir, output_path=out_file)
    assert count == 1
    assert out_file.is_file()

    ok, errors = check_skills(skills_dir=skills_dir)
    assert ok is True
    assert len(errors) == 0

    recipe = format_small_model_recipe(records[0])
    assert "=== SKILL RECIPE: my-test-skill ===" in recipe
    assert "Run step 1." in recipe
