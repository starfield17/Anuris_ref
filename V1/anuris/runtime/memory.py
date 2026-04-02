from __future__ import annotations

from pathlib import Path


class ProjectMemoryStore:
    """Workspace-scoped memory paths with traversal protection."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.base_dir = self.workspace_root / ".anuris" / "memory"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def read_workspace(self) -> str:
        return self._read_text(self.base_dir / "workspace.md")

    def append_workspace(self, text: str) -> str:
        return self._append_text(self.base_dir / "workspace.md", text)

    def read_session(self, session_id: str) -> str:
        return self._read_text(self._session_path(session_id))

    def append_session(self, session_id: str, text: str) -> str:
        return self._append_text(self._session_path(session_id), text)

    def clear_workspace(self) -> None:
        path = self.base_dir / "workspace.md"
        if path.exists():
            path.unlink()

    def _session_path(self, session_id: str) -> Path:
        return self._safe(self.base_dir / "sessions" / f"{session_id}.md")

    def _read_text(self, path: Path) -> str:
        safe = self._safe(path)
        if not safe.exists():
            return "No memory saved."
        content = safe.read_text(encoding="utf-8").strip()
        return content or "No memory saved."

    def _append_text(self, path: Path, text: str) -> str:
        value = str(text or "").strip()
        if not value:
            raise ValueError("memory text is required")
        safe = self._safe(path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        current = safe.read_text(encoding="utf-8").rstrip() if safe.exists() else ""
        safe.write_text(f"{current}\n- {value}".strip() + "\n", encoding="utf-8")
        return value

    def _safe(self, path: Path) -> Path:
        resolved_parent = path.parent.resolve()
        resolved_parent.relative_to(self.base_dir.resolve())
        candidate = resolved_parent / path.name
        candidate.resolve().parent.relative_to(self.base_dir.resolve())
        return candidate
