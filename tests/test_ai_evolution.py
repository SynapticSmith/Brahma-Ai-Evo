import pytest
"""
tests/test_ai_evolution.py
==========================
Unit tests for Brahma AI's Advanced Intelligence Leap:
1. Long-Term Episodic Semantic Memory & Habit Cortex (MemoryCortex)
2. Autonomous Multi-Step Goal Planner & DAG Orchestrator (OmniPlanner)
3. Proactive Anticipatory Intelligence & Watcher (AnticipatoryWatcher)
4. Local Tier 1.5 Semantic Fast-Path Matcher (IntentDispatcher)
5. Execution Reflection & Diagnostic Self-Correction (ExecutionReflectionEngine)
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from core.memory_cortex import MemoryCortex
try:
    from core.omni_planner import PlanStep, get_omni_planner
except ImportError:
    PlanStep = None
    get_omni_planner = None
from core.anticipatory_watcher import AnticipatoryWatcher
from core.execution_reflection import get_reflection_engine
try:
    from core.intent_dispatcher import IntentDispatcher
except ImportError:
    IntentDispatcher = None


# =========================================================================
# 1. MEMORY CORTEX TESTS
# =========================================================================

def test_memory_cortex_crud(tmp_path: Path):
    """Verifies storing, reinforcing, recalling, and deleting episodic memories."""
    db_file = tmp_path / "test_memory.db"
    cortex = MemoryCortex(db_path=db_file)

    # 1. Remember facts
    m1 = cortex.remember("User preferred coding font is JetBrains Mono", category="preference", importance=0.9)
    assert m1 > 0

    m2 = cortex.remember("Default gateway router admin is at 192.168.1.1", category="hardware", importance=0.95)
    assert m2 > 0

    # 2. Reinforce memory
    m1_again = cortex.remember("User preferred coding font is JetBrains Mono", category="preference")
    assert m1_again == m1

    # 3. Recall with relevance
    results = cortex.recall("What font does the user prefer?", top_k=2)
    assert len(results) >= 1
    assert "JetBrains Mono" in results[0]["content"]
    assert results[0]["category"] == "preference"

    # Query for router
    net_res = cortex.recall("What is my router IP?", top_k=2)
    assert len(net_res) >= 1
    assert "192.168.1.1" in net_res[0]["content"]

    # 4. Context Prompt Builder
    prompt = cortex.build_context_prompt("font preferences")
    assert "[RECALLED LONG-TERM MEMORY & USER CONTEXT]:" in prompt
    assert "JetBrains Mono" in prompt

    # 5. Forget
    assert cortex.forget(m1) is True
    post_forget = cortex.recall("JetBrains font", top_k=2)
    assert len(post_forget) == 0


def test_memory_cortex_auto_harvest(tmp_path: Path):
    """Verifies autonomous fact harvesting from user conversation."""
    db_file = tmp_path / "test_harvest.db"
    cortex = MemoryCortex(db_path=db_file)

    msg = "Hey Brahma, my name is Alex and my favorite editor is Neovim."
    harvested = cortex.auto_harvest_facts(msg)
    assert len(harvested) >= 2

    recalled = cortex.recall("What is my name?", top_k=2)
    assert len(recalled) >= 1
    assert "Alex" in recalled[0]["content"]

    recalled_fav = cortex.recall("What editor do I like?", top_k=2)
    assert len(recalled_fav) >= 1
    assert "Neovim" in recalled_fav[0]["content"]


def test_memory_cortex_habits(tmp_path: Path):
    """Verifies habit graph tracking and correlation suggestions."""
    db_file = tmp_path / "test_habits.db"
    cortex = MemoryCortex(db_path=db_file)

    # First event
    cortex.record_habit_event("launch_vscode", "start_study_guard")
    assert cortex.get_habit_suggestion("launch_vscode", min_frequency=2) is None

    # Second event meets threshold
    cortex.record_habit_event("launch_vscode", "start_study_guard")
    assert cortex.get_habit_suggestion("launch_vscode", min_frequency=2) == "start_study_guard"


# =========================================================================
# 2. OMNI-PLANNER TESTS
# =========================================================================

@pytest.mark.skipif(get_omni_planner is None, reason='OmniPlanner rejected in favor of AgentLoop')
def test_omni_planner_decomposition():
    """Verifies composite goal detection and DAG decomposition."""
    planner = get_omni_planner()

    # Composite detection
    assert planner.is_composite_goal("Prepare my setup for gaming and clean ram") is True
    assert planner.is_composite_goal("Clean memory then save workspace") is True
    assert planner.is_composite_goal("what time is it") is False

    # Decomposition
    steps = planner.decompose_goal("Prepare my system for gaming and clean ram")
    assert len(steps) >= 2
    subsystems = [s.subsystem for s in steps]
    assert "mem_reduct" in subsystems
    assert "game_sentinel" in subsystems


@pytest.mark.skipif(get_omni_planner is None, reason='OmniPlanner rejected in favor of AgentLoop')
def test_omni_planner_execution():
    """Verifies multi-step plan execution and executive briefing synthesis."""
    planner = get_omni_planner()

    with patch("core.mem_reduct.MemReductEngine.clean_ram", return_value={"success": True, "freed_mb": 1500.0, "after_percent": 42.0}):
        with patch("core.game_sentinel.GameSentinelManager.activate_game_mode", return_value={"success": True, "game_name": "Macro Turbo"}):
            with patch("core.flowstate.FlowStateManager.snapshot_workspace", return_value={"apps_count": 3, "chrome_tabs_count": 5, "edge_tabs_count": 0, "vscode_workspaces": ["code"]}):
                steps = [
                    PlanStep(1, "mem_reduct", "clean_ram", "Purge standby RAM"),
                    PlanStep(2, "game_sentinel", "activate_game_mode", "Turbo Boost"),
                    PlanStep(3, "flowstate", "snapshot_workspace", "Snapshot tabs")
                ]
                report = planner.execute_plan("Prepare system", steps=steps)

                assert report.total_steps == 3
                assert report.successful_steps == 3
                assert report.failed_steps == 0
                assert "Memory Purged: Freed 1500.0 MB" in report.executive_briefing
                assert "Turbo Hardware:" in report.executive_briefing


# =========================================================================
# 3. ANTICIPATORY WATCHER TESTS
# =========================================================================

def test_anticipatory_watcher_cycle():
    """Verifies background inspection cycle triggers memory purge on high RAM."""
    watcher = AnticipatoryWatcher(check_interval_sec=0.1, cooldown_sec=1.0)
    watcher.ram_threshold_percent = 80.0

    mock_mem = MagicMock()
    mock_mem.percent = 85.0

    delivered_nudges = []
    watcher.set_nudge_callback(lambda msg, cat: delivered_nudges.append((msg, cat)))

    mock_disk = MagicMock()
    mock_disk.percent = 40.0
    mock_disk.free = 50 * 1024**3
    with patch("psutil.virtual_memory", return_value=mock_mem), patch("psutil.disk_usage", return_value=mock_disk):
        with patch("core.mem_reduct.MemReductEngine.clean_ram", return_value={"freed_mb": 1200.0}):
            watcher.run_inspection_cycle()
            assert len(delivered_nudges) == 1
            assert delivered_nudges[0][1] == "memory"
            assert "1200.0 MB" in delivered_nudges[0][0]

    # Cooldown suppresses immediate duplicate nudge
    with patch("psutil.virtual_memory", return_value=mock_mem), patch("psutil.disk_usage", return_value=mock_disk):
        watcher.run_inspection_cycle()
        assert len(delivered_nudges) == 1


# =========================================================================
# 4. TIER 1.5 LOCAL SEMANTIC FAST-PATH TESTS
# =========================================================================

@pytest.mark.skipif(IntentDispatcher is None, reason='IntentDispatcher not installed')
def test_intent_dispatcher_semantic_fastpath(tmp_path: Path):
    """Verifies that natural language colloquialisms match in Tier 1.5 without cloud LLM."""
    cache_file = tmp_path / "intent_cache.json"
    dispatcher = IntentDispatcher(cache_file=cache_file)

    # 1. RAM cleaner natural phrasing
    r1 = dispatcher.match_semantic_fastpath("please flush the ram cache")
    assert r1 is not None
    assert r1.intent == "game_sentinel"
    assert r1.action == "clean_ram"
    assert r1.latency_ms < 5.0

    # 2. Wi-Fi Radar natural phrasing
    r2 = dispatcher.match_semantic_fastpath("who is on my wifi network right now")
    assert r2 is not None
    assert r2.intent == "net_sentinel"
    assert r2.action == "scan_network"

    # 3. Router inspection natural phrasing
    r3 = dispatcher.match_semantic_fastpath("check my gateway router status")
    assert r3 is not None
    assert r3.intent == "net_sentinel"
    assert r3.action == "inspect_router"

    # 4. Workspace snapshot natural phrasing
    r4 = dispatcher.match_semantic_fastpath("snapshot my tabs and current setup")
    assert r4 is not None
    assert r4.intent == "flowstate"
    assert r4.action == "snapshot_workspace"

    # 5. Composite goal resolution in resolve()
    r_comp = dispatcher.resolve("prepare my system for gaming and clean ram")
    assert r_comp.intent in ("game_sentinel", "omni_planner")
    assert r_comp.action in ("clean_ram", "execute_composite_goal")


# =========================================================================
# 5. EXECUTION REFLECTION & DIAGNOSTICS TESTS
# =========================================================================

def test_execution_reflection():
    """Verifies post-execution verification and diagnostic analysis."""
    reflector = get_reflection_engine()

    # Success case with RAM
    clean_res = {"success": True, "freed_mb": 950.0, "after_percent": 38.0}
    ver_clean = reflector.reflect_and_verify("clean_ram", clean_res)
    assert ver_clean.verified_success is True
    assert "950.0 MB" in ver_clean.diagnosis

    # Failure case: Permission Denied
    perm_fail = {"success": False, "error": "[WinError 5] Access is denied"}
    ver_perm = reflector.reflect_and_verify("powercfg_change", perm_fail)
    assert ver_perm.verified_success is False
    assert "Administrator" in ver_perm.suggested_fix

    # Failure case: Unreachable ping target
    ping_fail = {"success": False, "status": "Unreachable", "target": "192.168.1.99"}
    ver_ping = reflector.reflect_and_verify("ping", ping_fail)
    assert ver_ping.verified_success is False
    assert "192.168.1.99" in ver_ping.diagnosis
