"""Query-string parsing."""

from __future__ import annotations


def parse_query(query: str) -> dict[str, str]:
    """Parse a query string such as ``page=2&q=hello`` into a mapping.

    Pairs without ``=`` map to an empty string. Later duplicates win.
    """
    result: dict[str, str] = {}
    for pair in query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        result[key] = value
    return result
