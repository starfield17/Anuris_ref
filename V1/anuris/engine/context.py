from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..agent.skills import SkillLoader
from ..agent.tasks import PersistentTaskManager
from ..agent.todo import TodoManager

EventCallback = Callable[[Dict[str, Any]], None]
SubagentRunner = Callable[[str, str, bool], str]
WorkspaceSwitcher = Callable[[str], str]
WorkspaceResetter = Callable[[], str]


@dataclass
class PermissionContext:
    """Minimal permission state inspired by Claude Code tool gating."""

    mode: str = "default"
    allowed_tools: Optional[set[str]] = None
    denied_tools: Optional[set[str]] = None
    sandbox_mode: str = "workspace-write"
    excluded_commands: tuple[str, ...] = ()

    def permits_tool(self, tool_name: str) -> bool:
        return self.explain_tool_denial(tool_name) is None

    def explain_tool_denial(
        self,
        tool_name: str,
        *,
        requires_write: bool = False,
        permission_type: str = "execute",
    ) -> Optional[Dict[str, Any]]:
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return {
                "reason_code": "not_enabled_in_context",
                "message": f"Tool {tool_name} is not enabled in this context.",
                "mode": self.mode,
                "tool_name": tool_name,
                "permission_type": permission_type,
            }
        if self.denied_tools is not None and tool_name in self.denied_tools:
            return {
                "reason_code": "denied_by_mode",
                "message": f"Tool {tool_name} is denied in {self.mode} mode.",
                "mode": self.mode,
                "tool_name": tool_name,
                "permission_type": permission_type,
            }
        if self.mode == "readonly" and (requires_write or permission_type in {"edit", "write"}):
            return {
                "reason_code": "readonly_requires_write",
                "message": f"Tool {tool_name} requires write access, but the current mode is readonly.",
                "mode": self.mode,
                "tool_name": tool_name,
                "permission_type": permission_type,
            }
        return None

    def permits_command(self, command: str) -> bool:
        return self.explain_command_denial(command) is None

    def explain_command_denial(self, command: str) -> Optional[Dict[str, Any]]:
        normalized = command.strip().lower()
        if not normalized:
            return None
        for pattern in self.excluded_commands:
            token = str(pattern or "").strip().lower()
            if token and token in normalized:
                return {
                    "reason_code": "excluded_command_pattern",
                    "message": f"Command matches excluded sandbox pattern: {pattern}",
                    "mode": self.mode,
                    "sandbox_mode": self.sandbox_mode,
                    "pattern": pattern,
                }
        return None


@dataclass
class SessionServices:
    """Session-scoped mutable services used by tools and commands."""

    todo_manager: TodoManager
    task_manager: PersistentTaskManager
    skill_loader: SkillLoader
    permission_manager: Any
    session_catalog: Any
    worktree_manager: Any
    plugin_manager: Any
    mcp_manager: Any
    settings_manager: Any
    hook_manager: Any
    context_files: Any
    usage_tracker: Any
    memory_manager: Any = None
    notification_center: Any = None
    runtime_watcher: Any = None
    context_visualizer: Any = None
    search_service: Any = None
    diagnostics: Any = None
    context_budget: Any = None
    runtime_state: Any = None
    run_manager: Any = None
    runtime_queue: Any = None
    tool_result_store: Any = None
    read_file_tracker: Any = None


@dataclass
class ToolUseContext:
    """Context passed into every tool invocation."""

    workspace_root: Path
    session_store: Any
    services: SessionServices
    permission_context: PermissionContext
    emit_event: EventCallback
    run_subagent: SubagentRunner
    switch_workspace: WorkspaceSwitcher
    reset_workspace: WorkspaceResetter
    config: Any
    ui: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
