"""Grouped command registrations for the expanding command surface."""

from .analysis import register_analysis_commands
from .diagnostics import register_diagnostic_commands
from .events import register_event_commands
from .inspection import register_inspection_commands
from .session_ops import register_session_ops_commands
from .workflow import register_workflow_commands
from .workspace import register_workspace_commands

__all__ = [
    "register_analysis_commands",
    "register_diagnostic_commands",
    "register_event_commands",
    "register_inspection_commands",
    "register_session_ops_commands",
    "register_workflow_commands",
    "register_workspace_commands",
]
