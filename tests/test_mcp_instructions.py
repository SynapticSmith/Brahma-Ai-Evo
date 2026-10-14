"""Tests for automatic MCP instruction ingestion.

The promise being tested: adding an MCP server requires no manual authoring of
tool instructions. A server that describes itself through the standard MCP
surface (initialize.instructions, prompts/list, resources/list) must have those
instructions become a skill manual automatically, and a server that describes
itself through nothing must still connect and register its tools.
"""

import asyncio

import pytest

from core import mcp_manager as mm


class _Obj:
    """Minimal stand-in for a pydantic MCP response object."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeSession:
    """A fake MCP ClientSession recording which optional calls were made."""

    def __init__(self, tools=(), instructions=None, prompts=(), resources=(),
                 fail_prompts=False, fail_resources=False):
        self._tools = list(tools)
        self.instructions = instructions
        self._prompts = list(prompts)
        self._resources = list(resources)
        self.fail_prompts = fail_prompts
        self.fail_resources = fail_resources
        self.calls = []

    async def list_tools(self):
        self.calls.append("list_tools")
        return _Obj(tools=self._tools)

    async def list_prompts(self):
        self.calls.append("list_prompts")
        if self.fail_prompts:
            raise RuntimeError("method not found")
        return _Obj(prompts=self._prompts)

    async def get_prompt(self, name):
        self.calls.append(f"get_prompt:{name}")
        body = self._prompts_by_name[name]
        return _Obj(messages=[_Obj(content=_Obj(text=body))])

    async def list_resources(self):
        self.calls.append("list_resources")
        if self.fail_resources:
            raise RuntimeError("method not found")
        return _Obj(resources=self._resources)


@pytest.fixture
def capture(monkeypatch):
    """Captures published manuals instead of touching the process-wide index."""
    published = []

    def _publish(manual):
        published.append(manual)

    monkeypatch.setattr(mm, "_publish_manual", _publish)
    return published


def _init(instructions=None, name="TestServer", version="1.0"):
    return _Obj(serverInfo=_Obj(name=name, version=version), instructions=instructions)


def _ingest(capture, session, instructions=None):
    asyncio.run(
        mm.MCPServerManager._ingest_instructions("TestServer", _init(instructions), session)
    )
    return capture


# --------------------------------------------------------------------------- #


def test_initialize_instructions_become_a_manual(capture):
    session = _FakeSession(instructions="Use Calyx for grounded memory. Never share vaults.")
    manuals = _ingest(capture, session, instructions="Use Calyx for grounded memory. Never share vaults.")

    assert len(manuals) == 1
    manual = manuals[0]
    assert manual.skill_id == "mcp:TestServer"
    assert "Never share vaults" in manual.body
    assert "TestServer" in manual.title
    assert manual.origin == "mcp:TestServer"


def test_prompts_are_ingested_automatically(capture):
    """A server that only ships prompts still gets a manual — no config needed."""
    prompts = [_Obj(name="usage_guide", description="How to use this server")]
    session = _FakeSession(prompts=prompts)
    session._prompts_by_name = {"usage_guide": "Always call kernel_answer after ingest."}

    manuals = _ingest(capture, session)

    assert len(manuals) == 1
    assert "Always call kernel_answer" in manuals[0].body
    assert "usage_guide" in manuals[0].body
    assert "list_prompts" in session.calls
    assert "get_prompt:usage_guide" in session.calls


def test_all_three_sources_are_combined(capture):
    prompts = [_Obj(name="guide", description="")]
    session = _FakeSession(
        prompts=prompts,
        resources=[_Obj(uri="docs://usage", description="Read the usage guide first.")],
    )
    session._prompts_by_name = {"guide": "Prompt-derived rule."}

    manuals = _ingest(capture, session, instructions="Handshake-derived rule.")
    body = manuals[0].body

    assert "Handshake-derived rule." in body
    assert "Prompt-derived rule." in body
    assert "Read the usage guide first." in body


def test_resources_are_filtered_to_actual_manuals(capture):
    session = _FakeSession(resources=[
        _Obj(uri="data://some-dump", description="A big data dump."),
        _Obj(uri="docs://readme", description="The README text."),
    ])
    manuals = _ingest(capture, session)
    assert "A big data dump." not in manuals[0].body
    assert "The README text." in manuals[0].body


def test_server_with_no_documentation_still_works(capture):
    """The common case: no instructions, no prompts. Must not raise."""
    session = _FakeSession()
    assert _ingest(capture, session) == []


def test_prompts_not_implemented_is_not_fatal(capture):
    session = _FakeSession(fail_prompts=True, fail_resources=True)
    manuals = _ingest(capture, session, instructions="Still usable.")
    assert len(manuals) == 1
    assert "Still usable." in manuals[0].body


def test_prompt_fetch_failure_degrades_to_its_description(capture):
    class _Broken(_FakeSession):
        async def get_prompt(self, name):
            raise RuntimeError("boom")

    session = _Broken(prompts=[_Obj(name="guide", description="Fallback description.")])
    session._prompts_by_name = {"guide": "unused"}
    manuals = _ingest(capture, session)
    assert "Fallback description." in manuals[0].body


def test_ingestion_is_bounded(capture):
    """A server advertising hundreds of prompts must not stall boot."""
    prompts = [_Obj(name=f"p{i}", description=f"desc {i}") for i in range(500)]

    class _Bounded(_FakeSession):
        async def get_prompt(self, name):
            return _Obj(messages=[_Obj(content=_Obj(text=f"body {name}"))])

    session = _Bounded(prompts=prompts)
    manuals = _ingest(capture, session)
    assert len(session.calls) <= mm.MAX_INGESTED_PROMPTS + 2
    assert len(manuals) == 1


def test_manual_body_is_truncated(capture, monkeypatch):
    monkeypatch.setattr(mm, "MAX_MANUAL_CHARS", 200)
    session = _FakeSession(instructions="x" * 5000)
    manuals = _ingest(capture, session, instructions="x" * 5000)
    assert len(manuals[0].body) <= 200


def test_flatten_handles_both_message_shapes():
    plain = _Obj(messages=[_Obj(content=_Obj(text="hello"))])
    assert mm._flatten_prompt_messages(plain) == "hello"

    chat = _Obj(chat_messages=[_Obj(content=_Obj(text="hi"))])
    assert mm._flatten_prompt_messages(chat) == "hi"

    dict_style = {"messages": [{"content": {"text": "dict style"}}]}
    assert "dict style" in mm._flatten_prompt_messages(dict_style)

    assert mm._flatten_prompt_messages(_Obj()) == ""


def test_drop_from_index_tolerates_missing_index(monkeypatch):
    """Index teardown must never raise, even if the index was never built."""
    class _Boom:
        def remove_source(self, s):
            raise RuntimeError("no index")

    import core.tool_index as ti

    monkeypatch.setattr(ti, "get_index", lambda: _Boom())
    mm._drop_from_index({"mcp:Gone"})  # must not raise