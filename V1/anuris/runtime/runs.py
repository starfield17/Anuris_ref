from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .events import utc_timestamp


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class RunRecord:
    id: str
    run_type: str
    workspace_root: str
    worktree_id: str
    description: str = ""
    status: str = "pending"
    owner: str = ""
    parent_run_id: str = ""
    task_id: str = ""
    created_at: str = field(default_factory=utc_timestamp)
    updated_at: str = field(default_factory=utc_timestamp)
    artifact_dir: str = ""
    transcript_path: str = ""
    output_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def touch(self, status: Optional[str] = None) -> None:
        if status:
            self.status = status
        self.updated_at = utc_timestamp()


class RuntimeRunManager:
    """Persistent run registry for session, forked, background, and teammate work."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        run_id: str,
        run_type: str,
        workspace_root: Path,
        *,
        description: str = "",
        owner: str = "",
        parent_run_id: str = "",
        task_id: str = "",
        worktree_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RunRecord:
        artifact_dir = self.root / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        workspace = Path(workspace_root).resolve()
        record = RunRecord(
            id=run_id,
            run_type=run_type,
            workspace_root=str(workspace),
            worktree_id=worktree_id or str(workspace),
            description=description.strip(),
            status="running",
            owner=owner.strip(),
            parent_run_id=parent_run_id.strip(),
            task_id=task_id.strip(),
            artifact_dir=str(artifact_dir),
            transcript_path=str((artifact_dir / "transcript.md").resolve()),
            output_path=str((artifact_dir / "output.log").resolve()),
            metadata=dict(metadata or {}),
        )
        self._save(record)
        return record

    def get(self, run_id: str) -> RunRecord:
        path = self.root / run_id / "run.json"
        if not path.exists():
            raise KeyError(f"Unknown runtime run: {run_id}")
        return RunRecord(**json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> List[RunRecord]:
        runs: List[RunRecord] = []
        for path in sorted(self.root.glob("*/run.json")):
            runs.append(RunRecord(**json.loads(path.read_text(encoding="utf-8"))))
        return runs

    def update(
        self,
        run_id: str,
        *,
        status: Optional[str] = None,
        owner: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        transcript_path: Optional[str] = None,
        output_path: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> RunRecord:
        record = self.get(run_id)
        if record.status in TERMINAL_RUN_STATUSES and status and status != record.status:
            raise ValueError(f"Run {run_id} is terminal: {record.status}")
        record.touch(status)
        if owner is not None:
            record.owner = owner.strip()
        if metadata:
            record.metadata.update(metadata)
        if transcript_path is not None:
            record.transcript_path = transcript_path
        if output_path is not None:
            record.output_path = output_path
        if task_id is not None:
            record.task_id = task_id.strip()
        self._save(record)
        return record

    def append_output(self, run_id: str, text: str) -> RunRecord:
        record = self.get(run_id)
        output_path = Path(record.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
        record.touch()
        self._save(record)
        return record

    def complete(self, run_id: str, **metadata: Any) -> RunRecord:
        return self._finish(run_id, "completed", metadata)

    def fail(self, run_id: str, **metadata: Any) -> RunRecord:
        return self._finish(run_id, "failed", metadata)

    def cancel(self, run_id: str, **metadata: Any) -> RunRecord:
        return self._finish(run_id, "cancelled", metadata)

    def _finish(self, run_id: str, status: str, metadata: Dict[str, Any]) -> RunRecord:
        final_metadata = dict(metadata)
        return self.update(
            run_id,
            status=status,
            metadata=final_metadata or None,
            transcript_path=self._pop_text_value(final_metadata, "transcript_path"),
            output_path=self._pop_text_value(final_metadata, "output_path"),
            task_id=self._pop_text_value(final_metadata, "task_id"),
        )

    def active(self) -> List[RunRecord]:
        return [record for record in self.list() if record.status not in TERMINAL_RUN_STATUSES]

    def _pop_text_value(self, metadata: Dict[str, Any], key: str) -> Optional[str]:
        if key not in metadata:
            return None
        return str(metadata.pop(key))

    def _save(self, record: RunRecord) -> None:
        path = self.root / record.id / "run.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")
