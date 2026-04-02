from __future__ import annotations

import json
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .messages import ToolCall


STREAM_TOOL_NAMELESS = "stream_tool_missing_name"
STREAM_TOOL_ARGS_INVALID = "stream_tool_invalid_arguments"
STREAM_TOOL_DUPLICATE_ID = "stream_tool_duplicate_id"


@dataclass(frozen=True)
class CompletionPayload:
    content: Any
    reasoning: str
    tool_calls: List[ToolCall]
    finish_reason: str = ""


@dataclass
class StreamToolFragment:
    order_key: str
    call_id: str = ""
    name: str = ""
    arguments_json: str = ""

    def append_arguments(self, value: Any) -> None:
        if value is None:
            return
        self.arguments_json += str(value)

    def to_tool_call(self) -> ToolCall:
        return ToolCall(
            id=self.call_id or f"tool_{self.order_key}",
            name=self.name,
            arguments_json=self.arguments_json or "{}",
        )


class StreamingAccumulator:
    """Accumulates streamed assistant output and tool-call fragments."""

    def __init__(self):
        self._content_parts: List[str] = []
        self._reasoning_parts: List[str] = []
        self._tool_fragments: "OrderedDict[str, StreamToolFragment]" = OrderedDict()
        self.finish_reason = ""

    def ingest(self, chunk: Any) -> Dict[str, Any]:
        choice = _extract_choice(chunk)
        delta = _extract_delta(choice)
        content = _coerce_text(_read_attr(delta, "content", ""))
        reasoning = str(
            _read_attr(delta, "reasoning_content", "")
            or _read_attr(delta, "reasoning", "")
            or ""
        )
        if content:
            self._content_parts.append(content)
        if reasoning:
            self._reasoning_parts.append(reasoning)
        self.finish_reason = str(_read_attr(choice, "finish_reason", "") or self.finish_reason)
        for index, entry in enumerate(_read_attr(delta, "tool_calls", []) or []):
            self._ingest_tool_call(index, entry)
        return {"content": content, "reasoning": reasoning}

    def build(self) -> CompletionPayload:
        tool_calls = [item.to_tool_call() for item in self._tool_fragments.values()]
        return CompletionPayload(
            content="".join(self._content_parts),
            reasoning="".join(self._reasoning_parts),
            tool_calls=tool_calls,
            finish_reason=self.finish_reason,
        )

    def validation_error(self) -> str:
        seen_ids: set[str] = set()
        for tool_call in [item.to_tool_call() for item in self._tool_fragments.values()]:
            if not tool_call.name.strip():
                return STREAM_TOOL_NAMELESS
            try:
                parsed = json.loads(tool_call.arguments_json or "{}")
            except json.JSONDecodeError:
                return STREAM_TOOL_ARGS_INVALID
            if not isinstance(parsed, dict):
                return STREAM_TOOL_ARGS_INVALID
            if tool_call.id in seen_ids:
                return STREAM_TOOL_DUPLICATE_ID
            seen_ids.add(tool_call.id)
        return ""

    def _ingest_tool_call(self, fallback_index: int, entry: Any) -> None:
        position = _read_attr(entry, "index", fallback_index)
        key = str(position)
        fragment = self._tool_fragments.setdefault(
            key,
            StreamToolFragment(order_key=key, call_id=str(_read_attr(entry, "id", ""))),
        )
        entry_id = str(_read_attr(entry, "id", "") or "")
        if entry_id:
            fragment.call_id = entry_id
        function = _read_attr(entry, "function", {})
        name = str(
            _read_attr(function, "name", "")
            or _read_attr(entry, "name", "")
            or ""
        ).strip()
        if name:
            fragment.name = name
        arguments = _read_attr(function, "arguments", None)
        if arguments is None and _read_attr(entry, "type", "") == "tool_use":
            arguments = json.dumps(_read_attr(entry, "input", {}))
        fragment.append_arguments(arguments)


def extract_completion_payload(response: Any) -> CompletionPayload:
    if hasattr(response, "choices"):
        choices = getattr(response, "choices", [])
        if choices:
            return _extract_choice_payload(choices[0].message, getattr(choices[0], "finish_reason", ""))
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices:
            choice = choices[0]
            return _extract_choice_payload(choice.get("message", {}), choice.get("finish_reason", ""))
        return _extract_choice_payload(response, response.get("finish_reason", ""))
    raise ValueError("Unsupported completion response shape")


def normalize_tool_calls(tool_calls: Any) -> List[ToolCall]:
    normalized: List[ToolCall] = []
    for item in tool_calls or []:
        function = _read_attr(item, "function", {})
        if _read_attr(item, "type", "") == "tool_use":
            normalized.append(
                ToolCall(
                    id=str(_read_attr(item, "id", uuid.uuid4().hex[:8])),
                    name=str(_read_attr(item, "name", "")),
                    arguments_json=json.dumps(_read_attr(item, "input", {})),
                )
            )
            continue
        normalized.append(
            ToolCall(
                id=str(_read_attr(item, "id", uuid.uuid4().hex[:8])),
                name=str(_read_attr(function, "name", _read_attr(item, "name", ""))),
                arguments_json=str(
                    _read_attr(function, "arguments", _read_attr(item, "arguments", "{}"))
                ),
            )
        )
    return normalized


def normalize_content(content: Any) -> Any:
    if not isinstance(content, list):
        return content or ""
    blocks: List[Dict[str, Any]] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            blocks.append({"type": "text", "text": str(item.get("text", ""))})
            continue
        if getattr(item, "type", "") == "text":
            blocks.append({"type": "text", "text": str(getattr(item, "text", ""))})
    return blocks or ""


def parse_tool_arguments(arguments_json: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Tool arguments were not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must decode to an object")
    return parsed


def _extract_choice_payload(message: Any, finish_reason: Any) -> CompletionPayload:
    return CompletionPayload(
        content=normalize_content(_read_attr(message, "content", "")),
        reasoning=str(_read_attr(message, "reasoning_content", "") or ""),
        tool_calls=normalize_tool_calls(_read_attr(message, "tool_calls", [])),
        finish_reason=str(finish_reason or ""),
    )


def _extract_choice(chunk: Any) -> Any:
    if isinstance(chunk, dict):
        return (chunk.get("choices") or [{}])[0]
    return (getattr(chunk, "choices", None) or [{}])[0]


def _extract_delta(choice: Any) -> Any:
    if isinstance(choice, dict):
        return choice.get("delta", choice.get("message", {}))
    return getattr(choice, "delta", getattr(choice, "message", None))


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    parts: List[str] = []
    for item in value or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
            continue
        if hasattr(item, "text"):
            parts.append(str(getattr(item, "text", "")))
    return "".join(parts)


def _read_attr(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)
