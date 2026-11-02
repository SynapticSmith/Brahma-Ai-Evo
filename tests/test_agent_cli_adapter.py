"""Contract and unit tests for CLIAgentAdapter (ADR-018, Q2)."""

from pathlib import Path
import sys

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import register, unregister
from core.agent_bridge.adapters.cli_agent import CLIAgentAdapter
from core.agent_bridge.models import AgentRecord, AgentTransport

FAKE_CLI_SCRIPT = r'''
import sys

args = sys.argv[1:]
if not args:
    sys.exit(0)

if args[0] == "--version":
    print("fake-coder 2.1.0")
    sys.exit(0)

if args[0] == "FAIL_TRIGGER":
    sys.stderr.write("Command failed with critical error\n")
    sys.exit(1)

print(f"Executed CLI task: {' '.join(args)}")
sys.exit(0)
'''


def _make_fake_cli_record(tmp_path: Path) -> AgentRecord:
    script_path = tmp_path / "fake_coder.py"
    script_path.write_text(FAKE_CLI_SCRIPT, encoding="utf-8")

    return AgentRecord(
        id="fake_coder",
        display_name="Fake Coder CLI",
        kind="cli_agent",
        executable=sys.executable,
        transport=AgentTransport(
            mode="stdio",
            argv=[sys.executable, str(script_path)],
            cwd=str(tmp_path),
        ),
        sources=["path"],
    )


def test_cli_adapter_probe_success(tmp_path):
    record = _make_fake_cli_record(tmp_path)
    adapter = CLIAgentAdapter(record)

    probe = adapter.probe()
    assert probe.ok is True
    assert probe.stage == "capability"
    assert "fake-coder 2.1.0" in (probe.version or "")


def test_cli_adapter_probe_missing_executable():
    record = AgentRecord(
        id="missing_cli",
        display_name="Missing CLI",
        kind="cli_agent",
        executable="C:\\nonexistent_dir_123\\missing_cli.exe",
    )
    adapter = CLIAgentAdapter(record)
    probe = adapter.probe()
    assert probe.ok is False
    assert probe.stage == "liveness"


def test_cli_adapter_connect_and_verbs(tmp_path):
    record = _make_fake_cli_record(tmp_path)
    adapter = CLIAgentAdapter(record)
    adapter.connect()

    assert adapter.record.status == "connected"
    verbs = adapter.verbs()
    assert len(verbs) == 1
    assert verbs[0].name == "run_task"
    assert verbs[0].mutating is True  # Requires approval policy check


def test_cli_adapter_call_success(tmp_path):
    record = _make_fake_cli_record(tmp_path)
    adapter = CLIAgentAdapter(record)
    adapter.connect()

    result = adapter.call("run_task", {"prompt": "Write hello world"})
    assert result.ok is True
    assert "Executed CLI task: Write hello world" in result.output
    assert result.error is None


def test_cli_adapter_degraded_state(tmp_path):
    record = _make_fake_cli_record(tmp_path)
    adapter = CLIAgentAdapter(record)
    adapter.connect()

    result = adapter.call("run_task", {"prompt": "FAIL_TRIGGER"})
    assert result.ok is False
    assert result.error is not None
    assert "Command failed with critical error" in result.error


def test_cli_adapter_to_tool_specs_and_runtime_execution(tmp_path):
    record = _make_fake_cli_record(tmp_path)
    adapter = CLIAgentAdapter(record)
    adapter.connect()

    specs = adapter.to_tool_specs()
    assert len(specs) == 1
    task_spec = specs[0]
    assert task_spec.name == "agent_fake_coder_run_task"
    assert task_spec.tool_class == "exec"
    assert task_spec.requires_approval is True

    # Register and test execution through AgentRuntime with approval
    register(task_spec)
    try:
        runtime = AgentRuntime(
            workspace=tmp_path,
            policy=PermissionPolicy.from_dict({"classes": {"exec": "allow"}}),
            approver=lambda name, args, detail: True,
            headless=False,
        )
        out = runtime.execute(task_spec.name, {"prompt": "Refactor codebase"})
        assert "Executed CLI task: Refactor codebase" in out
    finally:
        unregister(task_spec.name)
