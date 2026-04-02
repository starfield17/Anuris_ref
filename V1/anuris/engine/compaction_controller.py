from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


MAX_COMPACTION_FAILURES = 3
TOOL_RESULT_KEEP_RECENT = 4
EMERGENCY_TOOL_RESULT_KEEP_RECENT = 2
DEFAULT_KEEP_LAST = 8
EMERGENCY_KEEP_LAST = 4


@dataclass
class CompactionDecision:
    attempted: bool
    summary: str = ""
    circuit_open: bool = False
    changed: bool = False


class CompactionController:
    """Coordinates micro-compaction and summary compaction with a circuit breaker."""

    def __init__(self, max_failures: int = MAX_COMPACTION_FAILURES, keep_recent_tool_results: int = TOOL_RESULT_KEEP_RECENT):
        self.max_failures = max_failures
        self.keep_recent_tool_results = keep_recent_tool_results
        self._consecutive_failures = 0

    @property
    def failures(self) -> int:
        return self._consecutive_failures

    def micro_compact_tool_results(self, messages: list[Any], *, emergency: bool = False) -> bool:
        tool_messages = [message for message in messages if getattr(message, "role", "") == "tool"]
        keep_recent = EMERGENCY_TOOL_RESULT_KEEP_RECENT if emergency else self.keep_recent_tool_results
        if len(tool_messages) <= keep_recent:
            return False
        retained = set(id(message) for message in tool_messages[-keep_recent:])
        changed = False
        for message in tool_messages[:-keep_recent]:
            if id(message) in retained:
                continue
            metadata = getattr(message, "metadata", {}) or {}
            artifact_path = str(metadata.get("artifact_path", "") or "")
            if not artifact_path:
                continue
            message.content = f"[Stored tool result retained at {artifact_path}]"
            changed = True
        return changed

    def compact(self, store: Any, focus: str, *, keep_last: int = DEFAULT_KEEP_LAST) -> CompactionDecision:
        if self._consecutive_failures >= self.max_failures:
            return CompactionDecision(attempted=False, circuit_open=True)
        try:
            summary = store.compact_history(focus, keep_last=keep_last)
        except Exception:
            self._consecutive_failures += 1
            raise
        self._consecutive_failures = 0
        return CompactionDecision(
            attempted=True,
            summary=summary,
            changed=summary != "Context is already compact.",
        )
