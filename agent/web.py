"""Web search and page fetch as harness-native tools.

The desktop bundle already had a ``web_search``, but it lives in the PyQt/COM
host and is tagged ``desktop``. The harness deliberately drops that tag (the
API and CLI must not pull in the desktop host), which left the ``worker``
profile's ``network: allow`` with nothing to allow. These two tools are the
harness-shaped equivalent: stdlib only, no SDK, no desktop host.

Two rules keep them safe to hand to a remote API token:

* **SSRF guard.** Only ``http``/``https`` is accepted, credentials in the URL
  are refused, and the host is resolved and checked against the non-public
  address ranges (loopback, private, link-local, reserved, multicast). Redirect
  hops are validated by the same function, not just the first URL.
* **Bounded output.** The body is read up to a hard byte cap and the returned
  text is capped again so one page cannot consume the model's context.

The search backend is configurable. The default is keyless DuckDuckGo HTML;
Brave, Tavily and SerpAPI are used when a key is present. A keyed backend with
no key falls back to DuckDuckGo and says so rather than failing the turn.
"""
from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from .spec import ToolContext, register, ToolSpec

#: Socket budget for one request. Deliberately finite: an unbounded fetch would
#: hold the agent's worker thread for as long as the remote server stays quiet.
DEFAULT_TIMEOUT = 20.0
MAX_TIMEOUT = 60.0
#: Read cap for a page. A 200 MB log is a denial of context, not a fetch.
MAX_FETCH_BYTES = 2_000_000
#: Returned text cap (the ToolSpec enforces the same value again).
MAX_OUTPUT_CHARS = 8_000
MAX_REDIRECTS = 5
DEFAULT_RESULTS = 5
MAX_RESULTS = 10

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) BrahmaEvo/1.0"
)

_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "instance-data",
}
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".home.arpa",
)

_DDG_HTML = "https://html.duckduckgo.com/html/"
_BRAVE_API = "https://api.search.brave.com/res/v1/web/search"
_TAVILY_API = "https://api.tavily.com/search"
_SERPAPI = "https://serpapi.com/search.json"


# --------------------------------------------------------------------------- #
# URL guard
# --------------------------------------------------------------------------- #

def _allow_private() -> bool:
    """True only when the owner explicitly opts in.

    A remote API token must never be able to read ``http://169.254.169.254`` or
    a service on ``127.0.0.1``; the override exists for a trusted local setup.
    """
    raw = (os.getenv("BRAHMA_WEB_ALLOW_PRIVATE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    try:
        from memory import config_manager

        value = config_manager.get_setting("web_allow_private_hosts", False)
    except Exception:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _address_is_public(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _check_host(hostname: str) -> None:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("URL has no host")
    if host in _BLOCKED_HOSTNAMES or any(host.endswith(s) for s in _BLOCKED_SUFFIXES):
        raise ValueError(f"refusing to fetch non-public host {host!r}")
    if _allow_private():
        return

    # A literal address needs no DNS. A name is resolved once for the check;
    # urllib resolves it again when connecting, so this is a strong filter
    # rather than a defence against a hostile DNS server that changes answers.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve {host!r}: {exc}") from exc
    addresses = {str(info[4][0]) for info in infos if info[4]}
    if not addresses:
        raise ValueError(f"could not resolve {host!r}")
    for address in addresses:
        if not _address_is_public(address):
            raise ValueError(
                f"refusing to fetch {host!r}: {address} is not a public address"
            )


def validate_url(url: str) -> str:
    """Return a normalised URL or raise ``ValueError`` for an unsafe one."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("a URL is required")
    parsed = urllib.parse.urlsplit(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"only http/https URLs are supported (got {scheme or 'none'!r})")
    if parsed.username or parsed.password:
        # Never forward credentials, and never echo them back in an error.
        raise ValueError("URLs with embedded credentials are refused")
    _check_host(parsed.hostname or "")
    return urllib.parse.urlunsplit(parsed)


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate every redirect target, not only the URL the model supplied."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_GuardedRedirectHandler())


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #

def _timeout(args: Dict[str, Any]) -> float:
    try:
        value = float(args.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT
    return max(1.0, min(value, MAX_TIMEOUT))


def _request(
    url: str,
    *,
    timeout: float,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
) -> Tuple[bytes, str, str]:
    """Fetch one URL, returning ``(body, content_type, charset)``."""
    validate_url(url)
    merged = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    merged.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=merged)
    with _opener().open(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type() or "application/octet-stream"
        charset = response.headers.get_content_charset() or "utf-8"
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_FETCH_BYTES:
                break
    return b"".join(chunks)[:MAX_FETCH_BYTES], content_type, charset


def _decode(body: bytes, charset: str) -> str:
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# HTML -> text
# --------------------------------------------------------------------------- #

class _TextExtractor(HTMLParser):
    """Drop markup and scripts, keep readable block-separated text."""

    _SKIP = {"script", "style", "noscript", "template", "svg", "canvas"}
    _BLOCK = {
        "p", "div", "br", "li", "tr", "td", "th", "section", "article",
        "header", "footer", "pre", "blockquote", "h1", "h2", "h3", "h4",
        "h5", "h6", "table", "ul", "ol", "form", "nav", "aside", "main",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        self._parts.append(data)

    def text(self) -> str:
        raw = html.unescape("".join(self._parts))
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        # A malformed page should still yield whatever parsed before the error.
        pass
    return parser.text()


# --------------------------------------------------------------------------- #
# web_fetch
# --------------------------------------------------------------------------- #

WEB_FETCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "Absolute http(s) URL to fetch.",
        },
        "max_chars": {
            "type": "integer",
            "description": f"Optional cap on returned text (default {MAX_OUTPUT_CHARS}).",
        },
        "timeout": {
            "type": "number",
            "description": f"Seconds before the request is abandoned (default {DEFAULT_TIMEOUT:.0f}, max {MAX_TIMEOUT:.0f}).",
        },
    },
    "required": ["url"],
}


def web_fetch(args: Dict[str, Any], context: ToolContext) -> str:
    url = str(args.get("url") or "").strip()
    try:
        url = validate_url(url)
    except ValueError as exc:
        return f"web_fetch refused: {exc}"

    timeout = _timeout(args)
    try:
        body, content_type, charset = _request(url, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            # urllib refuses a redirect to a non-http scheme before our handler
            # sees it; report that as the same "redirect refused" outcome.
            return f"web_fetch refused a redirect: {exc.reason}"
        return f"web_fetch {url} -> HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return f"web_fetch {url} failed: {exc.reason}"
    except (TimeoutError, socket.timeout):
        return f"web_fetch {url} timed out after {timeout:.0f}s"
    except ValueError as exc:  # redirect target rejected by the guard
        return f"web_fetch refused a redirect: {exc}"

    if b"\x00" in body[:4096]:
        return (
            f"web_fetch {url}: binary content ({content_type}, {len(body):,} bytes) "
            "is not shown as text"
        )

    text = _decode(body, charset)
    if content_type in ("text/html", "application/xhtml+xml"):
        text = _html_to_text(text)
    elif content_type == "application/json":
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError:
            pass

    try:
        cap = int(args.get("max_chars") or MAX_OUTPUT_CHARS)
    except (TypeError, ValueError):
        cap = MAX_OUTPUT_CHARS
    cap = max(200, min(cap, MAX_OUTPUT_CHARS))

    header = f"{url}\n[{content_type}, {len(body):,} bytes]"
    if not text.strip():
        return f"{header}\n(no readable text)"
    if len(text) > cap:
        text = text[:cap] + f"\n... [text truncated at {cap} chars]"
    return f"{header}\n\n{text}"


# --------------------------------------------------------------------------- #
# web_search
# --------------------------------------------------------------------------- #

WEB_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query, phrased as you would type it into a search engine.",
        },
        "max_results": {
            "type": "integer",
            "description": f"Number of results to return (default {DEFAULT_RESULTS}, max {MAX_RESULTS}).",
        },
    },
    "required": ["query"],
}

_SEARCH_BACKEND_ALIASES = {
    "ddg": "duckduckgo",
    "duck": "duckduckgo",
    "duckduckgo": "duckduckgo",
    "brave": "brave",
    "tavily": "tavily",
    "serp": "serpapi",
    "serpapi": "serpapi",
    "google": "serpapi",
}


def _search_backend() -> str:
    raw = (os.getenv("BRAHMA_SEARCH_BACKEND") or "").strip().lower()
    if not raw:
        try:
            from memory import config_manager

            raw = str(config_manager.get_setting("web_search_backend", "") or "").strip().lower()
        except Exception:
            raw = ""
    return _SEARCH_BACKEND_ALIASES.get(raw, "duckduckgo")


def _search_key(names: Tuple[str, ...]) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    try:
        from core.secret_store import load_api_keys

        data = load_api_keys()
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    for name in names:
        value = str(data.get(name) or "").strip()
        if value:
            return value
    return ""


def _unwrap_ddg(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = urllib.parse.urljoin(_DDG_HTML, href)
    parsed = urllib.parse.urlsplit(href)
    if parsed.path.rstrip("/").endswith("/l") or parsed.path.rstrip("/") == "/l":
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if target:
            return urllib.parse.unquote(target[0])
    return href


class _DDGParser(HTMLParser):
    """Pull ``result__a`` links and ``result__snippet`` text out of DDG HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self._current: Optional[Dict[str, str]] = None
        self._mode = ""
        self._buf: List[str] = []

    def _flush(self) -> None:
        if self._current is not None and self._current.get("title"):
            self.results.append(self._current)
        self._current = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag.lower() == "a" and "result__a" in classes:
            self._flush()
            self._current = {
                "title": "",
                "url": _unwrap_ddg(attrs.get("href") or ""),
                "snippet": "",
            }
            self._mode = "title"
            self._buf = []
        elif self._current is not None and "result__snippet" in classes:
            self._mode = "snippet"
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._mode:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._mode:
            return
        # Only the snippet anchor itself ends a snippet. A nested <b>/<em>
        # inside it must not flush early, or "First <b>snippet</b> text" loses
        # everything after the bold run.
        if self._mode == "title" and tag.lower() == "a":
            assert self._current is not None
            self._current["title"] = " ".join("".join(self._buf).split())
            self._mode = ""
            self._buf = []
        elif self._mode == "snippet" and tag.lower() == "a":
            assert self._current is not None
            self._current["snippet"] = " ".join("".join(self._buf).split())
            self._mode = ""
            self._buf = []

    def close(self) -> None:
        super().close()
        self._flush()


def _ddg_search(query: str, limit: int, timeout: float) -> List[Dict[str, str]]:
    url = _DDG_HTML + "?" + urllib.parse.urlencode({"q": query})
    body, _, charset = _request(
        url,
        timeout=timeout,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    parser = _DDGParser()
    parser.feed(_decode(body, charset))
    parser.close()
    return parser.results[:limit]


def _brave_search(query: str, limit: int, timeout: float) -> List[Dict[str, str]]:
    key = _search_key(("BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY", "brave_search_api_key"))
    if not key:
        raise LookupError("no Brave Search key configured")
    url = _BRAVE_API + "?" + urllib.parse.urlencode({"q": query, "count": limit})
    body, _, charset = _request(
        url,
        timeout=timeout,
        headers={"Accept": "application/json", "X-Subscription-Token": key},
    )
    data = json.loads(_decode(body, charset))
    rows = ((data or {}).get("web") or {}).get("results") or []
    return [
        {
            "title": str(row.get("title") or ""),
            "url": str(row.get("url") or ""),
            "snippet": str(row.get("description") or ""),
        }
        for row in rows
        if isinstance(row, dict)
    ][:limit]


def _tavily_search(query: str, limit: int, timeout: float) -> List[Dict[str, str]]:
    key = _search_key(("TAVILY_API_KEY", "tavily_api_key"))
    if not key:
        raise LookupError("no Tavily key configured")
    payload = json.dumps(
        {
            "api_key": key,
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
        }
    ).encode("utf-8")
    body, _, charset = _request(
        _TAVILY_API,
        timeout=timeout,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        data=payload,
    )
    data = json.loads(_decode(body, charset))
    rows = (data or {}).get("results") or []
    return [
        {
            "title": str(row.get("title") or ""),
            "url": str(row.get("url") or ""),
            "snippet": str(row.get("content") or ""),
        }
        for row in rows
        if isinstance(row, dict)
    ][:limit]


def _serpapi_search(query: str, limit: int, timeout: float) -> List[Dict[str, str]]:
    key = _search_key(("SERPAPI_API_KEY", "SERP_API_KEY", "serpapi_api_key"))
    if not key:
        raise LookupError("no SerpAPI key configured")
    url = _SERPAPI + "?" + urllib.parse.urlencode(
        {"engine": "google", "q": query, "num": limit, "api_key": key}
    )
    body, _, charset = _request(
        url, timeout=timeout, headers={"Accept": "application/json"}
    )
    data = json.loads(_decode(body, charset))
    rows = (data or {}).get("organic_results") or []
    return [
        {
            "title": str(row.get("title") or ""),
            "url": str(row.get("link") or ""),
            "snippet": str(row.get("snippet") or ""),
        }
        for row in rows
        if isinstance(row, dict)
    ][:limit]


_BACKENDS = {
    "duckduckgo": _ddg_search,
    "brave": _brave_search,
    "tavily": _tavily_search,
    "serpapi": _serpapi_search,
}


def web_search(args: Dict[str, Any], context: ToolContext) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "web_search needs a 'query'."
    try:
        limit = int(args.get("max_results") or DEFAULT_RESULTS)
    except (TypeError, ValueError):
        limit = DEFAULT_RESULTS
    limit = max(1, min(limit, MAX_RESULTS))
    timeout = _timeout(args)

    backend = _search_backend()
    note = ""
    try:
        rows = _BACKENDS[backend](query, limit, timeout)
    except LookupError as exc:
        # A keyed backend with no key is a configuration gap, not a failed
        # turn: answer from the keyless backend and say what happened.
        note = f" ({backend} unavailable: {exc}; used DuckDuckGo)"
        backend = "duckduckgo"
        try:
            rows = _ddg_search(query, limit, timeout)
        except Exception as inner:  # noqa: BLE001 - report, do not abort the turn
            return f"web_search failed: {type(inner).__name__}: {inner}"
    except urllib.error.HTTPError as exc:
        return f"web_search ({backend}) -> HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return f"web_search ({backend}) failed: {exc.reason}"
    except (TimeoutError, socket.timeout):
        return f"web_search ({backend}) timed out after {timeout:.0f}s"
    except (ValueError, json.JSONDecodeError) as exc:
        return f"web_search ({backend}) returned unusable data: {exc}"

    if not rows:
        return f'No search results for "{query}" ({backend}{note}).'

    lines = [f'Search results for "{query}" ({backend}{note}):']
    for index, row in enumerate(rows, 1):
        title = row.get("title") or "(untitled)"
        lines.append(f"{index}. {title}")
        if row.get("url"):
            lines.append(f"   {row['url']}")
        if row.get("snippet"):
            lines.append(f"   {row['snippet']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #

SPECS: Tuple[ToolSpec, ...] = (
    ToolSpec(
        name="web_search",
        description=(
            "Search the public web and return titles, URLs and snippets. "
            "Use web_fetch on a result URL when you need the full page."
        ),
        parameters=WEB_SEARCH_SCHEMA,
        tool_class="network",
        handler=web_search,
        tags=("web", "search"),
        max_output_chars=MAX_OUTPUT_CHARS,
    ),
    ToolSpec(
        name="web_fetch",
        description=(
            "Fetch one public http(s) page and return its readable text. "
            "HTML is stripped to text; JSON is pretty-printed; binary is refused."
        ),
        parameters=WEB_FETCH_SCHEMA,
        tool_class="network",
        handler=web_fetch,
        tags=("web", "fetch"),
        max_output_chars=MAX_OUTPUT_CHARS,
    ),
)


def register_all() -> None:
    """Idempotent registration, safe to call from every entry point."""
    from . import spec as spec_module

    for spec in SPECS:
        if spec_module.get(spec.name) is None:
            register(spec)


register_all()


def is_web_tool(name: str) -> bool:
    return any(spec.name == name for spec in SPECS)
