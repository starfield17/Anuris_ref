from __future__ import annotations

from pathlib import Path


class MemoryManager:
    """Simple local memory file manager for the active workspace."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.memory_path = self.workspace_root / ".anuris" / "memory.md"
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> str:
        if not self.memory_path.exists():
            return "No memory saved."
        content = self.memory_path.read_text(encoding="utf-8").strip()
        return content or "No memory saved."

    def append(self, text: str) -> str:
        value = text.strip()
        if not value:
            raise ValueError("memory text is required")
        existing = ""
        if self.memory_path.exists():
            existing = self.memory_path.read_text(encoding="utf-8").rstrip()
        updated = f"{existing}\n- {value}".strip() + "\n"
        self.memory_path.write_text(updated, encoding="utf-8")
        return value

    def clear(self) -> None:
        if self.memory_path.exists():
            self.memory_path.unlink()
