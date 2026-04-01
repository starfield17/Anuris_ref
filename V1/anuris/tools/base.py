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
    search_hint = ""
    permission_type = "execute"
    usage_policy = ""
    requires_write = False
    coordination_tool = False

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self._schema_description(),
                "parameters": self.input_schema(),
            },
        }

    def input_schema(self) -> Dict[str, Any]:
        raise NotImplementedError

    def _schema_description(self) -> str:
        parts = [self.description.strip()]
        if self.search_hint:
            parts.append(f"Search hint: {self.search_hint.strip()}.")
        if self.usage_policy:
            parts.append(self.usage_policy.strip())
        return " ".join(part for part in parts if part)

    def compact_summary(self, args: Dict[str, Any]) -> str:
        return self.name

    def can_expose_detail(self, context: Any) -> Dict[str, Any]:
        denial = context.permission_context.explain_tool_denial(
            self.name,
            requires_write=self.requires_write,
            permission_type=self.permission_type,
        )
        return {"allowed": denial is None, "denial": denial}

    def can_expose(self, context: Any) -> bool:
        return self.can_expose_detail(context)["allowed"]

    def execute(self, args: Dict[str, Any], context: Any) -> ToolExecutionResult:
        raise NotImplementedError


class ToolPermissionError(PermissionError):
    """Structured permission failure raised by the tool registry."""

    def __init__(self, denial: Dict[str, Any]):
        self.denial = dict(denial)
        super().__init__(self.denial.get("message", "Tool permission denied"))
