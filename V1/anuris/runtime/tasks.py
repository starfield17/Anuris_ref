from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .events import utc_timestamp


TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class TaskRecord:
    id: str
    kind: str
    description: str
    status: str = "pending"
    owner: str = ""
    created_at: str = field(default_factory=utc_timestamp)
    updated_at: str = field(default_factory=utc_timestamp)
    output_file: str = ""
    output_offset: int = 0
    metadata: Dict[str, str] = field(default_factory=dict)

    def touch(self, status: Optional[str] = None) -> None:
        if status:
            self.status = status
        self.updated_at = utc_timestamp()


class RuntimeTaskManager:
    """Runtime task lifecycle store with persisted task records."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: str, kind: str, description: str, owner: str = "") -> TaskRecord:
        record = TaskRecord(
            id=task_id,
            kind=kind,
            description=description,
            owner=owner,
            output_file=str((self.root / f"{task_id}.log").resolve()),
        )
        self._save(record)
        return record

    def update(self, task_id: str, status: str, owner: Optional[str] = None) -> TaskRecord:
        record = self.get(task_id)
        if record.status in TERMINAL_TASK_STATUSES:
            raise ValueError(f"Task {task_id} is terminal: {record.status}")
        record.touch(status)
        if owner is not None:
            record.owner = owner.strip()
        self._save(record)
        return record

    def complete(self, task_id: str) -> TaskRecord:
        return self.update(task_id, "completed")

    def fail(self, task_id: str) -> TaskRecord:
        return self.update(task_id, "failed")

    def cancel(self, task_id: str) -> TaskRecord:
        return self.update(task_id, "cancelled")

    def get(self, task_id: str) -> TaskRecord:
        path = self.root / f"{task_id}.json"
        if not path.exists():
            raise KeyError(f"Unknown runtime task: {task_id}")
        return TaskRecord(**json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> List[TaskRecord]:
        items: List[TaskRecord] = []
        for path in sorted(self.root.glob("*.json")):
            items.append(TaskRecord(**json.loads(path.read_text(encoding="utf-8"))))
        return items

    def append_output(self, task_id: str, text: str) -> TaskRecord:
        record = self.get(task_id)
        output_path = Path(record.output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
        record.output_offset = output_path.stat().st_size
        record.touch()
        self._save(record)
        return record

    def _save(self, record: TaskRecord) -> None:
        path = self.root / f"{record.id}.json"
        path.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")
