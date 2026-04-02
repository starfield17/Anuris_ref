from __future__ import annotations

import json
import shlex
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


@dataclass(frozen=True)
class LoopProgressDecision:
    stall_reason: str = ""
    low_value_repetition: bool = False


class LoopProgressGuard:
    """Detect repeated no-progress tool batches."""

    def __init__(self, threshold: int = STALL_THRESHOLD):
        self.threshold = threshold
        self._last_fingerprint = ""
        self._repeat_count = 0
        self._last_read_targets: tuple[str, ...] = ()

    def record(
        self,
        tool_calls: Iterable[ToolCall],
        tool_results: Iterable[ConversationMessage],
    ) -> LoopProgressDecision:
        call_list = list(tool_calls)
        result_list = list(tool_results)
        fingerprint = _tool_batch_fingerprint(call_list, result_list)
        if not fingerprint:
            self._last_fingerprint = ""
            self._repeat_count = 0
            self._last_read_targets = ()
            return LoopProgressDecision()
        if fingerprint == self._last_fingerprint:
            self._repeat_count += 1
        else:
            self._last_fingerprint = fingerprint
            self._repeat_count = 1
        read_targets = _extract_read_targets(call_list)
        low_value_repetition = (
            bool(read_targets)
            and read_targets == self._last_read_targets
            and _results_are_low_value(result_list)
        )
        self._last_read_targets = read_targets
        if self._repeat_count >= self.threshold:
            return LoopProgressDecision(
                stall_reason=f"Tool loop stalled after {self._repeat_count} repeated batches.",
                low_value_repetition=low_value_repetition,
            )
        return LoopProgressDecision(low_value_repetition=low_value_repetition)


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


def tool_batch_fingerprint(
    tool_calls: Iterable[ToolCall],
    tool_results: Iterable[ConversationMessage],
) -> str:
    return _tool_batch_fingerprint(tool_calls, tool_results)


def _normalize_json(value: str) -> Any:
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        return value


def _extract_read_targets(tool_calls: List[ToolCall]) -> tuple[str, ...]:
    targets: list[str] = []
    for tool_call in tool_calls:
        if tool_call.name == "read_file":
            target = _read_file_target(tool_call.arguments_json)
        elif tool_call.name == "bash":
            target = _bash_read_target(tool_call.arguments_json)
        else:
            return ()
        if not target:
            return ()
        targets.append(target)
    return tuple(sorted(set(targets)))


def _read_file_target(arguments_json: str) -> str:
    payload = _normalize_json(arguments_json)
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("path", "")).strip()


def _bash_read_target(arguments_json: str) -> str:
    payload = _normalize_json(arguments_json)
    if not isinstance(payload, dict):
        return ""
    command = str(payload.get("command", "")).strip()
    if not command or any(token in command for token in ("|", "&&", "||", ";")):
        return ""
    parts = shlex.split(command)
    if not parts:
        return ""
    if parts[0] in {"cat", "head", "tail", "wc"}:
        return parts[-1] if len(parts) > 1 else ""
    if parts[0] == "sed" and "-n" in parts[1:]:
        return parts[-1] if len(parts) > 2 else ""
    return ""


def _results_are_low_value(tool_results: List[ConversationMessage]) -> bool:
    if not tool_results:
        return False
    for item in tool_results:
        metadata = item.metadata or {}
        if metadata.get("stored_externally") or metadata.get("unchanged_since_last_read"):
            continue
        if "Tool output stored externally at " in extract_text_content(item.content):
            continue
        return False
    return True
