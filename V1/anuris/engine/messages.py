from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def extract_text_content(content: Any) -> str:
    """Best-effort text extraction for transcript and compaction logic."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    parts.append(str(block.get("text", "")))
                elif block_type == "image_url":
                    parts.append("[image attachment]")
                else:
                    parts.append(str(block))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part).strip()
    if content is None:
        return ""
    return str(content)


@dataclass
class ToolCall:
    """Model-requested tool invocation."""

    id: str
    name: str
    arguments_json: str

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments_json,
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments_json": self.arguments_json,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ToolCall":
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            arguments_json=str(payload.get("arguments_json", "{}")),
        )


@dataclass
class ConversationMessage:
    """Normalized session message stored independently from provider objects."""

    role: str
    content: Any
    kind: str = "message"
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_api_message(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.role == "assistant" and self.tool_calls:
            payload["tool_calls"] = [item.to_api_dict() for item in self.tool_calls]
        if self.role == "tool" and self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = self.name
        if self.reasoning:
            payload["reasoning_content"] = self.reasoning
        return payload

    def preview(self, max_chars: int = 160) -> str:
        text = extract_text_content(self.content).replace("\n", " ").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "kind": self.kind,
            "name": self.name,
            "tool_call_id": self.tool_call_id,
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "reasoning": self.reasoning,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ConversationMessage":
        return cls(
            role=str(payload.get("role", "user")),
            content=payload.get("content", ""),
            kind=str(payload.get("kind", "message")),
            name=payload.get("name"),
            tool_call_id=payload.get("tool_call_id"),
            tool_calls=[ToolCall.from_dict(item) for item in payload.get("tool_calls", [])],
            reasoning=str(payload.get("reasoning", "")),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass
class EngineResponse:
    """Result from one QueryEngine submit() call."""

    final_text: str
    reasoning_text: str
    rounds: int
    tool_events: List[str] = field(default_factory=list)
