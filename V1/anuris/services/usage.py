from __future__ import annotations


class UsageTracker:
    """Lightweight session usage accounting for local cost/status commands."""

    def __init__(self):
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

    def render(self) -> str:
        return "\n".join(
            [
                f"queries: {self.query_count}",
                f"tool_calls: {self.tool_call_count}",
                f"prompt_chars: {self.prompt_chars}",
                f"response_chars: {self.response_chars}",
                f"reasoning_chars: {self.reasoning_chars}",
                "estimated_cost: unavailable (provider usage metrics not yet wired)",
            ]
        )
