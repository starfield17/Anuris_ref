from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .base import ToolPermissionError


class ToolRegistry:
    """Registry and filter layer for active tools."""

    def __init__(self, tools: Iterable[Any]):
        self.tools = list(tools)
        self.by_name: Dict[str, Any] = {tool.name: tool for tool in self.tools}

    def list_tools(self, context: Any, allowed_tool_names: Optional[set[str]] = None) -> List[Any]:
        result = []
        for tool in self.tools:
            if allowed_tool_names is not None and tool.name not in allowed_tool_names:
                continue
            if tool.can_expose(context):
                result.append(tool)
        return result

    def get_schemas(self, context: Any, allowed_tool_names: Optional[set[str]] = None) -> List[Dict[str, Any]]:
        return [tool.schema() for tool in self.list_tools(context, allowed_tool_names=allowed_tool_names)]

    def require(self, name: str, context: Any, allowed_tool_names: Optional[set[str]] = None) -> Any:
        tool = self.by_name.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        if allowed_tool_names is not None and name not in allowed_tool_names:
            raise ToolPermissionError(
                {
                    "reason_code": "not_enabled_in_context",
                    "message": f"Tool {name} is not enabled in this context.",
                    "tool_name": name,
                    "mode": context.permission_context.mode,
                }
            )
        decision = tool.can_expose_detail(context)
        if not decision["allowed"]:
            raise ToolPermissionError(decision["denial"] or {"message": f"Tool {name} is not permitted."})
        return tool
