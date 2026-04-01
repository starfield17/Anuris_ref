"""Session-scoped services for the expanded Claude Code-style runtime."""

from .context_budget import ContextBudgetService
from .context_files import ContextFileTracker
from .context_viz import ContextVisualizer
from .diagnostics_ext import DiagnosticsService
from .hooks import HookManager
from .mcp import MCPManager
from .memory import MemoryManager
from .notifications import NotificationCenter
from .permissions import PermissionManager
from .plugins import PluginManager
from .runtime_watch import RuntimeWatcher
from .search import WorkspaceSearch
from .sessions import SessionCatalog
from .settings import SettingsManager
from .usage import UsageTracker
from .worktree import WorktreeManager

__all__ = [
    "ContextBudgetService",
    "ContextFileTracker",
    "ContextVisualizer",
    "DiagnosticsService",
    "HookManager",
    "MCPManager",
    "MemoryManager",
    "NotificationCenter",
    "PermissionManager",
    "PluginManager",
    "RuntimeWatcher",
    "WorkspaceSearch",
    "SessionCatalog",
    "SettingsManager",
    "UsageTracker",
    "WorktreeManager",
]
