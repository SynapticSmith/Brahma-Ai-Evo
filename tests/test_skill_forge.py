from unittest.mock import patch

from core.dynamic_registry import DynamicToolRegistry
from core.skill_crucible import SkillCrucible
from core.skill_forge import SkillForge


def test_forged_feature_is_staged_not_activated(tmp_path):
    original_skills = DynamicToolRegistry._skills.copy()
    original_initialized = DynamicToolRegistry._initialized
    payload = {
        "success": True,
        "manifest": {
            "name": "forge_smoke_test",
            "description": "Forge smoke test",
            "parameters": {"type": "OBJECT", "properties": {}},
            "active": True,
        },
        "code": 'def execute(**kwargs):\n    return "forge smoke test passed"\n',
        "test_cases": [{"input": {}}],
    }
    staged_root = tmp_path / "staged"

    try:
        with (
            patch("core.skill_forge.staged_skills_dir", return_value=staged_root),
            patch.object(SkillForge, "_call_llm_synthesizer", return_value=payload),
            patch.object(SkillCrucible, "resolve_dependencies", return_value=(True, "No dependencies.")),
        ):
            result = SkillForge.forge_skill("smoke test feature", "forge_smoke_test")

            assert result["success"] is True, result
            assert result["staged"] is True
            assert result["activated"] is False
            assert "reviewed" in result["message"]

            staged_dir = staged_root / "forge_smoke_test"
            assert (staged_dir / "skill.py").is_file()
            assert (staged_dir / "manifest.json").is_file()
            assert (staged_dir / "test_cases.json").is_file()
            assert (staged_dir / "staged.json").is_file()

            # Staging must not import or register the generated code.
            assert DynamicToolRegistry.has_tool("forge_smoke_test") is False
    finally:
        DynamicToolRegistry._skills = original_skills
        DynamicToolRegistry._initialized = original_initialized


def test_crucible_detects_error_dictionary_in_sandbox():
    broken_code = 'def execute(**kwargs):\n    return {"error": "SSL handshake failed"}\n'
    ok, msg, telemetry = SkillCrucible.run_sandbox_test(broken_code, [{"input": {}}])
    assert ok is False
    assert "SSL handshake failed" in msg

    working_code = 'def execute(**kwargs):\n    return {"title": "Success", "summary": "Rendered"}\n'
    ok2, msg2, telemetry2 = SkillCrucible.run_sandbox_test(working_code, [{"input": {}}])
    assert ok2 is True


def test_forge_repairs_skill_when_sandbox_returns_error_dict(tmp_path):
    original_skills = DynamicToolRegistry._skills.copy()
    original_initialized = DynamicToolRegistry._initialized

    initial_payload = {
        "success": True,
        "manifest": {
            "name": "repair_smoke_test",
            "description": "Repair test",
            "parameters": {"type": "OBJECT", "properties": {}},
            "active": True,
        },
        "code": 'def execute(**kwargs):\n    return {"error": "Initial SSL failure"}\n',
        "test_cases": [{"input": {}}],
    }

    repaired_code = 'def execute(**kwargs):\n    return {"title": "Repaired Deliverable", "summary": "Working"}\n'
    staged_root = tmp_path / "staged"

    try:
        with (
            patch("core.skill_forge.staged_skills_dir", return_value=staged_root),
            patch.object(SkillForge, "_call_llm_synthesizer", return_value=initial_payload),
            patch.object(SkillCrucible, "resolve_dependencies", return_value=(True, "No dependencies.")),
            patch.object(SkillForge, "_repair_code", return_value={"success": True, "code": repaired_code}) as mock_repair,
        ):
            result = SkillForge.forge_skill("repair smoke test", "repair_smoke_test")

            assert mock_repair.called
            assert result["success"] is True, result
            assert result["staged"] is True
            assert DynamicToolRegistry.has_tool("repair_smoke_test") is False

            staged_code = (staged_root / "repair_smoke_test" / "skill.py").read_text(encoding="utf-8")
            assert "Repaired Deliverable" in staged_code
    finally:
        DynamicToolRegistry._skills = original_skills
        DynamicToolRegistry._initialized = original_initialized