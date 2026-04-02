from __future__ import annotations

import json
import shutil
import subprocess
import difflib
from pathlib import Path
from typing import Any, Dict, List

from .base import BaseTool, ToolExecutionResult
from .read_tracker import ReadTracker, UNCHANGED_READ_MESSAGE


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
    search_hint = "run shell commands, inspect git, execute tests"
    usage_policy = "Prefer read-only inspection commands; write-like commands require non-readonly policy."

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
        denial = context.permission_context.explain_command_denial(command)
        if denial is not None:
            return ToolExecutionResult(
                model_content="Command blocked by local sandbox exclude rules.",
                is_error=True,
                summary=f"bash excluded: {command}",
                metadata={"permission_denial": denial, "command": command},
            )
        if context.permission_context.mode == "readonly" and _looks_write_like(command):
            return ToolExecutionResult(
                model_content="Readonly subagent cannot run write-like shell commands.",
                is_error=True,
                summary=f"bash denied: {command}",
                metadata={
                    "permission_denial": {
                        "reason_code": "readonly_write_like_command",
                        "message": "Readonly subagent cannot run write-like shell commands.",
                        "command": command,
                        "mode": context.permission_context.mode,
                    },
                    "command": command,
                },
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
    search_hint = "inspect code, config, docs, and transcripts"
    permission_type = "read"
    usage_policy = "Use for targeted reads; prefer line ranges when only part of a file is needed."
    result_persistence_policy = "never_persist"

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
        context.services.context_files.record(path)
        start_line = int(args.get("start_line", 1))
        end_line = int(args.get("end_line", 0))
        tracker = _read_tracker_for(context)
        mtime_ns = path.stat().st_mtime_ns
        if tracker.is_unchanged(path, start_line, end_line, mtime_ns):
            return ToolExecutionResult(
                model_content=UNCHANGED_READ_MESSAGE,
                summary=f"read_file {path.relative_to(context.workspace_root)}",
                metadata={
                    "path": str(path.relative_to(context.workspace_root)),
                    "start_line": start_line,
                    "end_line": end_line,
                    "mtime_ns": mtime_ns,
                    "unchanged_since_last_read": True,
                },
            )
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if end_line > 0:
            snippet = lines[start_line - 1 : end_line]
        else:
            snippet = lines[start_line - 1 :]
        content = "\n".join(snippet)
        snapshot = tracker.remember(
            path,
            start_line,
            end_line,
            mtime_ns=mtime_ns,
            content=content,
        )
        return ToolExecutionResult(
            model_content=content,
            summary=f"read_file {path.relative_to(context.workspace_root)}",
            metadata={
                "path": str(path.relative_to(context.workspace_root)),
                "start_line": start_line,
                "end_line": end_line,
                "mtime_ns": snapshot.mtime_ns,
                "content_hash": snapshot.content_hash,
                "unchanged_since_last_read": False,
            },
        )


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write a full text file in the workspace."
    search_hint = "create or replace file contents"
    permission_type = "edit"
    usage_policy = "Use for whole-file writes when replacing the entire contents is simpler than patching."
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
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(content, encoding="utf-8")
        context.services.context_files.record(path)
        diff = "\n".join(
            difflib.unified_diff(
                previous.splitlines(),
                content.splitlines(),
                fromfile=f"{path.name}:before",
                tofile=f"{path.name}:after",
                lineterm="",
            )
        )
        return ToolExecutionResult(
            model_content=f"Wrote {len(content)} characters to {path.relative_to(context.workspace_root)}",
            summary=f"write_file {path.relative_to(context.workspace_root)}",
            metadata={
                "path": str(path.relative_to(context.workspace_root)),
                "diff": diff,
                "summary": f"write_file {path.relative_to(context.workspace_root)}",
            },
        )


class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Replace text inside an existing workspace file."
    search_hint = "patch an existing file with targeted edits"
    permission_type = "edit"
    usage_policy = "Prefer for localized changes that preserve surrounding file content."
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
        context.services.context_files.record(path)
        diff = "\n".join(
            difflib.unified_diff(
                content.splitlines(),
                updated.splitlines(),
                fromfile=f"{path.name}:before",
                tofile=f"{path.name}:after",
                lineterm="",
            )
        )
        return ToolExecutionResult(
            model_content=f"Edited {path.relative_to(context.workspace_root)}",
            summary=f"edit_file {path.relative_to(context.workspace_root)}",
            metadata={
                "path": str(path.relative_to(context.workspace_root)),
                "diff": diff,
                "summary": f"edit_file {path.relative_to(context.workspace_root)}",
            },
        )


class GlobTool(BaseTool):
    name = "glob"
    description = "Find files in the workspace using glob patterns."
    search_hint = "find files by name or wildcard pattern"
    permission_type = "read"

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
        for path in matches[:50]:
            context.services.context_files.record(path)
        rendered = "\n".join(str(path.relative_to(context.workspace_root)) for path in matches[:200]) or "(no matches)"
        return ToolExecutionResult(
            model_content=rendered,
            summary=f"glob {pattern}",
        )


class GrepTool(BaseTool):
    name = "grep"
    description = "Search workspace files for a text pattern."
    search_hint = "search code or docs for symbols, strings, or errors"
    permission_type = "read"

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
        context.services.context_files.record(search_root)
        return ToolExecutionResult(
            model_content=output,
            summary=f"grep {pattern}",
        )


class TodoWriteTool(BaseTool):
    name = "todo_write"
    description = "Replace the in-memory todo board for the session."
    search_hint = "track progress for multi-step work"
    permission_type = "coordination"
    usage_policy = (
        "Use proactively for complex multi-step tasks, and keep exactly one item in_progress whenever work is active."
    )

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
    search_hint = "persist a task for later claiming or governance"
    permission_type = "edit"
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
    search_hint = "inspect one persistent task"
    permission_type = "read"

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
    search_hint = "change task status, owner, or dependencies"
    permission_type = "edit"
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
    search_hint = "show the persistent task board"
    permission_type = "read"

    def input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        del args
        payload = context.services.task_manager.list_all()
        return ToolExecutionResult(model_content=payload, summary="task_list")


class SkillTool(BaseTool):
    name = "load_skill"
    description = "Load a local skill body from the workspace skill directories."
    search_hint = "invoke a workspace skill or slash-command-like prompt"
    permission_type = "coordination"
    usage_policy = "Use before responding when a skill clearly matches the user's requested workflow."

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


class ToolSearchTool(BaseTool):
    name = "tool_search"
    description = "Search the active tool catalog by keyword."
    search_hint = "discover relevant tools by name, description, or usage hint"
    permission_type = "read"

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        query = str(args.get("query", "")).strip().lower()
        if not query:
            raise ValueError("query is required")
        matches = []
        for tool in context.metadata.get("tool_registry", []):
            haystack = f"{tool.name} {tool.description} {getattr(tool, 'search_hint', '')} {getattr(tool, 'usage_policy', '')}".lower()
            if query in haystack and tool.can_expose(context):
                matches.append(f"- {tool.name}: {tool.description}")
        rendered = "\n".join(matches) or "No matching tools."
        return ToolExecutionResult(model_content=rendered, summary=f"tool_search {query}")


class ListMcpResourcesTool(BaseTool):
    name = "list_mcp_resources"
    description = "List configured local MCP resources."
    search_hint = "inspect MCP resource catalog"
    permission_type = "read"

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server": {"type": "string"},
            },
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        rendered = context.services.mcp_manager.render_resources(args.get("server"))
        return ToolExecutionResult(model_content=rendered, summary="list_mcp_resources")


class ReadMcpResourceTool(BaseTool):
    name = "read_mcp_resource"
    description = "Read the content of a configured local MCP resource."
    search_hint = "read an MCP resource into the current context"
    permission_type = "read"

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "server": {"type": "string"},
            },
            "required": ["name"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        content = context.services.mcp_manager.read_resource(str(args.get("name", "")), args.get("server"))
        for resource in context.services.mcp_manager.list_resources(args.get("server")):
            if resource.get("name") == str(args.get("name", "")):
                context.services.context_files.record(resource.get("path", ""))
                break
        return ToolExecutionResult(model_content=content, summary=f"read_mcp_resource {args.get('name', '')}")


class EnterWorktreeTool(BaseTool):
    name = "enter_worktree"
    description = "Switch the active workspace to another worktree or directory."
    search_hint = "enter another worktree or directory"
    permission_type = "edit"

    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        }

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        message = context.switch_workspace(str(args.get("path", "")))
        return ToolExecutionResult(model_content=message, summary="enter_worktree")


class ExitWorktreeTool(BaseTool):
    name = "exit_worktree"
    description = "Return to the primary workspace root."
    search_hint = "return to the primary workspace"
    permission_type = "edit"

    def input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        del args
        message = context.reset_workspace()
        return ToolExecutionResult(model_content=message, summary="exit_worktree")


class AgentTaskTool(BaseTool):
    name = "task"
    description = "Delegate a bounded sub-task to a temporary subagent."
    search_hint = "delegate bounded work to a temporary subagent"
    permission_type = "coordination"
    usage_policy = "Use for bounded, self-contained subtasks rather than urgent blocking work that needs tight local control."
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
        ToolSearchTool(),
        ListMcpResourcesTool(),
        ReadMcpResourceTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        AgentTaskTool(),
    ]


def _read_tracker_for(context: Any) -> ReadTracker:
    tracker = getattr(context.services, "read_file_tracker", None)
    if tracker is None:
        tracker = ReadTracker()
        context.services.read_file_tracker = tracker
    return tracker
