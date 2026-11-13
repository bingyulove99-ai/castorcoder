from pathlib import Path


class Workspace:
    """Resolve all tool paths against one stable project root."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def validate_path(self, candidate):
        """Return True only when candidate stays inside the workspace."""
        resolved = Path(candidate).resolve()
        return resolved.is_relative_to(self.root)
