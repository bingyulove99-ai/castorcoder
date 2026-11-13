"""File creation / overwrite."""

from ..fsutil import atomic_write_text
from .base import WorkspaceTool
from .edit import _DEFAULT_TRACKER, ChangeTracker, _changed_files


class WriteFileTool(WorkspaceTool):
    name = "write_file"
    description = (
        "Create a new file or completely overwrite an existing one. "
        "For small edits to existing files, prefer edit_file instead."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path for the file",
            },
            "content": {
                "type": "string",
                "description": "Full file content to write",
            },
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    }
    risk = "write"
    parallel_safe = False
    timeout_s = 30.0

    def __init__(self, workspace, tracker: ChangeTracker | None = None):
        super().__init__(workspace)
        self.tracker = tracker or _DEFAULT_TRACKER

    def execute(self, file_path: str, content: str) -> str:
        try:
            p = self.workspace.resolve(file_path)
            before = None
            if p.is_file():
                # Preserve CRLF so /undo restores the original bytes exactly.
                with p.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                    before = handle.read()
            atomic_write_text(p, content)
            self.tracker.record(p, before, content)
            _changed_files.add(str(p))
            n_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return f"Wrote {n_lines} lines to {file_path}"
        except Exception as e:
            return f"Error: {e}"
