"""Grouped command registrations for the expanding command surface."""

from .analysis import register_analysis_commands
from .events import register_event_commands
from .inspection import register_inspection_commands

__all__ = ["register_analysis_commands", "register_event_commands", "register_inspection_commands"]
