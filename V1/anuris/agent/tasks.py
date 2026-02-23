import json
from pathlib import Path
from typing import Iterable, List, Optional


class PersistentTaskManager:
    """File-backed task board inspired by learn-claude-code s07."""

    VALID_STATUSES = {"pending", "in_progress", "completed"}

    def __init__(self, tasks_dir: Path):
        self.tasks_dir = tasks_dir.resolve()
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def create(self, subject: str, description: str = "") -> str:
        subject = subject.strip()
        if not subject:
            raise ValueError("subject is required")

        task = {
            "id": self._next_id(),
            "subject": subject,
            "description": description.strip(),
            "status": "pending",
            "owner": "",
            "blockedBy": [],
            "blocks": [],
        }
        self._save(task)
        return json.dumps(task, indent=2)

    def get(self, task_id: int) -> str:
        return json.dumps(self._load(task_id), indent=2)

    def update(
        self,
        task_id: int,
        status: Optional[str] = None,
        add_blocked_by: Optional[List[int]] = None,
        add_blocks: Optional[List[int]] = None,
        owner: Optional[str] = None,
    ) -> str:
        task = self._load(task_id)

        if status:
            normalized = status.strip().lower()
            if normalized == "deleted":
                self._task_path(task_id).unlink(missing_ok=True)
                return f"Task {task_id} deleted"
            if normalized not in self.VALID_STATUSES:
                raise ValueError(f"Invalid status: {status}")
            task["status"] = normalized
            if normalized == "completed":
                self._clear_dependency(task_id)

        if owner is not None:
            task["owner"] = owner.strip()

        if add_blocked_by:
            ids = self._normalize_task_ids(add_blocked_by)
            task["blockedBy"] = sorted(set(task.get("blockedBy", []) + ids))

        if add_blocks:
            ids = self._normalize_task_ids(add_blocks)
            task["blocks"] = sorted(set(task.get("blocks", []) + ids))
            for blocked_id in ids:
                self._add_blocked_by(blocked_id, task_id)

        self._save(task)
        return json.dumps(task, indent=2)

    def list_all(self) -> str:
        tasks = [json.loads(path.read_text()) for path in self._task_paths()]
        if not tasks:
            return "No tasks."

        lines = []
        for task in tasks:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }.get(task.get("status"), "[?]")
            owner = f" @{task['owner']}" if task.get("owner") else ""
            blocked = f" (blocked by: {task['blockedBy']})" if task.get("blockedBy") else ""
            lines.append(f"{marker} #{task['id']}: {task.get('subject', '')}{owner}{blocked}")
        return "\n".join(lines)

    def _task_paths(self) -> List[Path]:
        paths = []
        for path in self.tasks_dir.glob("task_*.json"):
            try:
                self._extract_task_id(path)
            except ValueError:
                continue
            paths.append(path)
        return sorted(paths, key=self._extract_task_id)

    def _next_id(self) -> int:
        ids = [self._extract_task_id(path) for path in self._task_paths()]
        return (max(ids) + 1) if ids else 1

    @staticmethod
    def _extract_task_id(path: Path) -> int:
        stem = path.stem
        if not stem.startswith("task_"):
            raise ValueError(f"Invalid task filename: {path.name}")
        return int(stem.split("_", 1)[1])

    def _task_path(self, task_id: int) -> Path:
        return self.tasks_dir / f"task_{int(task_id)}.json"

    def _load(self, task_id: int) -> dict:
        path = self._task_path(task_id)
        if not path.exists():
            raise ValueError(f"Task {task_id} not found")
        return json.loads(path.read_text())

    def _save(self, task: dict) -> None:
        self._task_path(int(task["id"])).write_text(json.dumps(task, indent=2))

    @staticmethod
    def _normalize_task_ids(task_ids: Iterable[int]) -> List[int]:
        normalized = []
        for task_id in task_ids:
            normalized.append(int(task_id))
        return normalized

    def _clear_dependency(self, completed_id: int) -> None:
        for path in self._task_paths():
            task = json.loads(path.read_text())
            blocked_by = task.get("blockedBy", [])
            if completed_id in blocked_by:
                task["blockedBy"] = [task_id for task_id in blocked_by if task_id != completed_id]
                self._save(task)

    def _add_blocked_by(self, task_id: int, blocker_id: int) -> None:
        try:
            task = self._load(task_id)
        except ValueError:
            return
        blocked_by = task.get("blockedBy", [])
        if blocker_id not in blocked_by:
            task["blockedBy"] = sorted(blocked_by + [blocker_id])
            self._save(task)
