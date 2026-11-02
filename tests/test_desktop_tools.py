"""ADR-019 wave 1: native actions dispatched as ``ToolSpec``s.

The point of the migration is not "the function still runs". It is that these
tools now go through :class:`AgentRuntime` -- one permission boundary for the
GUI, the CLI and a test -- and that their HUD feedback comes from the host
layer instead of from ``core/tool_exec.py``.

Guards here:

* the wave-1 table carries the right capability class (and irreversible
  actions ask every time);
* a migrated tool is exercised with a recording host, including the GUI state
  (``current_file``) that used to be reachable only through a live window;
* a policy denial stops the call before the action body runs;
* a missing host is a diagnostic, not a global fallback;
* the runtime renders only the desktop names, so a code-agent turn does not
  repaint the panel.
"""

import ast
from pathlib import Path

import pytest

import core.desktop_tools as desktop_tools
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import unregister
from core import desktop_host

ROOT = Path(__file__).resolve().parent.parent


def _declarations() -> list[dict]:
    """``main.TOOL_DECLARATIONS`` without importing PyQt (it is a literal)."""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TOOL_DECLARATIONS"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("TOOL_DECLARATIONS not found in main.py")


@pytest.fixture
def registered():
    names = desktop_tools.register(_declarations())
    try:
        yield names
    finally:
        for name in names:
            unregister(name)


def _runtime(tmp_path: Path, **classes) -> AgentRuntime:
    base = {"read": "allow", "write": "allow", "exec": "allow"}
    base.update(classes)
    return AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict({"classes": base}),
        approver=None,
        headless=True,
    )


# --------------------------------------------------------------------------- #
# Spec shape
# --------------------------------------------------------------------------- #


def test_wave1_carries_the_policy_class_and_the_declared_schema():
    specs = {spec.name: spec for spec in desktop_tools.build_specs(_declarations())}

    assert specs, "wave 1 must publish at least one tool"
    for name, spec in specs.items():
        assert spec.tool_class in ("write", "exec"), name
        assert desktop_tools.DESKTOP_TAG in spec.tags
        assert spec.parameters.get("type") == "object", (
            f"{name} kept the Gemini uppercase schema instead of the OpenAI form"
        )

    assert specs["pdf_document"].tool_class == "write"
    assert specs["word_document"].tool_class == "write"
    # Undoing a self-patch needs a human and a backup, so it asks every time --
    # even after an "always allow" (AgentRuntime.execute honours that).
    for name in ("auto_heal", "self_patch", "rollback", "shutdown_brahma"):
        assert specs[name].tool_class == "exec"
        assert specs[name].requires_approval is True
    assert specs["reminder"].requires_approval is False


def test_aliases_borrow_the_declared_schema_and_are_named_in_the_description():
    declared = {item["name"]: item for item in _declarations()}
    specs = {spec.name: spec for spec in desktop_tools.build_specs(_declarations())}
    assert specs["rollback"].parameters == specs["auto_heal"].parameters
    assert "auto-heal" in specs["rollback"].description
    assert "rollback" in specs["rollback"].description
    assert specs["auto_heal"].description == declared["auto_heal"]["description"]


# --------------------------------------------------------------------------- #
# Dispatch, host injection, policy
# --------------------------------------------------------------------------- #


def test_a_migrated_action_runs_with_a_recording_host(registered, tmp_path: Path):
    """``pdf_document`` used to be an ``elif`` branch with no policy check."""
    host = desktop_host.RecordingHost()
    out = tmp_path / "wave-one.pdf"
    runtime = _runtime(tmp_path)
    runtime.event_sink = desktop_host.HostEventSink(host, desktop_tools.desktop_names())

    result = runtime.execute(
        "pdf_document",
        {
            "title": "Wave One",
            "content": "Created by the ADR-019 migration test.",
            "output_path": str(out),
            "auto_open": False,
        },
        extra={"host": host},
    )

    assert out.exists() and out.stat().st_size > 0
    assert "PDF created" in result
    # The HUD came from the runtime's events, not from the action.
    assert host.spoken and "pdf document" in host.spoken[0]
    assert host.ui.operations[0]["tool"] == "pdf_document"
    assert host.ui.deliverables[-1]["file_path"] == str(out)
    assert host.ui.states[0] == "THINKING" and host.ui.states[-1] == "LISTENING"


def test_gui_state_reaches_the_action_through_the_host(registered, tmp_path, monkeypatch):
    """``word_document`` reads the file-drop area, which is pure GUI state."""
    import actions.docx_tools as docx_tools

    seen = {}
    monkeypatch.setattr(docx_tools, "word_document", lambda **kwargs: seen.update(kwargs) or "ok")

    host = desktop_host.RecordingHost()
    host.ui.current_file = str(tmp_path / "dropped.docx")

    result = _runtime(tmp_path).execute(
        "word_document", {"action": "read"}, extra={"host": host}
    )

    assert result == "ok"
    assert seen["parameters"]["file_path"] == host.ui.current_file
    assert seen["player"] is host.ui


def test_policy_denial_stops_the_action_before_it_runs(registered, tmp_path, monkeypatch):
    import actions.pdf_tools as pdf_tools

    called = []
    monkeypatch.setattr(pdf_tools, "create_pdf", lambda **kwargs: called.append(kwargs) or "x")

    result = _runtime(tmp_path, write="deny").execute(
        "pdf_document",
        {"title": "Nope"},
        extra={"host": desktop_host.RecordingHost()},
    )

    assert result.lower().startswith("permission denied")
    assert called == [], "the handler ran despite a denied policy"


def test_exec_tools_are_denied_without_an_approver(registered, tmp_path, monkeypatch):
    import actions.reminder as reminder_module

    called = []
    monkeypatch.setattr(
        reminder_module, "reminder", lambda **kwargs: called.append(kwargs) or "x"
    )

    runtime = AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict({"classes": {"exec": "allow"}}),
        approver=None,
        headless=True,
    )
    result = runtime.execute(
        "reminder",
        {"date": "2030-01-01", "time": "09:00", "message": "x"},
        extra={"host": desktop_host.RecordingHost()},
    )

    assert result.lower().startswith("permission denied")
    assert "headless.exec" in result
    assert called == []


def test_requires_approval_is_never_remembered(registered, tmp_path, monkeypatch):
    import actions.auto_heal_engine as auto_heal_engine

    calls = []
    monkeypatch.setattr(
        auto_heal_engine, "auto_heal", lambda **kwargs: calls.append(kwargs) or "patched"
    )

    asked = []

    def approve(name, args, detail):
        asked.append(name)
        return "always"  # even ALWAYS ALLOW must not stick here

    runtime = AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict({"classes": {"exec": "allow"}, "headless": {}}),
        approver=approve,
        headless=False,
    )
    assert runtime.execute(
        "auto_heal", {"action": "status"}, extra={"host": desktop_host.RecordingHost()}
    ) == "patched"

    runtime.approver = None
    second = runtime.execute(
        "auto_heal", {"action": "status"}, extra={"host": desktop_host.RecordingHost()}
    )
    assert second.lower().startswith("not run:")
    assert "tool.requires_approval" in second
    assert asked == ["auto_heal"]
    assert len(calls) == 1


def test_a_missing_host_is_a_diagnostic_not_a_fallback(registered, tmp_path):
    result = _runtime(tmp_path).execute("pdf_document", {"title": "No host"})
    assert result == desktop_host.MISSING_HOST
    assert "GUI" in result


def test_attach_host_installs_the_bundle_for_a_text_turn(registered, tmp_path, monkeypatch):
    """``AgentLoop`` builds its own ``extra``, so the host must be a runtime default."""
    import actions.pdf_tools as pdf_tools

    seen = {}
    monkeypatch.setattr(
        pdf_tools,
        "create_pdf",
        lambda **kwargs: seen.update(kwargs) or "PDF created: C:\\tmp\\x.pdf",
    )

    from agent import runtime as agent_runtime

    runtime = _runtime(tmp_path)
    previous = agent_runtime._current
    agent_runtime._current = runtime
    try:
        sink = desktop_tools.attach_host(_FakeLive())
    finally:
        agent_runtime._current = previous

    # No per-call extra, exactly like the loop's own dispatch.
    assert runtime.execute("pdf_document", {"title": "Text door"}) == (
        "PDF created: C:\\tmp\\x.pdf"
    )
    assert seen["player"] is sink.host.ui
    assert runtime.default_extra["host"] is sink.host

    # A per-call mapping still wins over the default.
    other = desktop_host.RecordingHost()
    runtime.execute("pdf_document", {"title": "Explicit"}, extra={"host": other})
    assert seen["player"] is other.ui


# --------------------------------------------------------------------------- #
# HUD feedback moved to the runtime event sink
# --------------------------------------------------------------------------- #


def test_the_runtime_publishes_the_result_for_the_host(tmp_path):
    (tmp_path / "note.txt").write_text("hello host", encoding="utf-8")
    events = []
    runtime = _runtime(tmp_path)
    runtime.event_sink = events.append

    runtime.execute("read_file", {"path": "note.txt"})

    ends = [event for event in events if event["type"] == "tool_end"]
    assert ends and ends[-1]["name"] == "read_file"
    assert "hello host" in ends[-1]["output"]


def test_the_event_sink_renders_only_desktop_tools():
    host = desktop_host.RecordingHost()
    sink = desktop_host.HostEventSink(host, frozenset({"pdf_document"}))

    sink({"type": "tool_start", "name": "read_file", "args": {"path": "x"}})
    assert host.ui.operations == []
    assert host.spoken == []

    sink({"type": "tool_start", "name": "pdf_document", "args": {"title": "Report"}})
    assert host.ui.operations[0]["tool"] == "pdf_document"
    assert host.ui.states == ["THINKING"]

    sink(
        {
            "type": "tool_end",
            "name": "pdf_document",
            "args": {"title": "Report"},
            "output": "PDF created: C:\\Users\\me\\Downloads\\Report.pdf",
        }
    )
    assert host.ui.deliverables[-1]["file_path"].endswith("Report.pdf")
    assert host.ui.states[-1] == "LISTENING"
    assert not [text for text in host.spoken if "completed" in text]


def test_attach_host_chains_an_existing_sink(registered, tmp_path):
    from agent import runtime as agent_runtime

    seen = []
    runtime = _runtime(tmp_path)
    runtime.event_sink = seen.append

    previous = agent_runtime._current
    agent_runtime._current = runtime
    try:
        sink = desktop_tools.attach_host(_FakeLive())
        assert runtime.event_sink is sink
        assert sink.also is not None and sink.also.__self__ is seen
        assert "pdf_document" in sink.names

        # A second attach (the voice executor builds one per instance) must not
        # nest sinks or drop the sink it was chaining.
        again = desktop_tools.attach_host(_FakeLive())
        assert again.also is not None and again.also.__self__ is seen
        assert not isinstance(again.also, desktop_host.HostEventSink)
    finally:
        agent_runtime._current = previous


class _FakeLive:
    def __init__(self):
        self.ui = desktop_host.RecordingUI()
        self._loop = None

    def speak(self, text):
        return None


# --------------------------------------------------------------------------- #
# Static guards: the branch really moved, and agent/ stayed portable
# --------------------------------------------------------------------------- #


def test_the_migrated_branches_left_the_legacy_chain():
    source = (ROOT / "core" / "tool_exec.py").read_text(encoding="utf-8")
    elif_lines = [
        line for line in source.splitlines() if line.lstrip().startswith("elif ")
    ]
    leftovers = [
        line.strip()
        for line in elif_lines
        for name in desktop_tools.WAVE1
        if f'"{name}"' in line
    ]
    assert leftovers == [], leftovers
    assert "desktop_host.present_start" in source
    assert "desktop_tools.attach_host" in source


def test_agent_never_imports_the_desktop_layer():
    for path in (ROOT / "agent").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "desktop_tools" not in text, path.name
        assert "desktop_host" not in text, path.name
