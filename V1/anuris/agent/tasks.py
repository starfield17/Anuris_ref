import json
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..runtime.events import utc_timestamp


class PersistentTaskManager:
    """File-backed task board inspired by learn-claude-code s07."""

    VALID_STATUSES = {"pending", "in_progress", "completed", "failed", "cancelled"}

    def __init__(self, tasks_dir: Path):
        self.tasks_dir = tasks_dir.resolve()
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def create(
        self,
        subject: str,
        description: str = "",
        *,
        task_type: str = "agent",
        workspace_root: str = "",
        worktree_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        subject = subject.strip()
        if not subject:
            raise ValueError("subject is required")

        with self._lock:
            task = {
                "id": self._next_id(),
                "subject": subject,
                "description": description.strip(),
                "task_type": task_type.strip() or "agent",
                "status": "pending",
                "owner": "",
                "blockedBy": [],
                "blocks": [],
                "run_id": "",
                "artifact_dir": "",
                "workspace_root": workspace_root,
                "worktree_id": worktree_id or workspace_root,
                "created_at": utc_timestamp(),
                "updated_at": utc_timestamp(),
                "metadata": dict(metadata or {}),
            }
            self._save(task)
        return json.dumps(task, indent=2)

    def get(self, task_id: int) -> str:
        with self._lock:
            task = self._load(task_id)
        return json.dumps(task, indent=2)

    def update(
        self,
        task_id: int,
        status: Optional[str] = None,
        add_blocked_by: Optional[List[int]] = None,
        add_blocks: Optional[List[int]] = None,
        owner: Optional[str] = None,
        run_id: Optional[str] = None,
        artifact_dir: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        with self._lock:
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
            if run_id is not None:
                task["run_id"] = run_id.strip()
            if artifact_dir is not None:
                task["artifact_dir"] = artifact_dir
            if metadata:
                task.setdefault("metadata", {}).update(metadata)

            if add_blocked_by:
                ids = self._normalize_task_ids(add_blocked_by)
                task["blockedBy"] = sorted(set(task.get("blockedBy", []) + ids))

            if add_blocks:
                ids = self._normalize_task_ids(add_blocks)
                task["blocks"] = sorted(set(task.get("blocks", []) + ids))
                for blocked_id in ids:
                    self._add_blocked_by(blocked_id, task_id)

            task["updated_at"] = utc_timestamp()
            self._save(task)
        return json.dumps(task, indent=2)

    def list_all(self) -> str:
        tasks = self.list_records()
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

    def list_by_status(self, status: str) -> List[dict]:
        normalized = status.strip().lower()
        return [task for task in self.list_records() if str(task.get("status", "") or "") == normalized]

    def list_records(self) -> List[dict]:
        with self._lock:
            return [self._normalize_task(json.loads(path.read_text())) for path in self._task_paths()]

    def summary_counts(self) -> dict:
        tasks = self.list_records()
        counts = {
            "total": len(tasks),
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "blocked": 0,
            "unowned": 0,
        }
        owners: dict[str, int] = {}
        for task in tasks:
            status = str(task.get("status", "") or "")
            if status in counts:
                counts[status] += 1
            if task.get("blockedBy"):
                counts["blocked"] += 1
            owner = str(task.get("owner", "") or "").strip()
            if owner:
                owners[owner] = owners.get(owner, 0) + 1
            else:
                counts["unowned"] += 1
        counts["owners"] = owners
        return counts

    def render_summary(self) -> str:
        counts = self.summary_counts()
        owners = counts.pop("owners", {})
        lines = [
            f"tasks_total: {counts['total']}",
            f"pending: {counts['pending']}",
            f"in_progress: {counts['in_progress']}",
            f"completed: {counts['completed']}",
            f"blocked: {counts['blocked']}",
            f"unowned: {counts['unowned']}",
        ]
        if owners:
            lines.append("owners: " + ", ".join(f"{name}={count}" for name, count in sorted(owners.items())))
        return "\n".join(lines)

    def render_board(self) -> str:
        tasks = self.list_records()
        if not tasks:
            return "No tasks."
        grouped = {
            "in_progress": [],
            "pending": [],
            "blocked": [],
            "completed": [],
        }
        for task in tasks:
            if task.get("blockedBy") and task.get("status") != "completed":
                grouped["blocked"].append(task)
                continue
            grouped.setdefault(str(task.get("status", "pending")), []).append(task)
        lines = ["Task board:"]
        for key in ("in_progress", "pending", "blocked", "completed"):
            items = grouped.get(key, [])
            lines.extend(["", f"{key} ({len(items)}):"])
            if not items:
                lines.append("- (none)")
                continue
            for task in items:
                owner = f" owner={task.get('owner')}" if task.get("owner") else ""
                blocked = f" blockedBy={task.get('blockedBy')}" if task.get("blockedBy") else ""
                lines.append(f"- #{task['id']} {task.get('subject', '')}{owner}{blocked}")
        return "\n".join(lines)

    def resumable_task(self, owner: str = "") -> Optional[dict]:
        tasks = self.list_records()
        preferred = []
        if owner:
            preferred = [task for task in tasks if task.get("owner") == owner and task.get("status") == "in_progress"]
        if preferred:
            return preferred[-1]
        in_progress = [task for task in tasks if task.get("status") == "in_progress"]
        if in_progress:
            return in_progress[-1]
        pending = [task for task in tasks if task.get("status") == "pending" and not task.get("blockedBy")]
        if pending:
            return pending[0]
        return None

    def claim_task(self, task_id: int, owner: str) -> str:
        with self._lock:
            task = self._load(task_id)
            task["owner"] = owner.strip()
            task["status"] = "in_progress"
            task["updated_at"] = utc_timestamp()
            self._save(task)
        return json.dumps(task, indent=2)

    def claim_next_unblocked(self, owner: str) -> Optional[dict]:
        with self._lock:
            for path in self._task_paths():
                task = json.loads(path.read_text())
                if task.get("status") != "pending":
                    continue
                if task.get("owner"):
                    continue
                if task.get("blockedBy"):
                    continue
                task["owner"] = owner.strip()
                task["status"] = "in_progress"
                task["updated_at"] = utc_timestamp()
                self._save(task)
                return task
        return None

    def attach_run(self, task_id: int, run_id: str, artifact_dir: str = "") -> dict:
        with self._lock:
            task = self._load(task_id)
            task["run_id"] = run_id.strip()
            if artifact_dir:
                task["artifact_dir"] = artifact_dir
            task["updated_at"] = utc_timestamp()
            self._save(task)
        return task

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
        return self._normalize_task(json.loads(path.read_text()))

    def _save(self, task: dict) -> None:
        task.setdefault("updated_at", utc_timestamp())
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

    @staticmethod
    def _normalize_task(task: dict) -> dict:
        normalized = dict(task)
        normalized.setdefault("task_type", "agent")
        normalized.setdefault("run_id", "")
        normalized.setdefault("artifact_dir", "")
        normalized.setdefault("workspace_root", "")
        normalized.setdefault("worktree_id", normalized.get("workspace_root", ""))
        normalized.setdefault("created_at", utc_timestamp())
        normalized.setdefault("updated_at", normalized["created_at"])
        normalized.setdefault("metadata", {})
        return normalized
