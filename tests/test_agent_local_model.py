"""Contract and unit tests for LocalModelAdapter (ADR-018, Q2)."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import register, unregister
from core.agent_bridge.adapters.local_model import LocalModelAdapter
from core.agent_bridge.models import AgentRecord, AgentTransport


class MockOpenAIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet logging during tests

    def do_GET(self):
        if self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "data": [
                    {"id": "llama3.2:latest"},
                    {"id": "qwen2.5-coder:7b"},
                ]
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            req = json.loads(body.decode("utf-8"))

            prompt = req.get("messages", [{}])[0].get("content", "")
            if prompt == "CRASH_TRIGGER":
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Internal Server Error"}).encode("utf-8"))
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"Local response to: {prompt}",
                        }
                    }
                ]
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def _start_mock_server() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), MockOpenAIHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}/v1"


def test_local_model_probe_success():
    server, endpoint = _start_mock_server()
    try:
        record = AgentRecord(
            id="test_ollama",
            display_name="Test Ollama",
            kind="local_model",
            endpoint=endpoint,
            transport=AgentTransport(mode="http_openai"),
        )
        adapter = LocalModelAdapter(record)
        probe = adapter.probe()

        assert probe.ok is True
        assert probe.stage == "capability"
        assert "llama3.2:latest" in probe.models
        assert "qwen2.5-coder:7b" in probe.models
    finally:
        server.shutdown()


def test_local_model_probe_connection_refused():
    record = AgentRecord(
        id="offline_server",
        display_name="Offline Server",
        kind="local_model",
        endpoint="http://127.0.0.1:54321/v1",  # Unbound port
        transport=AgentTransport(mode="http_openai"),
    )
    adapter = LocalModelAdapter(record)
    probe = adapter.probe()
    assert probe.ok is False
    assert probe.stage == "protocol"


def test_local_model_connect_and_verbs():
    server, endpoint = _start_mock_server()
    try:
        record = AgentRecord(
            id="test_ollama",
            display_name="Test Ollama",
            kind="local_model",
            endpoint=endpoint,
        )
        adapter = LocalModelAdapter(record)
        adapter.connect()

        assert adapter.record.status == "connected"
        verbs = adapter.verbs()
        assert len(verbs) == 1
        assert verbs[0].name == "chat"
        assert verbs[0].parameters["properties"]["model"]["default"] == "llama3.2:latest"
    finally:
        server.shutdown()


def test_local_model_call_success():
    server, endpoint = _start_mock_server()
    try:
        record = AgentRecord(
            id="test_ollama",
            display_name="Test Ollama",
            kind="local_model",
            endpoint=endpoint,
        )
        adapter = LocalModelAdapter(record)
        result = adapter.call("chat", {"prompt": "What is Python?"})

        assert result.ok is True
        assert "Local response to: What is Python?" in result.output
        assert result.error is None
    finally:
        server.shutdown()


def test_local_model_degraded_state():
    server, endpoint = _start_mock_server()
    try:
        record = AgentRecord(
            id="test_ollama",
            display_name="Test Ollama",
            kind="local_model",
            endpoint=endpoint,
        )
        adapter = LocalModelAdapter(record)
        # Trigger mock 500 error
        result = adapter.call("chat", {"prompt": "CRASH_TRIGGER"})

        assert result.ok is False
        assert result.error is not None
        assert "500" in result.error
    finally:
        server.shutdown()


def test_local_model_to_tool_specs_and_runtime_execution(tmp_path):
    server, endpoint = _start_mock_server()
    try:
        record = AgentRecord(
            id="test_ollama",
            display_name="Test Ollama",
            kind="local_model",
            endpoint=endpoint,
        )
        adapter = LocalModelAdapter(record)
        adapter.connect()

        specs = adapter.to_tool_specs()
        assert len(specs) == 1
        chat_spec = specs[0]
        assert chat_spec.name == "agent_test_ollama_chat"
        assert chat_spec.tool_class == "network"

        # Dispatch via AgentRuntime
        register(chat_spec)
        try:
            runtime = AgentRuntime(
                workspace=tmp_path,
                policy=PermissionPolicy.from_dict({"classes": {"network": "allow"}}),
            )
            out = runtime.execute(chat_spec.name, {"prompt": "Hello Runtime"})
            assert "Local response to: Hello Runtime" in out
        finally:
            unregister(chat_spec.name)
    finally:
        server.shutdown()
