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
    sandbox_mode: str = "workspace-write"
    excluded_commands: tuple[str, ...] = ()

    def permits_tool(self, tool_name: str) -> bool:
        if self.allowed_tools is None:
            return True
        return tool_name in self.allowed_tools

    def permits_command(self, command: str) -> bool:
        normalized = command.strip().lower()
        if not normalized:
            return True
        for pattern in self.excluded_commands:
            token = str(pattern or "").strip().lower()
            if token and token in normalized:
                return False
        return True


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
