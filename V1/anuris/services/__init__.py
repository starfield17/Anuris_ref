"""Session-scoped services for the expanded Claude Code-style runtime."""

from .context_files import ContextFileTracker
from .hooks import HookManager
from .mcp import MCPManager
from .permissions import PermissionManager
from .plugins import PluginManager
from .sessions import SessionCatalog
from .settings import SettingsManager
from .usage import UsageTracker
from .worktree import WorktreeManager

__all__ = [
    "ContextFileTracker",
    "HookManager",
    "MCPManager",
    "PermissionManager",
    "PluginManager",
    "SessionCatalog",
    "SettingsManager",
    "UsageTracker",
    "WorktreeManager",
]
