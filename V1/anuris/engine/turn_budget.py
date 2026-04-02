from __future__ import annotations

from dataclasses import dataclass


DEFAULT_BASE_TURN_LIMIT = 24
DEFAULT_TURN_EXTENSION_STEP = 12
DEFAULT_MAX_TURN_LIMIT = 240
DEFAULT_SUBAGENT_BASE_TURN_LIMIT = 16
DEFAULT_SUBAGENT_TURN_EXTENSION_STEP = 8
DEFAULT_SUBAGENT_MAX_TURN_LIMIT = 120
NEAR_LIMIT_THRESHOLD = 2
CONTINUATION_EMERGENCY_THRESHOLD = 2


@dataclass(frozen=True)
class RoundProgress:
    tool_batch_changed: bool = False
    assistant_text: bool = False
    continuation: bool = False
    compaction: bool = False

    def made_progress(self) -> bool:
        return any(
            (
                self.tool_batch_changed,
                self.assistant_text,
                self.continuation,
                self.compaction,
            )
        )

    def reason_text(self) -> str:
        reasons: list[str] = []
        if self.tool_batch_changed:
            reasons.append("tool_batch_changed")
        if self.assistant_text:
            reasons.append("assistant_text")
        if self.continuation:
            reasons.append("continuation")
        if self.compaction:
            reasons.append("compaction")
        return ", ".join(reasons)


@dataclass(frozen=True)
class TurnBudgetDecision:
    extended: bool = False
    exhausted: bool = False
    previous_limit: int = 0
    new_limit: int = 0
    extension_count: int = 0
    reason: str = ""


class AdaptiveTurnBudget:
    def __init__(
        self,
        base_limit: int = DEFAULT_BASE_TURN_LIMIT,
        extension_step: int = DEFAULT_TURN_EXTENSION_STEP,
        max_limit: int = DEFAULT_MAX_TURN_LIMIT,
    ):
        self.base_limit = max(1, int(base_limit))
        self.extension_step = max(1, int(extension_step))
        self.max_limit = max(self.base_limit, int(max_limit))
        self.current_limit = self.base_limit
        self.extension_count = 0

    def remaining_turns(self, round_index: int) -> int:
        return self.current_limit - round_index + 1

    def should_emergency_compact(self, round_index: int, continuation_count: int) -> bool:
        return self.remaining_turns(round_index) <= NEAR_LIMIT_THRESHOLD or continuation_count >= CONTINUATION_EMERGENCY_THRESHOLD

    def ensure_capacity(self, next_round: int, progress: RoundProgress) -> TurnBudgetDecision:
        if next_round <= self.current_limit:
            return TurnBudgetDecision()
        if not progress.made_progress():
            return TurnBudgetDecision(
                exhausted=True,
                previous_limit=self.current_limit,
                new_limit=self.current_limit,
                extension_count=self.extension_count,
                reason="no real progress before the turn budget boundary",
            )
        if self.current_limit >= self.max_limit:
            return TurnBudgetDecision(
                exhausted=True,
                previous_limit=self.current_limit,
                new_limit=self.current_limit,
                extension_count=self.extension_count,
                reason=f"reached max turn budget {self.max_limit}",
            )
        previous_limit = self.current_limit
        self.current_limit = min(self.max_limit, self.current_limit + self.extension_step)
        self.extension_count += 1
        return TurnBudgetDecision(
            extended=True,
            previous_limit=previous_limit,
            new_limit=self.current_limit,
            extension_count=self.extension_count,
            reason=progress.reason_text(),
        )
