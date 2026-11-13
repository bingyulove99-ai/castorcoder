"""Code chunking, incremental indexing and hybrid retrieval tests."""

from __future__ import annotations

from castorcoder.index import CodeIndex, HashingEmbedder, chunk_file
from castorcoder.index.retriever import hybrid_search
from castorcoder.tools.search_code import SearchCodeTool


def test_python_chunking_preserves_symbols_and_lines(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text(
        "import os\n\n\n"
        "def greet(name):\n"
        "    return 'hi ' + name\n\n\n"
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n"
    )
    chunks = chunk_file(source, rel_path="sample.py")
    symbols = {chunk.symbol for chunk in chunks}
    assert "<module>" in symbols
    assert "greet" in symbols
    assert "Calculator" in symbols
    greet = next(chunk for chunk in chunks if chunk.symbol == "greet")
    assert greet.start_line == 4
    assert "return 'hi ' + name" in greet.content


def test_markdown_chunking_by_heading(tmp_path):
    source = tmp_path / "guide.md"
    source.write_text("# Title\n\nIntro.\n\n## Install\n\nRun pip install.\n")
    chunks = chunk_file(source, rel_path="guide.md")
    symbols = {chunk.symbol for chunk in chunks}
    assert "Title" in symbols
    assert "Install" in symbols
    install = next(chunk for chunk in chunks if chunk.symbol == "Install")
    assert "pip install" in install.content


def test_embedder_is_deterministic_and_discriminative():
    embedder = HashingEmbedder(dim=64)
    first, second, different = embedder.embed(["workspace path", "workspace path", "memory store"])
    assert first == second
    assert first != different


def test_incremental_index_update(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "app.py").write_text("def run():\n    return 1\n")
    index = CodeIndex(workspace, db_path=tmp_path / "db" / "index.db", auto_build=False)

    first = index.update()
    assert first.files_indexed == 1
    assert first.chunks >= 1

    second = index.update()
    assert second.files_indexed == 0
    assert second.files_unchanged == 1

    (workspace / "app.py").write_text("def run():\n    return 2\n")
    third = index.update()
    assert third.files_indexed == 1

    (workspace / "app.py").unlink()
    fourth = index.update()
    assert fourth.files_removed == 1


def test_hybrid_search_and_tool(tmp_path):
    (tmp_path / "workspace.py").write_text(
        "class Workspace:\n"
        "    def validate_path(self, candidate):\n"
        "        return candidate\n"
    )
    index = CodeIndex(tmp_path, db_path=tmp_path / "db" / "index.db", auto_build=False)
    index.update()
    hits = hybrid_search(index.store, "validate workspace path", k=3)
    assert hits
    assert hits[0].chunk.path == "workspace.py"
    assert "bm25" in hits[0].sources and "vector" in hits[0].sources

    output = SearchCodeTool(index).execute(query="validate path", k=2)
    assert "workspace.py:" in output

    bm25_only = hybrid_search(index.store, "validate workspace path", k=3, mode="bm25")
    assert bm25_only and all(hit.sources == ("bm25",) for hit in bm25_only)
    excluded = hybrid_search(
        index.store,
        "validate workspace path",
        k=3,
        exclude_prefixes=("workspace.py",),
    )
    assert excluded == []


def test_index_skips_binary_and_noise(tmp_path):
    (tmp_path / "good.py").write_text("value = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "noise.py").write_text("value = 2\n")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00\x00")
    index = CodeIndex(tmp_path, db_path=tmp_path / "db" / "index.db", auto_build=False)
    stats = index.update()
    assert stats.files_indexed == 1
    assert index.store.stats()["chunks"] >= 1


def test_large_function_and_class_are_split(tmp_path):
    body = "\n".join(f"    value_{index} = {index}" for index in range(500))
    source = tmp_path / "big.py"
    source.write_text(f"def big():\n{body}\n\nclass Large:\n" + "\n".join(
        f"    def method_{index}(self):\n        return {index}" for index in range(100)
    ) + "\n")
    chunks = chunk_file(source, rel_path="big.py", max_chars=1_000)
    big_chunks = [chunk for chunk in chunks if chunk.symbol.startswith("big")]
    large_chunks = [chunk for chunk in chunks if chunk.symbol.startswith("Large.method")]
    assert len(big_chunks) > 1
    assert any("#" in chunk.symbol for chunk in big_chunks)
    assert large_chunks


def test_generic_window_markdown_preamble_and_syntax_fallback(tmp_path):
    generic = tmp_path / "data.txt"
    generic.write_text("\n".join(f"line {index}" for index in range(150)))
    windows = chunk_file(generic, rel_path="data.txt", max_chars=500)
    assert len(windows) > 1

    markdown = tmp_path / "notes.md"
    markdown.write_text("Preamble text.\n\n# Heading\nBody.\n")
    chunks = chunk_file(markdown, rel_path="notes.md")
    assert any(chunk.symbol == "<preamble>" for chunk in chunks)

    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n    pass\n")
    fallback = chunk_file(broken, rel_path="broken.py")
    assert fallback and fallback[0].kind == "text"


def test_store_clear_and_missing_chunks(tmp_path):
    (tmp_path / "app.py").write_text("def run():\n    return 1\n")
    index = CodeIndex(tmp_path, db_path=tmp_path / "db" / "index.db", auto_build=False)
    index.update()
    assert index.store.get_chunk_map([]) == {}
    assert index.store.get_chunks(["missing"]) == []
    index.clear()
    assert index.store.stats()["chunks"] == 0
    assert index.store.known_file_hashes() == {}
