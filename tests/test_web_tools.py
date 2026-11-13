"""Web tools: SSRF guard and HTML-to-text conversion, without network access."""

from __future__ import annotations

from castorcoder.tools import web
from castorcoder.tools.web import WebFetchTool, WebSearchTool, _html_to_text


def test_fetch_refuses_private_and_non_http_urls():
    tool = WebFetchTool()
    assert "only http" in tool.execute("file:///etc/passwd")
    assert "local, private" in tool.execute("http://127.0.0.1/admin")
    assert "local, private" in tool.execute("http://localhost:8080/")
    assert "credentials" in tool.execute("https://user:pass@example.com/")


def test_html_conversion_keeps_text_and_title():
    text, title = _html_to_text(
        "<html><head><title>Release notes</title>"
        "<style>body{color:red}</style></head>"
        "<body><h1>Version 2</h1><script>alert(1)</script>"
        "<p>Fixed the <b>loop</b> guard.</p></body></html>"
    )
    assert title == "Release notes"
    assert "Version 2" in text
    assert "Fixed the loop guard." in text
    assert "alert(1)" not in text
    assert "color:red" not in text


def test_search_reports_a_useful_hint_when_the_backend_returns_nothing(monkeypatch):
    monkeypatch.delenv("CASTORCODER_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("WAKU_SEARCH_API_KEY", raising=False)
    monkeypatch.setattr(web, "_duckduckgo", lambda query, n: [])
    output = WebSearchTool().execute("castorcoder")
    assert "No results" in output
    assert "CASTORCODER_SEARCH_API_KEY" in output


def test_search_formats_results(monkeypatch):
    monkeypatch.setattr(
        web, "_duckduckgo",
        lambda query, n: [("Title", "Snippet", "https://example.com")],
    )
    output = WebSearchTool().execute("q", max_results=1)
    assert "Title" in output and "https://example.com" in output
    assert "DuckDuckGo" in output
