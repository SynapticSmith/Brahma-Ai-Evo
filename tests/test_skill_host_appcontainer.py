"""
Tests for SkillHost Windows AppContainer, Capability SIDs, and Isolation (WS-2, SEC-06).
"""

import pytest

from core.skill_host import (
    execute_in_skill_host,
)
from sandbox.launcher_win import (
    CAPABILITY_SIDS,
    AppContainerConfig,
    WindowsAppContainerManager,
)


def test_capability_sid_mapping():
    """Acceptance test: capability SIDs correctly map to standard Windows capability SIDs."""
    assert CAPABILITY_SIDS["internetClient"] == "S-1-15-3-1"
    assert CAPABILITY_SIDS["internetClientServer"] == "S-1-15-3-2"
    assert CAPABILITY_SIDS["privateNetworkClientServer"] == "S-1-15-3-3"
    assert CAPABILITY_SIDS["documentsLibrary"] == "S-1-15-3-7"


def test_appcontainer_config_generation(tmp_path):
    """Verifies AppContainerConfig constructs profile metadata and resolves capability SIDs."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    config = AppContainerConfig(
        profile_name="brahma.skill.test1234",
        display_name="Test Sandbox",
        capabilities=["internetClient", "documentsLibrary"],
        scratch_dir=scratch,
        memory_limit_mb=256,
        timeout_seconds=30.0,
    )

    assert config.profile_name == "brahma.skill.test1234"
    assert config.memory_limit_mb == 256
    sids = config.get_capability_sids()
    assert "S-1-15-3-1" in sids
    assert "S-1-15-3-7" in sids


def test_appcontainer_manager_profile_and_sid():
    """Verifies WindowsAppContainerManager creates or derives valid AppContainer SIDs."""
    mgr = WindowsAppContainerManager()
    config = AppContainerConfig(profile_name="brahma.skill.sample")

    ok, sid_str = mgr.create_or_get_profile(config)
    assert sid_str is not None
    assert sid_str.startswith("S-1-15-")


def test_execute_in_skill_host_executes_isolated_skill(tmp_path):
    """Verifies execution of a synthetic skill function inside the isolated SkillHost."""
    skill_file = tmp_path / "calc_skill.py"
    skill_file.write_text(
        "def execute(a, b):\n"
        "    return a * b + 10\n",
        encoding="utf-8",
    )

    result = execute_in_skill_host(skill_file, {"a": 5, "b": 6})
    assert result == 40


def test_execute_in_skill_host_scrubs_secrets(tmp_path):
    """Acceptance test: SkillHost scrubs secret environment variables from the child."""
    skill_file = tmp_path / "leak_check.py"
    skill_file.write_text(
        "import os\n"
        "def execute():\n"
        "    return 'OPENAI_API_KEY' in os.environ or 'GEMINI_API_KEY' in os.environ\n",
        encoding="utf-8",
    )

    has_secrets = execute_in_skill_host(skill_file, {})
    assert has_secrets is False


def test_execute_in_skill_host_timeout_enforcement(tmp_path):
    """Acceptance test: a hanging skill is terminated and raises TimeoutError."""
    hang_skill = tmp_path / "hang.py"
    hang_skill.write_text(
        "import time\n"
        "def execute():\n"
        "    time.sleep(10)\n"
        "    return 'done'\n",
        encoding="utf-8",
    )

    with pytest.raises(TimeoutError) as exc_info:
        execute_in_skill_host(hang_skill, {}, timeout=0.3)
    assert "timed out" in str(exc_info.value).lower()
