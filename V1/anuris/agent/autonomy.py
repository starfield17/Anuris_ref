from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional


class AutonomousTaskController:
    """Claim and execute persistent tasks using the shared run/runtime infrastructure."""

    def __init__(
        self,
        task_manager: Any,
        *,
        workspace_root: Path,
        run_manager: Any = None,
        runtime_task_manager: Any = None,
        runtime_queue: Any = None,
    ):
        self.task_manager = task_manager
        self.workspace_root = Path(workspace_root).resolve()
        self.run_manager = run_manager
        self.runtime_task_manager = runtime_task_manager
        self.runtime_queue = runtime_queue

    def run_next(self, owner: str, runner: Callable[[dict], str]) -> Optional[dict]:
        task = self.task_manager.claim_next_unblocked(owner)
        if not task:
            return None
        run_record = None
        runtime_task = None
        if self.run_manager is not None:
            run_record = self.run_manager.create(
                f"taskrun_{task['id']}",
                "autonomous_task",
                self.workspace_root,
                description=str(task.get("subject", "")),
                owner=owner,
                task_id=str(task.get("id", "")),
            )
        if self.runtime_task_manager is not None:
            runtime_task = self.runtime_task_manager.create(
                f"task_{task['id']}",
                "autonomous_task",
                str(task.get("subject", ""))[:120],
                owner=owner,
                run_id=getattr(run_record, "id", ""),
                workspace_root=str(self.workspace_root),
                worktree_id=str(self.workspace_root),
                artifact_dir=getattr(run_record, "artifact_dir", ""),
                transcript_path=getattr(run_record, "transcript_path", ""),
            )
            self.runtime_task_manager.update(runtime_task.id, "running")
        self.task_manager.attach_run(task["id"], getattr(run_record, "id", ""), getattr(run_record, "artifact_dir", ""))
        if self.runtime_queue is not None:
            self.runtime_queue.enqueue(
                "autonomous_task_started",
                {"task_id": task["id"], "owner": owner, "subject": task.get("subject", "")},
                source="autonomy",
                priority="next",
            )
        try:
            result = runner(task)
        except Exception as exc:
            self.task_manager.update(task["id"], status="failed", metadata={"error": str(exc)})
            if runtime_task is not None:
                self.runtime_task_manager.fail(runtime_task.id)
                self.runtime_task_manager.append_output(runtime_task.id, f"Error: {exc}\n")
            if run_record is not None:
                self.run_manager.fail(run_record.id, error=str(exc))
                self.run_manager.append_output(run_record.id, f"Error: {exc}\n")
            if self.runtime_queue is not None:
                self.runtime_queue.enqueue(
                    "autonomous_task_failed",
                    {"task_id": task["id"], "owner": owner, "error": str(exc)},
                    source="autonomy",
                    priority="later",
                )
            raise
        self.task_manager.update(task["id"], status="completed")
        if runtime_task is not None:
            self.runtime_task_manager.append_output(runtime_task.id, (result or "(no output)") + "\n")
            self.runtime_task_manager.complete(runtime_task.id)
        if run_record is not None:
            self.run_manager.append_output(run_record.id, (result or "(no output)") + "\n")
            self.run_manager.complete(run_record.id, final_text=result)
        if self.runtime_queue is not None:
            self.runtime_queue.enqueue(
                "autonomous_task_completed",
                {"task_id": task["id"], "owner": owner, "result": (result or "(no output)")[:240]},
                source="autonomy",
                priority="later",
            )
        return self.task_manager.resumable_task(owner)
