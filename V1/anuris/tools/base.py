from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ToolExecutionResult:
    """Standardized tool result consumed by the query loop."""

    model_content: str
    display_content: str | None = None
    is_error: bool = False
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.display_content is None:
            self.display_content = self.model_content


class BaseTool:
    """Base class for all model-facing tools."""

    name = ""
    description = ""
    requires_write = False
    coordination_tool = False

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema(),
            },
        }

    def input_schema(self) -> Dict[str, Any]:
        raise NotImplementedError

    def can_expose(self, context: Any) -> bool:
        if not context.permission_context.permits_tool(self.name):
            return False
        if context.permission_context.mode == "readonly" and self.requires_write:
            return False
        return True

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        raise NotImplementedError
