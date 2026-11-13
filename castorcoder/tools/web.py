"""Web search and page fetching — the two read-only tools CastorCoder lacked.

Waku ships a zero-dependency ``search_web`` tool (DuckDuckGo HTML, with Tavily
when a key is present).  A coding agent needs the same reach for release
notes, API documentation and error messages, plus a way to read a page it
finds.  Both tools return plain text; neither parses HTML for the model.
"""

from __future__ import annotations

import contextlib
import html
import ipaddress
import json
import os
import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .base import Tool

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_BYTES = 2_000_000


# --------------------------------------------------------------------------
# HTML -> text
# --------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "canvas", "template", "iframe"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "pre", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            return
        if self._in_title:
            self.title += text
        else:
            self.parts.append(text)

    def text(self) -> str:
        joined = "".join(self.parts)
        lines = [line.strip() for line in joined.splitlines()]
        # Drop long runs of blank lines and repeated navigation-ish noise.
        compact: list[str] = []
        for line in lines:
            if line:
                compact.append(line)
            elif compact and compact[-1] != "":
                compact.append("")
        return "\n".join(compact).strip()


def _html_to_text(raw: str) -> tuple[str, str]:
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        # A malformed document still yields the regex fallback.
        text = re.sub(r"<[^>]+>", " ", raw)
        return html.unescape(re.sub(r"\s+", " ", text)).strip(), ""
    return parser.text(), re.sub(r"\s+", " ", parser.title).strip()


# --------------------------------------------------------------------------
# SSRF guard
# --------------------------------------------------------------------------
def _is_public_host(host: str) -> bool:
    if not host:
        return False
    host = host.strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return False
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _safe_url(url: str) -> tuple[str, urllib.parse.ParseResult] | str:
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return "Error: invalid URL"
    if parsed.scheme not in {"http", "https"}:
        return "Error: only http:// and https:// URLs are allowed"
    if parsed.username or parsed.password:
        return "Error: URLs with embedded credentials are not allowed"
    if not parsed.hostname:
        return "Error: URL has no host"
    if not _is_public_host(parsed.hostname):
        return "Error: refusing to fetch a local, private or unresolvable host"
    return url.strip(), parsed


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------
class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch a public http(s) URL and return its readable text (HTML is "
        "converted to plain text). Use for documentation, changelogs, issue "
        "threads and error messages. Private/local addresses are refused."
    )
    risk = "read"
    parallel_safe = True
    timeout_s = 45.0
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public http(s) URL to read"},
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default 12000)",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def execute(self, url: str, max_chars: int = 12_000) -> str:
        checked = _safe_url(url)
        if isinstance(checked, str):
            return checked
        target, parsed = checked
        max_chars = max(500, min(int(max_chars), 60_000))
        request = urllib.request.Request(
            target,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.5",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                raw = response.read(_MAX_BYTES + 1)
                final_url = response.geturl()
        except Exception as exc:
            return f"Error: could not fetch {parsed.netloc}: {type(exc).__name__}: {exc}"

        if len(raw) > _MAX_BYTES:
            raw = raw[: _MAX_BYTES]
            truncated_bytes = True
        else:
            truncated_bytes = False

        charset = "utf-8"
        match = re.search(r"charset=([\w.-]+)", content_type)
        if match:
            charset = match.group(1)
        try:
            text = raw.decode(charset, "replace")
        except (LookupError, UnicodeError):
            text = raw.decode("utf-8", "replace")

        title = ""
        if "html" in content_type or text.lstrip().lower().startswith("<!doctype html"):
            text, title = _html_to_text(text)
        elif "json" in content_type:
            with contextlib.suppress(ValueError):
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)

        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n... (truncated, {len(text):,} chars total)"
        header = [f"URL: {final_url}"]
        if title:
            header.append(f"Title: {title}")
        if truncated_bytes:
            header.append("(response body capped at 2 MB)")
        return "\n".join(header) + "\n\n" + (text or "(no readable text)")


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the public web and return the top results (title, snippet, URL). "
        "Use for current facts, release notes and documentation you do not know."
    )
    risk = "read"
    parallel_safe = True
    timeout_s = 30.0
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {
                "type": "integer",
                "description": "How many results (default 5, max 10)",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def execute(self, query: str, max_results: int = 5) -> str:
        max_results = max(1, min(int(max_results), 10))
        key = (
            os.getenv("CASTORCODER_SEARCH_API_KEY")
            or os.getenv("TAVILY_API_KEY")
            or os.getenv("WAKU_SEARCH_API_KEY")
        )
        try:
            results = (
                _tavily(query, key, max_results)
                if key
                else _duckduckgo(query, max_results)
            )
        except Exception as exc:
            if key:
                return f"Web search failed: {type(exc).__name__}: {exc}"
            results = []
        if not results:
            hint = (
                "No results. Set CASTORCODER_SEARCH_API_KEY (Tavily) for reliable "
                "search; DuckDuckGo's free endpoint often blocks automated requests."
            )
            return hint
        engine = "Tavily" if key else "DuckDuckGo"
        lines = [f"Web results for '{query}' (via {engine}):"]
        for index, (title, snippet, link) in enumerate(results, 1):
            lines.append(f"{index}. {title}\n   {snippet}\n   {link}")
        return "\n".join(lines)


def _tavily(query: str, key: str, max_results: int) -> list[tuple[str, str, str]]:
    body = json.dumps(
        {
            "api_key": key,
            "query": query,
            "max_results": max_results,
            "include_answer": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read())
    return [
        (
            str(item.get("title", "")),
            str(item.get("content", ""))[:400],
            str(item.get("url", "")),
        )
        for item in data.get("results", [])
        if isinstance(item, dict)
    ]


def _strip_tags(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _duckduckgo(query: str, max_results: int) -> list[tuple[str, str, str]]:
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        page = response.read().decode("utf-8", "ignore")
    links = re.findall(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.DOTALL)
    snippets = re.findall(r'result__snippet"[^>]*>(.*?)</a>', page, re.DOTALL)
    results = []
    for index, (href, title) in enumerate(links[:max_results]):
        target = href
        match = re.search(r"uddg=([^&]+)", href)
        if match:
            target = urllib.parse.unquote(match.group(1))
        results.append(
            (
                _strip_tags(title),
                _strip_tags(snippets[index]) if index < len(snippets) else "",
                target,
            )
        )
    return results
