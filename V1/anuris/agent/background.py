import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class BackgroundManager:
    """s08-style background task runner with notification draining."""

    def __init__(
        self,
        workspace_root: Path,
        runtime_task_manager: Optional[object] = None,
        runtime_run_manager: Optional[object] = None,
        runtime_queue: Optional[object] = None,
    ):
        self.workspace_root = workspace_root.resolve()
        self.runtime_task_manager = runtime_task_manager
        self.runtime_run_manager = runtime_run_manager
        self.runtime_queue = runtime_queue
        self.tasks: Dict[str, Dict[str, Optional[str]]] = {}
        self._notifications: List[Dict[str, str]] = []
        self._lock = threading.Lock()

    def run(self, command: str, timeout: int = 300) -> str:
        if self._is_dangerous(command):
            return "Error: Dangerous command blocked"

        task_id = str(uuid.uuid4())[:8]
        with self._lock:
            self.tasks[task_id] = {
                "status": "running",
                "command": command,
                "result": None,
            }
        run_record = None
        if self.runtime_run_manager is not None:
            run_record = self.runtime_run_manager.create(
                task_id,
                "background_shell",
                self.workspace_root,
                description=command[:240],
                owner="background",
                metadata={"timeout": timeout},
            )
        if self.runtime_task_manager is not None:
            self.runtime_task_manager.create(
                task_id,
                "background_command",
                command[:120],
                owner="background",
                run_id=getattr(run_record, "id", ""),
                workspace_root=str(self.workspace_root),
                worktree_id=str(self.workspace_root),
                artifact_dir=getattr(run_record, "artifact_dir", ""),
                transcript_path=getattr(run_record, "transcript_path", ""),
            )
            self.runtime_task_manager.update(task_id, "running")
        thread = threading.Thread(
            target=self._execute,
            args=(task_id, command, timeout, run_record),
            daemon=True,
        )
        thread.start()
        return f"Background task {task_id} started: {command[:80]}"

    def run_agent(self, label: str, runner: Callable[[], str]) -> str:
        task_id = str(uuid.uuid4())[:8]
        with self._lock:
            self.tasks[task_id] = {"status": "running", "command": label, "result": None}
        run_record = None
        if self.runtime_run_manager is not None:
            run_record = self.runtime_run_manager.create(
                task_id,
                "background_agent",
                self.workspace_root,
                description=label[:240],
                owner="background",
            )
        if self.runtime_task_manager is not None:
            self.runtime_task_manager.create(
                task_id,
                "background_agent",
                label[:120],
                owner="background",
                run_id=getattr(run_record, "id", ""),
                workspace_root=str(self.workspace_root),
                worktree_id=str(self.workspace_root),
                artifact_dir=getattr(run_record, "artifact_dir", ""),
                transcript_path=getattr(run_record, "transcript_path", ""),
            )
            self.runtime_task_manager.update(task_id, "running")
        thread = threading.Thread(target=self._execute_callable, args=(task_id, label, runner, run_record), daemon=True)
        thread.start()
        return f"Background agent task {task_id} started: {label[:80]}"

    def _execute(self, task_id: str, command: str, timeout: int, run_record: Optional[Any]) -> None:
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout + result.stderr).strip()[:50000]
            status = "completed"
        except subprocess.TimeoutExpired:
            output = f"Error: Timeout ({timeout}s)"
            status = "timeout"
        except Exception as exc:
            output = f"Error: {exc}"
            status = "error"
        self._finalize(task_id, command, output, status, run_record)

    def _execute_callable(self, task_id: str, label: str, runner: Callable[[], str], run_record: Optional[Any]) -> None:
        try:
            output = runner()
            status = "completed"
        except Exception as exc:
            output = f"Error: {exc}"
            status = "error"
        self._finalize(task_id, label, output, status, run_record)

    def _finalize(self, task_id: str, command: str, output: str, status: str, run_record: Optional[Any]) -> None:
        rendered_output = output or "(no output)"
        with self._lock:
            if task_id not in self.tasks:
                return
            self.tasks[task_id]["status"] = status
            self.tasks[task_id]["result"] = rendered_output
            self._notifications.append(
                {
                    "task_id": task_id,
                    "status": status,
                    "result": rendered_output[:500],
                    "command": command[:80],
                }
            )
        if self.runtime_task_manager is not None:
            self.runtime_task_manager.append_output(task_id, rendered_output + "\n")
            if status == "completed":
                self.runtime_task_manager.complete(task_id)
            else:
                self.runtime_task_manager.fail(task_id)
        if self.runtime_run_manager is not None and run_record is not None:
            self.runtime_run_manager.append_output(run_record.id, rendered_output + "\n")
            if status == "completed":
                self.runtime_run_manager.complete(run_record.id)
            else:
                self.runtime_run_manager.fail(run_record.id, error=rendered_output[:500])
        if self.runtime_queue is not None:
            self.runtime_queue.enqueue(
                "background_task_finished",
                {
                    "task_id": task_id,
                    "status": status,
                    "command": command[:80],
                    "result": rendered_output[:500],
                },
                source="background",
                priority="later",
            )

    def check(self, task_id: Optional[str] = None) -> str:
        with self._lock:
            if task_id:
                task = self.tasks.get(task_id)
                if not task:
                    return f"Error: Unknown task {task_id}"
                result = task.get("result") or "(running)"
                return f"[{task['status']}] {task.get('command', '')[:60]}\n{result}"

            if not self.tasks:
                return "No background tasks."
            lines = []
            for tid, task in self.tasks.items():
                lines.append(f"{tid}: [{task['status']}] {task.get('command', '')[:60]}")
            return "\n".join(lines)

    def drain_notifications(self) -> List[Dict[str, str]]:
        with self._lock:
            notifications = list(self._notifications)
            self._notifications.clear()
        return notifications

    @staticmethod
    def _is_dangerous(command: str) -> bool:
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
        return any(item in command for item in dangerous)
