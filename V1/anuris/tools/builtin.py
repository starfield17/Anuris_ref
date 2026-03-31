from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from .base import BaseTool, ToolExecutionResult


def _resolve_workspace_path(workspace_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {raw_path}") from exc
    return resolved


def _looks_write_like(command: str) -> bool:
    suspicious = [
        " rm ",
        " mv ",
        " cp ",
        " chmod ",
        " chown ",
        " tee ",
        " >",
        ">>",
        "sed -i",
        "git checkout",
        "git reset",
    ]
    wrapped = f" {command.strip()} "
    return any(token in wrapped for token in suspicious)


class BashTool(BaseTool):
    name = "bash"
    description = "Run a shell command in the workspace."

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            "required": ["command"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        command = str(args.get("command", "")).strip()
        timeout = int(args.get("timeout_sec", 20))
        if not command:
            raise ValueError("command is required")
        if context.permission_context.mode == "readonly" and _looks_write_like(command):
            return ToolExecutionResult(
                model_content="Readonly subagent cannot run write-like shell commands.",
                is_error=True,
                summary=f"bash denied: {command}",
            )

        completed = subprocess.run(
            command,
            cwd=context.workspace_root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
        if not output:
            output = "(no output)"
        if len(output) > 8000:
            output = output[:8000] + "\n...[truncated]"
        return ToolExecutionResult(
            model_content=output,
            display_content=output,
            is_error=completed.returncode != 0,
            summary=f"bash exit={completed.returncode}: {command}",
            metadata={"returncode": completed.returncode},
        )


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a UTF-8 text file from the workspace."

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        path = _resolve_workspace_path(context.workspace_root, str(args.get("path", "")))
        text = path.read_text(encoding="utf-8")
        start_line = int(args.get("start_line", 1))
        end_line = int(args.get("end_line", 0))
        lines = text.splitlines()
        if end_line > 0:
            snippet = lines[start_line - 1 : end_line]
        else:
            snippet = lines[start_line - 1 :]
        content = "\n".join(snippet)
        return ToolExecutionResult(
            model_content=content,
            summary=f"read_file {path.relative_to(context.workspace_root)}",
        )


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write a full text file in the workspace."
    requires_write = True

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        path = _resolve_workspace_path(context.workspace_root, str(args.get("path", "")))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content", ""))
        path.write_text(content, encoding="utf-8")
        return ToolExecutionResult(
            model_content=f"Wrote {len(content)} characters to {path.relative_to(context.workspace_root)}",
            summary=f"write_file {path.relative_to(context.workspace_root)}",
        )


class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Replace text inside an existing workspace file."
    requires_write = True

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        path = _resolve_workspace_path(context.workspace_root, str(args.get("path", "")))
        old_text = str(args.get("old_text", ""))
        new_text = str(args.get("new_text", ""))
        content = path.read_text(encoding="utf-8")
        if old_text not in content:
            raise ValueError("old_text was not found in the target file")
        updated = content.replace(old_text, new_text, 1)
        path.write_text(updated, encoding="utf-8")
        return ToolExecutionResult(
            model_content=f"Edited {path.relative_to(context.workspace_root)}",
            summary=f"edit_file {path.relative_to(context.workspace_root)}",
        )


class GlobTool(BaseTool):
    name = "glob"
    description = "Find files in the workspace using glob patterns."

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
            },
            "required": ["pattern"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern is required")
        matches = sorted(context.workspace_root.glob(pattern))
        rendered = "\n".join(str(path.relative_to(context.workspace_root)) for path in matches[:200]) or "(no matches)"
        return ToolExecutionResult(
            model_content=rendered,
            summary=f"glob {pattern}",
        )


class GrepTool(BaseTool):
    name = "grep"
    description = "Search workspace files for a text pattern."

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["pattern"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        pattern = str(args.get("pattern", "")).strip()
        raw_path = str(args.get("path", ".")).strip() or "."
        search_root = _resolve_workspace_path(context.workspace_root, raw_path)
        if shutil.which("rg"):
            completed = subprocess.run(
                ["rg", "-n", "--hidden", "--glob", "!.git", pattern, str(search_root)],
                cwd=context.workspace_root,
                capture_output=True,
                text=True,
                timeout=20,
            )
            output = completed.stdout.strip() or completed.stderr.strip() or "(no matches)"
        else:
            matches: List[str] = []
            for path in search_root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                        if pattern in line:
                            rel = path.relative_to(context.workspace_root)
                            matches.append(f"{rel}:{index}:{line.strip()}")
                except Exception:
                    continue
            output = "\n".join(matches[:200]) or "(no matches)"
        return ToolExecutionResult(
            model_content=output,
            summary=f"grep {pattern}",
        )


class TodoWriteTool(BaseTool):
    name = "todo_write"
    description = "Replace the in-memory todo board for the session."

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object"},
                }
            },
            "required": ["items"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        items = list(args.get("items", []))
        rendered = context.services.todo_manager.update(items)
        return ToolExecutionResult(
            model_content=rendered,
            summary="todo_write",
        )


class TaskCreateTool(BaseTool):
    name = "task_create"
    description = "Create a persistent task on disk."
    requires_write = True

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["subject"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        payload = context.services.task_manager.create(
            subject=str(args.get("subject", "")),
            description=str(args.get("description", "")),
        )
        return ToolExecutionResult(model_content=payload, summary="task_create")


class TaskGetTool(BaseTool):
    name = "task_get"
    description = "Read one persistent task."

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
            },
            "required": ["task_id"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        payload = context.services.task_manager.get(int(args.get("task_id", 0)))
        return ToolExecutionResult(model_content=payload, summary="task_get")


class TaskUpdateTool(BaseTool):
    name = "task_update"
    description = "Update a persistent task status, owner, or dependencies."
    requires_write = True

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "status": {"type": "string"},
                "owner": {"type": "string"},
                "add_blocked_by": {"type": "array", "items": {"type": "integer"}},
                "add_blocks": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["task_id"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        payload = context.services.task_manager.update(
            task_id=int(args.get("task_id", 0)),
            status=args.get("status"),
            owner=args.get("owner"),
            add_blocked_by=args.get("add_blocked_by"),
            add_blocks=args.get("add_blocks"),
        )
        return ToolExecutionResult(model_content=payload, summary="task_update")


class TaskListTool(BaseTool):
    name = "task_list"
    description = "List persistent tasks."

    def input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        del args
        payload = context.services.task_manager.list_all()
        return ToolExecutionResult(model_content=payload, summary="task_list")


class SkillTool(BaseTool):
    name = "load_skill"
    description = "Load a local skill body from the workspace skill directories."

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        body = context.services.skill_loader.load(str(args.get("name", "")))
        return ToolExecutionResult(model_content=body, summary="load_skill")


class AgentTaskTool(BaseTool):
    name = "task"
    description = "Delegate a bounded sub-task to a temporary subagent."
    coordination_tool = True

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "description": {"type": "string"},
                "readonly": {"type": "boolean"},
            },
            "required": ["prompt"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        prompt = str(args.get("prompt", "")).strip()
        description = str(args.get("description", "Subtask")).strip() or "Subtask"
        readonly = bool(args.get("readonly", True))
        if not prompt:
            raise ValueError("prompt is required")
        output = context.run_subagent(prompt, description, readonly)
        return ToolExecutionResult(
            model_content=output,
            summary=f"task subagent: {description}",
        )


def build_default_tools() -> List[BaseTool]:
    return [
        BashTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        GlobTool(),
        GrepTool(),
        TodoWriteTool(),
        TaskCreateTool(),
        TaskGetTool(),
        TaskUpdateTool(),
        TaskListTool(),
        SkillTool(),
        AgentTaskTool(),
    ]
