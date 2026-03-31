from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..agent.skills import SkillLoader
from ..agent.tasks import PersistentTaskManager
from ..agent.todo import TodoManager

EventCallback = Callable[[Dict[str, Any]], None]
SubagentRunner = Callable[[str, str, bool], str]


@dataclass
class PermissionContext:
    """Minimal permission state inspired by Claude Code tool gating."""

    mode: str = "default"
    allowed_tools: Optional[set[str]] = None

    def permits_tool(self, tool_name: str) -> bool:
        if self.allowed_tools is None:
            return True
        return tool_name in self.allowed_tools


@dataclass
class SessionServices:
    """Session-scoped mutable services used by tools and commands."""

    todo_manager: TodoManager
    task_manager: PersistentTaskManager
    skill_loader: SkillLoader


@dataclass
class ToolUseContext:
    """Context passed into every tool invocation."""

    workspace_root: Path
    session_store: Any
    services: SessionServices
    permission_context: PermissionContext
    emit_event: EventCallback
    run_subagent: SubagentRunner
    config: Any
    ui: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
