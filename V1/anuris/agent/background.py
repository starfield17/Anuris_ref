import subprocess
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional


class BackgroundManager:
    """s08-style background task runner with notification draining."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
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
        thread = threading.Thread(
            target=self._execute,
            args=(task_id, command, timeout),
            daemon=True,
        )
        thread.start()
        return f"Background task {task_id} started: {command[:80]}"

    def _execute(self, task_id: str, command: str, timeout: int) -> None:
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

        with self._lock:
            if task_id not in self.tasks:
                return
            self.tasks[task_id]["status"] = status
            self.tasks[task_id]["result"] = output or "(no output)"
            self._notifications.append(
                {
                    "task_id": task_id,
                    "status": status,
                    "result": (output or "(no output)")[:500],
                    "command": command[:80],
                }
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
