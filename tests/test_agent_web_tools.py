"""The harness-native web tools: fetch, search, and the URL guard.

These are the tools the API's ``worker``/``operator`` profiles can reach now
that the harness owns a ``network`` class. The guard is the important half: a
remote token must not be able to read ``169.254.169.254`` or a service on
loopback, and a redirect must not be a way around that check.

No external network is used. The fetch tests run against a loopback server with
the explicit private-host override, which is exactly the trusted-local case the
override exists for.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agent import web
from agent.spec import ToolContext

HTML_PAGE = (
    "<html><head><title>T</title><style>.x{color:red}</style></head>"
    "<body><h1>Hello</h1><script>bad()</script><p>World</p></body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib callback name
        if self.path == "/page":
            self._send(200, "text/html; charset=utf-8", HTML_PAGE.encode("utf-8"))
        elif self.path == "/json":
            body = json.dumps({"b": 2, "a": 1}).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
        elif self.path == "/binary":
            self._send(200, "application/octet-stream", b"\x00\x01\x02binary")
        elif self.path == "/long":
            self._send(200, "text/plain; charset=utf-8", (b"a" * 500))
        elif self.path == "/redirect-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.end_headers()
        elif self.path == "/redirect-metadata":
            self.send_response(302)
            self.send_header("Location", "http://metadata.google.internal/latest/meta-data/")
            self.end_headers()
        else:
            self._send(404, "text/plain", b"nope")

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence the test output
        return


@pytest.fixture()
def local_server(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("BRAHMA_WEB_ALLOW_PRIVATE", "1")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def context(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path)


# --------------------------------------------------------------------------- #
# URL guard
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://user:pass@example.com/",
        "javascript:alert(1)",
        "",
    ],
)
def test_validate_url_refuses_unsafe_shapes(url):
    with pytest.raises(ValueError):
        web.validate_url(url)


def test_validate_url_refuses_loopback_when_the_override_is_off(monkeypatch):
    monkeypatch.delenv("BRAHMA_WEB_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(web, "_allow_private", lambda: False)

    with pytest.raises(ValueError):
        web.validate_url("http://127.0.0.1:8000/")
    with pytest.raises(ValueError):
        web.validate_url("http://169.254.169.254/latest/meta-data/")


def test_metadata_hostnames_are_refused_even_with_the_override(monkeypatch):
    monkeypatch.setattr(web, "_allow_private", lambda: True)

    for host in ("localhost", "metadata.google.internal", "box.local"):
        with pytest.raises(ValueError):
            web.validate_url(f"http://{host}/")


def test_address_classification():
    assert web._address_is_public("8.8.8.8")
    assert not web._address_is_public("127.0.0.1")
    assert not web._address_is_public("10.0.0.5")
    assert not web._address_is_public("169.254.169.254")
    assert not web._address_is_public("::1")
    assert not web._address_is_public("not-an-address")


# --------------------------------------------------------------------------- #
# web_fetch
# --------------------------------------------------------------------------- #

def test_web_fetch_strips_markup_and_scripts(local_server, context):
    text = web.web_fetch({"url": f"{local_server}/page"}, context)

    assert "Hello" in text
    assert "World" in text
    assert "bad()" not in text
    assert ".x{color:red}" not in text


def test_web_fetch_pretty_prints_json(local_server, context):
    text = web.web_fetch({"url": f"{local_server}/json"}, context)

    assert '"a": 1' in text
    assert '"b": 2' in text


def test_web_fetch_refuses_binary_content(local_server, context):
    text = web.web_fetch({"url": f"{local_server}/binary"}, context)

    assert "binary content" in text
    assert "not shown as text" in text


def test_web_fetch_validates_redirect_targets(local_server, context):
    assert "refused a redirect" in web.web_fetch(
        {"url": f"{local_server}/redirect-file"}, context
    )
    assert "refused a redirect" in web.web_fetch(
        {"url": f"{local_server}/redirect-metadata"}, context
    )


def test_web_fetch_keeps_a_usable_minimum_cap(local_server, context):
    text = web.web_fetch({"url": f"{local_server}/page", "max_chars": 10}, context)

    # The floor is 200 chars, so a tiny request cannot suppress the page.
    assert "Hello" in text
    assert "World" in text


def test_web_fetch_truncates_a_long_body(local_server, context):
    text = web.web_fetch({"url": f"{local_server}/long", "max_chars": 200}, context)

    assert "text truncated at 200 chars" in text
    assert len(text) < 400


def test_web_fetch_reports_an_http_error(local_server, context):
    text = web.web_fetch({"url": f"{local_server}/missing"}, context)

    assert "HTTP 404" in text


def test_web_fetch_refuses_before_opening_a_socket(monkeypatch, context):
    def _boom(*_args, **_kwargs):
        raise AssertionError("a refused URL must never reach the transport")

    monkeypatch.setattr(web, "_request", _boom)
    monkeypatch.setattr(web, "_allow_private", lambda: False)

    text = web.web_fetch({"url": "http://127.0.0.1/"}, context)
    assert "refused" in text


# --------------------------------------------------------------------------- #
# web_search
# --------------------------------------------------------------------------- #

DDG_HTML = """
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone">
    First result
  </a>
  <a class="result__snippet">First <b>snippet</b> text</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.com/two">Second result</a>
  <a class="result__snippet">Second snippet</a>
</div>
"""


def test_web_search_parses_duckduckgo_html(monkeypatch, context):
    monkeypatch.setattr(web, "_search_backend", lambda: "duckduckgo")
    monkeypatch.setattr(
        web,
        "_request",
        lambda *_args, **_kwargs: (DDG_HTML.encode("utf-8"), "text/html", "utf-8"),
    )

    text = web.web_search({"query": "brahma evo"}, context)

    assert "First result" in text
    assert "https://example.com/one" in text
    assert "First snippet text" in text
    assert "Second result" in text
    assert text.index("First result") < text.index("Second result")


def test_web_search_limits_the_result_count(monkeypatch, context):
    monkeypatch.setattr(web, "_search_backend", lambda: "duckduckgo")
    monkeypatch.setattr(
        web,
        "_request",
        lambda *_args, **_kwargs: (DDG_HTML.encode("utf-8"), "text/html", "utf-8"),
    )

    text = web.web_search({"query": "x", "max_results": 1}, context)

    assert "First result" in text
    assert "Second result" not in text


def test_web_search_falls_back_when_a_keyed_backend_has_no_key(monkeypatch, context):
    monkeypatch.setattr(web, "_search_backend", lambda: "brave")
    monkeypatch.setattr(web, "_search_key", lambda _names: "")
    monkeypatch.setattr(
        web,
        "_ddg_search",
        lambda *_args, **_kwargs: [
            {"title": "Fallback", "url": "https://example.com/f", "snippet": "s"}
        ],
    )

    text = web.web_search({"query": "x"}, context)

    assert "brave unavailable" in text
    assert "used DuckDuckGo" in text
    assert "Fallback" in text


def test_web_search_requires_a_query(context):
    assert web.web_search({"query": "  "}, context) == "web_search needs a 'query'."


def test_web_search_returns_a_clean_empty_message(monkeypatch, context):
    monkeypatch.setattr(web, "_search_backend", lambda: "duckduckgo")
    monkeypatch.setitem(web._BACKENDS, "duckduckgo", lambda *_args, **_kwargs: [])

    text = web.web_search({"query": "nothing"}, context)
    assert text.startswith("No search results")


def test_the_web_tools_are_network_class_and_not_desktop():
    specs = {spec.name: spec for spec in web.SPECS}

    assert set(specs) == {"web_fetch", "web_search"}
    for spec in specs.values():
        assert spec.tool_class == "network"
        assert "desktop" not in spec.tags
        assert spec.max_output_chars == web.MAX_OUTPUT_CHARS
