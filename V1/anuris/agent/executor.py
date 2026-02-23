import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .tasks import PersistentTaskManager
from .todo import TodoManager


class AgentToolExecutor:
    """Tool executor for function-calling loops with optional s03/s04/s07 capabilities."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        include_write_edit: bool = True,
        include_todo: bool = True,
        include_task: bool = False,
        include_task_board: bool = True,
        subagent_runner: Optional[Callable[[str, str], str]] = None,
        todo_manager: Optional[TodoManager] = None,
        task_manager: Optional[PersistentTaskManager] = None,
    ):
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.todo_manager = todo_manager if include_todo else None
        self.subagent_runner = subagent_runner if include_task else None
        self.task_manager = task_manager if include_task_board else None
        if include_task_board and self.task_manager is None:
            self.task_manager = PersistentTaskManager(self.workspace_root / ".anuris_tasks")

        self.handlers: Dict[str, Callable[..., str]] = {
            "bash": lambda **kw: self.run_bash(kw["command"]),
            "read_file": lambda **kw: self.run_read(kw["path"], kw.get("limit")),
        }
        if include_write_edit:
            self.handlers.update(
                {
                    "write_file": lambda **kw: self.run_write(kw["path"], kw["content"]),
                    "edit_file": lambda **kw: self.run_edit(kw["path"], kw["old_text"], kw["new_text"]),
                }
            )
        if include_todo:
            self.handlers["TodoWrite"] = lambda **kw: self.run_todo_write(kw["items"])
        if include_task:
            self.handlers["task"] = lambda **kw: self.run_task(kw["prompt"], kw.get("agent_type", "Explore"))
        if include_task_board:
            self.handlers.update(
                {
                    "task_create": lambda **kw: self.run_task_create(kw["subject"], kw.get("description", "")),
                    "task_get": lambda **kw: self.run_task_get(kw["task_id"]),
                    "task_update": lambda **kw: self.run_task_update(
                        kw["task_id"],
                        kw.get("status"),
                        kw.get("add_blocked_by"),
                        kw.get("add_blocks"),
                        kw.get("owner"),
                    ),
                    "task_list": lambda **kw: self.run_task_list(),
                }
            )

    def set_subagent_runner(self, runner: Callable[[str, str], str]) -> None:
        """Attach a subagent callback for the task tool."""
        self.subagent_runner = runner

    def execute(self, tool_name: str, args: Dict[str, Any]) -> str:
        handler = self.handlers.get(tool_name)
        if not handler:
            return f"Error: Unknown tool '{tool_name}'"
        try:
            return str(handler(**args))
        except Exception as exc:
            return f"Error: {str(exc)}"

    def safe_path(self, relative_or_abs: str) -> Path:
        candidate = (self.workspace_root / relative_or_abs).resolve()
        if not candidate.is_relative_to(self.workspace_root):
            raise ValueError(f"Path escapes workspace: {relative_or_abs}")
        return candidate

    def run_bash(self, command: str) -> str:
        dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
        if any(item in command for item in dangerous):
            return "Error: Dangerous command blocked"
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = (result.stdout + result.stderr).strip()
            return output[:50000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"

    def run_read(self, path: str, limit: Optional[int] = None) -> str:
        lines = self.safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]

    def run_write(self, path: str, content: str) -> str:
        target = self.safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"

    def run_edit(self, path: str, old_text: str, new_text: str) -> str:
        target = self.safe_path(path)
        content = target.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        target.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"

    def run_todo_write(self, items: List[Dict[str, Any]]) -> str:
        if not self.todo_manager:
            return "Error: Todo manager unavailable"
        return self.todo_manager.update(items)

    def run_task(self, prompt: str, agent_type: str = "Explore") -> str:
        if not self.subagent_runner:
            return "Error: Subagent runner unavailable"
        return self.subagent_runner(prompt, agent_type)

    def run_task_create(self, subject: str, description: str = "") -> str:
        if not self.task_manager:
            return "Error: Task manager unavailable"
        return self.task_manager.create(subject, description)

    def run_task_get(self, task_id: int) -> str:
        if not self.task_manager:
            return "Error: Task manager unavailable"
        return self.task_manager.get(task_id)

    def run_task_update(
        self,
        task_id: int,
        status: Optional[str] = None,
        add_blocked_by: Optional[List[int]] = None,
        add_blocks: Optional[List[int]] = None,
        owner: Optional[str] = None,
    ) -> str:
        if not self.task_manager:
            return "Error: Task manager unavailable"
        return self.task_manager.update(
            task_id,
            status=status,
            add_blocked_by=add_blocked_by,
            add_blocks=add_blocks,
            owner=owner,
        )

    def run_task_list(self) -> str:
        if not self.task_manager:
            return "Error: Task manager unavailable"
        return self.task_manager.list_all()

    def get_todo_snapshot(self) -> str:
        if not self.todo_manager:
            return "Todo manager unavailable"
        return self.todo_manager.render()

    def get_task_snapshot(self) -> str:
        if not self.task_manager:
            return "Task manager unavailable"
        return self.task_manager.list_all()
