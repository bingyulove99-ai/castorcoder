"""Code-aware chunking for the local retrieval index.

Chunks keep a stable ``path:start-end`` location and, where possible, a symbol
name.  Python is split with the standard AST; Markdown is split by heading; all
other text falls back to a sliding line window.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

MAX_CHUNK_CHARS = 4_000
_WINDOW_LINES = 60
_WINDOW_OVERLAP = 10
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit of a file."""

    path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    content: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


def chunk_file(
    path: str | Path,
    *,
    rel_path: str,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[Chunk]:
    """Read ``path`` and return chunks labelled with ``rel_path``."""

    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    suffix = target.suffix.lower()
    if suffix == ".py":
        return _chunk_python(text, rel_path, max_chars)
    if suffix in {".md", ".markdown", ".mdx", ".rst"}:
        return _chunk_markdown(text, rel_path, max_chars)
    return _chunk_windows(text, rel_path, max_chars)


def _chunk_python(text: str, rel_path: str, max_chars: int) -> list[Chunk]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _chunk_windows(text, rel_path, max_chars)

    lines = text.splitlines()
    if not lines:
        return []

    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    chunks: list[Chunk] = []

    first_line = _node_start(nodes[0]) if nodes else len(lines) + 1
    if first_line > 1:
        header = "\n".join(lines[: first_line - 1]).strip()
        if header:
            chunks.append(
                Chunk(
                    path=rel_path,
                    symbol="<module>",
                    kind="module",
                    start_line=1,
                    end_line=first_line - 1,
                    content=header,
                )
            )

    for node in nodes:
        start = _node_start(node)
        end = getattr(node, "end_lineno", None) or _last_line(node, len(lines))
        body = "\n".join(lines[start - 1 : end])
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        symbol = node.name

        if kind == "class" and len(body) > max_chars:
            chunks.extend(_split_class(lines, node, rel_path, max_chars))
        else:
            chunks.extend(
                _split_large(
                    body,
                    rel_path,
                    symbol,
                    kind,
                    start,
                    max_chars,
                )
            )

    return _dedupe(chunks)


def _split_class(
    lines: list[str],
    node: ast.ClassDef,
    rel_path: str,
    max_chars: int,
) -> list[Chunk]:
    methods = [
        child
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not methods:
        start = _node_start(node)
        end = getattr(node, "end_lineno", None) or _last_line(node, len(lines))
        return _split_large(
            "\n".join(lines[start - 1 : end]), rel_path, node.name, "class", start, max_chars
        )

    chunks: list[Chunk] = []
    header_end = _node_start(methods[0]) - 1
    header_start = _node_start(node)
    header = "\n".join(lines[header_start - 1 : header_end]).strip()
    if header:
        chunks.append(
            Chunk(
                path=rel_path,
                symbol=node.name,
                kind="class",
                start_line=header_start,
                end_line=header_end,
                content=header,
            )
        )

    for method in methods:
        start = _node_start(method)
        end = getattr(method, "end_lineno", None) or _last_line(method, len(lines))
        body = "\n".join(lines[start - 1 : end])
        chunks.extend(
            _split_large(
                body,
                rel_path,
                f"{node.name}.{method.name}",
                "method",
                start,
                max_chars,
            )
        )
    return chunks


def _chunk_markdown(text: str, rel_path: str, max_chars: int) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []

    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        match = _HEADING.match(line)
        if match:
            headings.append((index, match.group(2)))
    if not headings:
        return _chunk_windows(text, rel_path, max_chars)

    chunks: list[Chunk] = []
    if headings[0][0] > 1:
        preamble = "\n".join(lines[: headings[0][0] - 1]).strip()
        if preamble:
            chunks.append(
                Chunk(
                    path=rel_path,
                    symbol="<preamble>",
                    kind="markdown",
                    start_line=1,
                    end_line=headings[0][0] - 1,
                    content=preamble,
                )
            )

    for position, (start, title) in enumerate(headings):
        end = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start - 1 : end])
        chunks.extend(
            _split_large(body, rel_path, title, "markdown", start, max_chars)
        )
    return _dedupe(chunks)


def _chunk_windows(text: str, rel_path: str, max_chars: int) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[Chunk] = []
    step = max(1, _WINDOW_LINES - _WINDOW_OVERLAP)
    for start_index in range(0, len(lines), step):
        window = lines[start_index : start_index + _WINDOW_LINES]
        if not any(line.strip() for line in window):
            continue
        start_line = start_index + 1
        end_line = start_index + len(window)
        body = "\n".join(window)
        chunks.extend(
            _split_large(body, rel_path, "", "text", start_line, max_chars)
        )
        if end_line >= len(lines):
            break
    return _dedupe(chunks)


def _split_large(
    body: str,
    rel_path: str,
    symbol: str,
    kind: str,
    start_line: int,
    max_chars: int,
) -> list[Chunk]:
    if len(body) <= max_chars:
        return [
            Chunk(
                path=rel_path,
                symbol=symbol,
                kind=kind,
                start_line=start_line,
                end_line=start_line + max(0, len(body.splitlines()) - 1),
                content=body,
            )
        ]

    lines = body.splitlines()
    chunks: list[Chunk] = []
    offset = 0
    part = 1
    while offset < len(lines):
        current: list[str] = []
        current_chars = 0
        cursor = offset
        while cursor < len(lines) and current_chars < max_chars:
            line = lines[cursor]
            current.append(line)
            current_chars += len(line) + 1
            cursor += 1
        if not current:
            break
        label = symbol if part == 1 else f"{symbol}#{part}" if symbol else ""
        chunks.append(
            Chunk(
                path=rel_path,
                symbol=label,
                kind=kind,
                start_line=start_line + offset,
                end_line=start_line + offset + len(current) - 1,
                content="\n".join(current),
            )
        )
        part += 1
        offset = cursor
    return chunks


def _node_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", None) or []
    if decorators:
        return min(getattr(item, "lineno", node.lineno) for item in decorators)
    return node.lineno


def _last_line(node: ast.AST, fallback: int) -> int:
    return getattr(node, "end_lineno", None) or fallback


def _dedupe(chunks: list[Chunk]) -> list[Chunk]:
    seen: set[tuple[str, int, int]] = set()
    result: list[Chunk] = []
    for chunk in chunks:
        key = (chunk.path, chunk.start_line, chunk.end_line)
        if key in seen or not chunk.content.strip():
            continue
        seen.add(key)
        result.append(chunk)
    return result
