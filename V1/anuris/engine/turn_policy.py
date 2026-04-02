from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from .messages import ConversationMessage, ToolCall, extract_text_content

CONTINUATION_MESSAGE = (
    "Continue directly from where you stopped. Do not apologize. "
    "Do not recap completed work. Finish the remaining task."
)
CONTINUATION_FINISH_REASONS = {"length", "max_tokens", "max_output_tokens"}
MAX_CONTINUATIONS = 3
STALL_THRESHOLD = 3


@dataclass(frozen=True)
class PairingIssue:
    missing_tool_results: List[str]
    orphaned_tool_results: List[str]

    @property
    def has_issue(self) -> bool:
        return bool(self.missing_tool_results or self.orphaned_tool_results)


class LoopProgressGuard:
    """Detect repeated no-progress tool batches."""

    def __init__(self, threshold: int = STALL_THRESHOLD):
        self.threshold = threshold
        self._last_fingerprint = ""
        self._repeat_count = 0

    def record(self, tool_calls: Iterable[ToolCall], tool_results: Iterable[ConversationMessage]) -> str:
        fingerprint = _tool_batch_fingerprint(tool_calls, tool_results)
        if not fingerprint:
            self._last_fingerprint = ""
            self._repeat_count = 0
            return ""
        if fingerprint == self._last_fingerprint:
            self._repeat_count += 1
        else:
            self._last_fingerprint = fingerprint
            self._repeat_count = 1
        if self._repeat_count >= self.threshold:
            return f"Tool loop stalled after {self._repeat_count} repeated batches."
        return ""


def continuation_message_for(finish_reason: str, continuation_count: int) -> str:
    if continuation_count >= MAX_CONTINUATIONS:
        return ""
    if str(finish_reason or "").strip().lower() not in CONTINUATION_FINISH_REASONS:
        return ""
    return CONTINUATION_MESSAGE


def validate_pairing(messages: List[ConversationMessage]) -> PairingIssue:
    pending: Dict[str, ToolCall] = {}
    missing: List[str] = []
    orphaned: List[str] = []
    for message in messages:
        if message.role == "assistant":
            for tool_call in message.tool_calls:
                pending[tool_call.id] = tool_call
            continue
        if message.role == "tool":
            tool_call_id = str(message.tool_call_id or "")
            if not tool_call_id or tool_call_id not in pending:
                orphaned.append(tool_call_id or "(missing)")
                continue
            pending.pop(tool_call_id, None)
    missing.extend(sorted(pending))
    return PairingIssue(missing_tool_results=missing, orphaned_tool_results=orphaned)


def tool_call_validation_error(tool_calls: Iterable[ToolCall]) -> str:
    seen_ids: set[str] = set()
    for tool_call in tool_calls:
        if not tool_call.name.strip():
            return "Tool call missing function name."
        if tool_call.id in seen_ids:
            return f"Duplicate tool call id: {tool_call.id}"
        seen_ids.add(tool_call.id)
        try:
            parsed = json.loads(tool_call.arguments_json or "{}")
        except json.JSONDecodeError as exc:
            return f"Invalid tool arguments for {tool_call.name}: {exc}"
        if not isinstance(parsed, dict):
            return f"Tool arguments for {tool_call.name} must decode to an object."
    return ""


def _tool_batch_fingerprint(
    tool_calls: Iterable[ToolCall],
    tool_results: Iterable[ConversationMessage],
) -> str:
    calls = [
        {
            "id": item.id,
            "name": item.name,
            "arguments": _normalize_json(item.arguments_json),
        }
        for item in tool_calls
    ]
    results = [
        {
            "id": str(item.tool_call_id or ""),
            "name": str(item.name or ""),
            "error": bool((item.metadata or {}).get("is_error", False)),
            "content": extract_text_content(item.content)[:400],
        }
        for item in tool_results
    ]
    if not calls and not results:
        return ""
    return json.dumps({"calls": calls, "results": results}, ensure_ascii=False, sort_keys=True)


def _normalize_json(value: str) -> Any:
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        return value
