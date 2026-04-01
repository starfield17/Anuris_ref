from __future__ import annotations

from typing import Optional

from ..engine.context import PermissionContext


class PermissionManager:
    """Local approximation of Claude Code permission modes."""

    VALID_MODES = {"default", "accept_edits", "readonly", "plan"}
    READONLY_DENY = {"write_file", "edit_file", "task_create", "task_update", "enter_worktree", "exit_worktree"}
    PLAN_ALLOWED = {
        "bash",
        "read_file",
        "glob",
        "grep",
        "load_skill",
        "todo_write",
        "task_get",
        "task_list",
        "list_mcp_resources",
        "read_mcp_resource",
        "tool_search",
    }

    def __init__(self, mode: str = "default"):
        self.mode = "default"
        self.set_mode(mode)

    def set_mode(self, mode: str) -> str:
        normalized = mode.strip().lower().replace("-", "_")
        if normalized not in self.VALID_MODES:
            raise ValueError(f"Unsupported permission mode: {mode}")
        self.mode = normalized
        return self.mode

    def create_context(
        self,
        *,
        agent_mode: bool,
        explicit_allowed_tools: Optional[set[str]] = None,
        sandbox_mode: str = "workspace-write",
        excluded_commands: Optional[list[str]] = None,
    ) -> PermissionContext:
        if not agent_mode:
            return PermissionContext(
                mode=self.mode,
                allowed_tools=set(),
                denied_tools=set(),
                sandbox_mode=sandbox_mode,
                excluded_commands=tuple(excluded_commands or ()),
            )

        allowed_tools = None if explicit_allowed_tools is None else set(explicit_allowed_tools)
        denied_tools: set[str] = set()
        effective_mode = self.mode
        if sandbox_mode == "read-only" and effective_mode == "default":
            effective_mode = "readonly"

        if effective_mode == "readonly":
            if allowed_tools is None:
                denied_tools |= self.READONLY_DENY
            else:
                allowed_tools -= self.READONLY_DENY
                denied_tools |= self.READONLY_DENY
        elif effective_mode == "plan":
            allowed_tools = set(self.PLAN_ALLOWED) if allowed_tools is None else (set(allowed_tools) & self.PLAN_ALLOWED)
            denied_tools |= self.READONLY_DENY
        return PermissionContext(
            mode=effective_mode,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools or None,
            sandbox_mode=sandbox_mode,
            excluded_commands=tuple(excluded_commands or ()),
        )

    def render(self) -> str:
        return f"permission_mode: {self.mode}"
