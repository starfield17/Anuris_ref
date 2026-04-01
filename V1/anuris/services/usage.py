from __future__ import annotations

from datetime import datetime, timezone


class UsageTracker:
    """Lightweight session usage accounting for local cost/status commands."""

    def __init__(self):
        self.started_at = datetime.now(timezone.utc)
        self.query_count = 0
        self.tool_call_count = 0
        self.prompt_chars = 0
        self.response_chars = 0
        self.reasoning_chars = 0

    def record_query(self, prompt: str) -> None:
        self.query_count += 1
        self.prompt_chars += len(prompt or "")

    def record_tool_call(self) -> None:
        self.tool_call_count += 1

    def record_response(self, final_text: str, reasoning_text: str = "") -> None:
        self.response_chars += len(final_text or "")
        self.reasoning_chars += len(reasoning_text or "")

    def elapsed_seconds(self) -> int:
        return int((datetime.now(timezone.utc) - self.started_at).total_seconds())

    def snapshot(self) -> dict[str, int]:
        return {
            "queries": self.query_count,
            "tool_calls": self.tool_call_count,
            "prompt_chars": self.prompt_chars,
            "response_chars": self.response_chars,
            "reasoning_chars": self.reasoning_chars,
            "elapsed_seconds": self.elapsed_seconds(),
        }

    def render(self) -> str:
        snapshot = self.snapshot()
        return "\n".join(
            [
                f"queries: {snapshot['queries']}",
                f"tool_calls: {snapshot['tool_calls']}",
                f"prompt_chars: {snapshot['prompt_chars']}",
                f"response_chars: {snapshot['response_chars']}",
                f"reasoning_chars: {snapshot['reasoning_chars']}",
                f"elapsed_seconds: {snapshot['elapsed_seconds']}",
                "estimated_cost: unavailable (provider usage metrics not yet wired)",
            ]
        )
