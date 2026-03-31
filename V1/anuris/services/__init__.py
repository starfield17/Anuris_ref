"""Session-scoped services for the expanded Claude Code-style runtime."""

from .mcp import MCPManager
from .permissions import PermissionManager
from .plugins import PluginManager
from .sessions import SessionCatalog
from .settings import SettingsManager
from .worktree import WorktreeManager

__all__ = [
    "MCPManager",
    "PermissionManager",
    "PluginManager",
    "SessionCatalog",
    "SettingsManager",
    "WorktreeManager",
]
