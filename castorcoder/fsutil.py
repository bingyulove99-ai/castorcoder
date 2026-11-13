"""Small filesystem helpers shared by file-mutating tools."""

from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path


def atomic_write_text(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """Write text through a unique temporary file and an atomic replace.

    A crash can leave a stale ``.tmp`` file behind, but it can never leave a
    half-written destination file.  ``os.replace`` is atomic on the same
    filesystem on both POSIX and Windows.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding=encoding, newline="")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            with contextlib.suppress(OSError):
                temporary.unlink()
