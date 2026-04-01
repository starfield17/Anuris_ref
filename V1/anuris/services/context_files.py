from __future__ import annotations

from pathlib import Path
from typing import List


class ContextFileTracker:
    """Track files recently brought into the active session context."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.paths: list[Path] = []

    def record(self, raw_path: str | Path) -> None:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (self.workspace_root / path).resolve()
        else:
            path = path.resolve()
        if path in self.paths:
            self.paths.remove(path)
        self.paths.append(path)
        self.paths = self.paths[-100:]

    def list_paths(self) -> List[Path]:
        return list(self.paths)

    def render(self) -> str:
        if not self.paths:
            return "No files in context."
        lines = []
        for path in self.paths:
            try:
                label = str(path.relative_to(self.workspace_root))
            except ValueError:
                label = str(path)
            lines.append(f"- {label}")
        return "\n".join(lines)
