import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _bash_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }


def _read_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    }


def _write_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    }


def _edit_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace one exact text occurrence in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    }


def _todo_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "TodoWrite",
            "description": "Update task tracking list for multi-step work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                                "activeForm": {"type": "string"},
                            },
                            "required": ["content", "status", "activeForm"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    }


def _task_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "task",
            "description": "Spawn a subagent with fresh context to handle a subtask.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "agent_type": {
                        "type": "string",
                        "enum": ["Explore", "general-purpose"],
                    },
                },
                "required": ["prompt"],
            },
        },
    }


def build_tool_schemas(
    include_write_edit: bool = True,
    include_todo: bool = True,
    include_task: bool = True,
) -> List[Dict[str, Any]]:
    """Build tool schema list by feature flags."""
    schemas = [_bash_schema(), _read_schema()]
    if include_write_edit:
        schemas.extend([_write_schema(), _edit_schema()])
    if include_todo:
        schemas.append(_todo_schema())
    if include_task:
        schemas.append(_task_schema())
    return schemas


TOOL_SCHEMAS = build_tool_schemas()


class TodoManager:
    """In-memory todo list manager (s03 style)."""

    def __init__(self):
        self.items: List[Dict[str, str]] = []

    def update(self, items: List[Dict[str, Any]]) -> str:
        if len(items) > 20:
            raise ValueError("Max 20 todos")

        validated: List[Dict[str, str]] = []
        in_progress_count = 0
        for index, item in enumerate(items):
            content = str(item.get("content", item.get("text", ""))).strip()
            status = str(item.get("status", "pending")).lower()
            active_form = str(item.get("activeForm", content)).strip()
            if not content:
                raise ValueError(f"Item {index}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {index}: invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1
                if not active_form:
                    raise ValueError(f"Item {index}: activeForm required for in_progress")
            validated.append(
                {
                    "content": content,
                    "status": status,
                    "activeForm": active_form,
                }
            )

        if in_progress_count > 1:
            raise ValueError("Only one in_progress allowed")

        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."

        lines: List[str] = []
        for item in self.items:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }.get(item["status"], "[?]")
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            lines.append(f"{marker} {item['content']}{suffix}")

        done = sum(1 for item in self.items if item["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


class AgentToolExecutor:
    """Tool executor for function-calling loops with optional s03/s04 capabilities."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        include_write_edit: bool = True,
        include_todo: bool = True,
        include_task: bool = False,
        subagent_runner: Optional[Callable[[str, str], str]] = None,
        todo_manager: Optional[TodoManager] = None,
    ):
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.todo_manager = todo_manager if include_todo else None
        self.subagent_runner = subagent_runner if include_task else None
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

    def get_todo_snapshot(self) -> str:
        if not self.todo_manager:
            return "Todo manager unavailable"
        return self.todo_manager.render()
