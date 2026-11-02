"""Phase 0 (Q0-A) regressions: execution and code lockdown.

These are guards, not behaviour probes. They fail if a future edit brings back
a runtime installer, a TLS bypass in the Forge prompts, or a model-facing tool
declaration that reaches around ``AgentRuntime``.
"""

import ast
import types
from pathlib import Path

import core.skill_forge as skill_forge
from core.skill_crucible import SkillCrucible

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"

# Literals that teach a generated skill to disable certificate verification.
# The Forge prompt must not contain any of them, even as a prohibition: naming
# the API is enough for a model to reach for it.
TLS_BYPASS_LITERALS = (
    "verify=False",
    "_create_unverified_context",
    "CERT_NONE",
    "check_hostname = False",
    "check_hostname=False",
)


def _tool_declarations() -> list[dict]:
    """``main.TOOL_DECLARATIONS`` without importing PyQt.

    The list is a pure literal, so ``ast.literal_eval`` reads it from source.
    """
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TOOL_DECLARATIONS"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("TOOL_DECLARATIONS not found in main.py")


def _function_node(name: str) -> ast.FunctionDef:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in main.py")


def test_forge_prompts_do_not_teach_a_tls_bypass():
    source = (ROOT / "core" / "skill_forge.py").read_text(encoding="utf-8")
    found = [needle for needle in TLS_BYPASS_LITERALS if needle in source]
    assert not found, (
        "core/skill_forge.py still contains TLS-bypass literals in its prompts: "
        + ", ".join(found)
    )


def test_resolve_dependencies_reports_without_installing(monkeypatch):
    calls = []

    def _explode(*args, **kwargs):
        calls.append(args)
        raise AssertionError("resolve_dependencies must not spawn a subprocess")

    # Replace the module attribute so a `subprocess.run` call is caught without
    # patching the real subprocess module for every other test.
    monkeypatch.setattr(
        "core.skill_crucible.subprocess", types.SimpleNamespace(run=_explode)
    )

    ok, message = SkillCrucible.resolve_dependencies(["json"])
    assert ok is True, message
    assert "json" in message

    ok, message = SkillCrucible.resolve_dependencies(["__brahma_missing_dependency__"])
    assert ok is False
    assert "__brahma_missing_dependency__" in message
    assert "No packages were installed" in message
    assert "pip install" in message
    assert calls == []


def test_no_model_facing_agent_task_or_confirmed_declaration():
    declarations = _tool_declarations()
    names = {declaration.get("name") for declaration in declarations}

    assert "agent_task" not in names

    offenders = [
        declaration["name"]
        for declaration in declarations
        if "confirmed"
        in ((declaration.get("parameters") or {}).get("properties") or {})
    ]
    assert offenders == [], (
        "these declarations still advertise a model-supplied 'confirmed' "
        f"parameter: {offenders}"
    )


def test_agent_task_handler_does_not_reach_the_legacy_queue():
    source = (ROOT / "core" / "tool_exec.py").read_text(encoding="utf-8")
    assert "agent.task_queue" not in source
    assert "get_queue" not in source


def test_forge_pipeline_never_executes_or_registers_its_output():
    source = (ROOT / "core" / "skill_forge.py").read_text(encoding="utf-8")
    assert "DynamicToolRegistry" not in source
    assert "execute_sync" not in source


def test_forge_skill_does_not_execute_the_staged_draft():
    for node in ast.walk(_function_node("_forge_skill")):
        if isinstance(node, ast.Attribute) and node.attr == "execute_sync":
            raise AssertionError(
                "_forge_skill must not execute a freshly forged skill; "
                "it is staged for review"
            )


def test_staged_skills_live_under_user_data(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_forge, "get_user_data_dir", lambda: tmp_path)

    staged = skill_forge.staged_skills_dir()

    assert staged == tmp_path / "staged_skills"
    assert staged.is_dir()
