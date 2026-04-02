from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List


class WorktreeManager:
    """Inspect and switch git worktrees for the current repository."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def set_workspace_root(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def identity(self) -> str:
        return str(self.workspace_root)

    def list_worktrees(self) -> List[Dict[str, str]]:
        try:
            completed = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except Exception:
            return [{"path": str(self.workspace_root), "branch": "", "head": "", "current": "true"}]

        entries: List[Dict[str, str]] = []
        current: Dict[str, str] = {}
        for raw_line in completed.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                if current:
                    current["current"] = str(Path(current.get("path", "")).resolve() == self.workspace_root)
                    entries.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        if current:
            current["current"] = str(Path(current.get("path", "")).resolve() == self.workspace_root)
            entries.append(current)
        return entries or [{"path": str(self.workspace_root), "branch": "", "head": "", "current": "true"}]

    def render(self) -> str:
        return "\n".join(
            f"- {item.get('path')} {('(current)' if item.get('current') == 'true' else '')} {item.get('branch', '')}".rstrip()
            for item in self.list_worktrees()
        )

    def resolve_target(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (self.workspace_root / path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Worktree path not found: {raw_path}")
        if not path.is_dir():
            raise ValueError(f"Worktree target is not a directory: {raw_path}")
        return path
