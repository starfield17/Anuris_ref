import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional


TOOL_SCHEMAS = [
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
]


class AgentToolExecutor:
    """Minimal tool executor for OpenAI function-calling loops."""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.handlers: Dict[str, Callable[..., str]] = {
            "bash": lambda **kw: self.run_bash(kw["command"]),
            "read_file": lambda **kw: self.run_read(kw["path"], kw.get("limit")),
            "write_file": lambda **kw: self.run_write(kw["path"], kw["content"]),
            "edit_file": lambda **kw: self.run_edit(kw["path"], kw["old_text"], kw["new_text"]),
        }

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
