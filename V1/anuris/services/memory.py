from __future__ import annotations

from pathlib import Path

from ..runtime.memory import ProjectMemoryStore


class MemoryManager:
    """Simple local memory file manager for the active workspace."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.store = ProjectMemoryStore(self.workspace_root)
        self.memory_path = self.workspace_root / ".anuris" / "memory" / "workspace.md"

    def read(self) -> str:
        return self.store.read_workspace()

    def append(self, text: str) -> str:
        return self.store.append_workspace(text)

    def read_session(self, session_id: str) -> str:
        return self.store.read_session(session_id)

    def append_session(self, session_id: str, text: str) -> str:
        return self.store.append_session(session_id, text)

    def clear(self) -> None:
        self.store.clear_workspace()
