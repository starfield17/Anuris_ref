from __future__ import annotations

from pathlib import Path
from typing import List


class ContextFileTracker:
    """Track files recently brought into the active session context."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.paths: list[Path] = []
        self.added_dirs: list[Path] = []

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

    def add_dir(self, raw_path: str | Path) -> Path:
        path = self._resolve(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {raw_path}")
        if not path.is_dir():
            raise ValueError(f"Not a directory: {raw_path}")
        if path in self.added_dirs:
            self.added_dirs.remove(path)
        self.added_dirs.append(path)
        self.added_dirs = self.added_dirs[-50:]
        return path

    def remove_dir(self, raw_path: str | Path) -> bool:
        path = self._resolve(raw_path)
        if path in self.added_dirs:
            self.added_dirs.remove(path)
            return True
        return False

    def clear_files(self) -> None:
        self.paths.clear()

    def clear_dirs(self) -> None:
        self.added_dirs.clear()

    def clear_all(self) -> None:
        self.clear_files()
        self.clear_dirs()

    def list_dirs(self) -> List[Path]:
        return list(self.added_dirs)

    def snapshot(self) -> dict[str, int]:
        return {
            "files": len(self.paths),
            "added_dirs": len(self.added_dirs),
        }

    def render_dirs(self) -> str:
        if not self.added_dirs:
            return "No added directories."
        lines = []
        for path in self.added_dirs:
            lines.append(f"- {self._label(path)}")
        return "\n".join(lines)

    def render(self) -> str:
        if not self.paths and not self.added_dirs:
            return "No files or directories in context."
        lines = []
        if self.added_dirs:
            lines.append("Added directories:")
            for path in self.added_dirs:
                lines.append(f"- {self._label(path)}")
        if self.paths:
            if lines:
                lines.append("")
            lines.append("Files in context:")
            for path in self.paths:
                lines.append(f"- {self._label(path)}")
        return "\n".join(lines)

    def _resolve(self, raw_path: str | Path) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (self.workspace_root / path).resolve()
        else:
            path = path.resolve()
        return path

    def _label(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace_root))
        except ValueError:
            return str(path)
