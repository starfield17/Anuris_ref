from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..engine.messages import extract_text_content


@dataclass
class BudgetSlice:
    name: str
    char_count: int
    item_count: int = 0
    details: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "char_count": self.char_count,
            "item_count": self.item_count,
            "details": list(self.details),
        }


@dataclass
class ContextBudgetSnapshot:
    approx_chars: int
    soft_limit: int
    hard_limit: int
    slices: Dict[str, BudgetSlice]
    top_consumers: List[Dict[str, Any]]
    compact_focus: str
    compact_reason: str
    should_compact: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approx_chars": self.approx_chars,
            "soft_limit": self.soft_limit,
            "hard_limit": self.hard_limit,
            "slices": {key: value.to_dict() for key, value in self.slices.items()},
            "top_consumers": list(self.top_consumers),
            "compact_focus": self.compact_focus,
            "compact_reason": self.compact_reason,
            "should_compact": self.should_compact,
        }


class ContextBudgetService:
    """Analyze session context pressure and recommend compaction strategy."""

    def __init__(self, session: Any, soft_limit: int = 18000, hard_limit: int = 26000):
        self.session = session
        self.soft_limit = int(soft_limit)
        self.hard_limit = int(hard_limit)

    def analyze(self, pending_prompt: str = "") -> ContextBudgetSnapshot:
        store = self.session.session_store
        context_files = self.session.services.context_files
        memory_manager = self.session.services.memory_manager
        notification_center = self.session.services.notification_center
        skill_loader = self.session.services.skill_loader

        slices: Dict[str, BudgetSlice] = {
            "conversation": BudgetSlice("conversation", 0, 0),
            "reasoning": BudgetSlice("reasoning", 0, 0),
            "tool_results": BudgetSlice("tool_results", 0, 0),
            "file_reads": BudgetSlice("file_reads", 0, 0),
            "memory": BudgetSlice("memory", 0, 0),
            "attachments": BudgetSlice("attachments", 0, 0),
            "compact_summaries": BudgetSlice("compact_summaries", 0, 0),
            "skills": BudgetSlice("skills", 0, 0),
            "runtime_notices": BudgetSlice("runtime_notices", 0, 0),
            "pending_prompt": BudgetSlice("pending_prompt", 0, 0),
        }
        top_consumers: List[Dict[str, Any]] = []

        for index, message in enumerate(store.messages, start=1):
            text = extract_text_content(message.content)
            text_chars = len(text)
            reasoning_chars = len(message.reasoning or "")
            if message.kind == "compact_boundary":
                slices["compact_summaries"].char_count += text_chars
                slices["compact_summaries"].item_count += 1
                slices["compact_summaries"].details.append(message.metadata.get("focus", "automatic") or "automatic")
                top_consumers.append(
                    {
                        "kind": "compact_boundary",
                        "index": index,
                        "char_count": text_chars,
                        "preview": message.preview(140),
                    }
                )
                continue
            if message.role in {"user", "assistant"}:
                slices["conversation"].char_count += text_chars
                slices["conversation"].item_count += 1
            if message.role == "tool":
                slices["tool_results"].char_count += text_chars
                slices["tool_results"].item_count += 1
            if reasoning_chars:
                slices["reasoning"].char_count += reasoning_chars
                slices["reasoning"].item_count += 1
            attachments = message.metadata.get("attachments", []) if isinstance(message.metadata, dict) else []
            if attachments:
                slices["attachments"].item_count += len(attachments)
                slices["attachments"].details.extend(str(item.get("name", item.get("path", "attachment"))) for item in attachments)
            if text_chars:
                top_consumers.append(
                    {
                        "kind": f"{message.role}:{message.kind}",
                        "index": index,
                        "char_count": text_chars + reasoning_chars,
                        "preview": message.preview(140),
                    }
                )

        for path in context_files.list_paths():
            label = self._label_path(path)
            char_count = self._safe_char_count(path)
            slices["file_reads"].char_count += char_count
            slices["file_reads"].item_count += 1
            slices["file_reads"].details.append(label)
            top_consumers.append({"kind": "file", "index": label, "char_count": char_count, "preview": label})

        memory_text = memory_manager.read() if memory_manager else "No memory saved."
        if memory_text and memory_text != "No memory saved.":
            slices["memory"].char_count = len(memory_text)
            slices["memory"].item_count = 1
            slices["memory"].details.append("workspace memory")

        if notification_center is not None:
            recent_notices = notification_center.recent(limit=10)
            for notice in recent_notices:
                message = str(notice.get("display_message", notice.get("message", "")))
                slices["runtime_notices"].char_count += len(message)
                slices["runtime_notices"].item_count += 1
                slices["runtime_notices"].details.append(message[:80])

        skill_descriptions = skill_loader.descriptions() if skill_loader else ""
        if skill_descriptions and skill_descriptions != "(no skills available)":
            slices["skills"].char_count = len(skill_descriptions)
            slices["skills"].item_count = len(getattr(skill_loader, "skills", {}) or {})

        if pending_prompt.strip():
            slices["pending_prompt"].char_count = len(pending_prompt.strip())
            slices["pending_prompt"].item_count = 1
            slices["pending_prompt"].details.append(pending_prompt.strip()[:120])

        approx_chars = sum(item.char_count for item in slices.values())
        top_consumers = sorted(top_consumers, key=lambda item: item["char_count"], reverse=True)[:12]
        compact_focus, compact_reason = self._recommend_compact_focus(slices, approx_chars)
        should_compact = approx_chars >= self.soft_limit or slices["tool_results"].char_count >= max(4000, self.soft_limit // 4)
        if approx_chars >= self.hard_limit:
            should_compact = True
            compact_reason = compact_reason or f"context exceeded hard limit ({approx_chars}/{self.hard_limit} chars)"
        return ContextBudgetSnapshot(
            approx_chars=approx_chars,
            soft_limit=self.soft_limit,
            hard_limit=self.hard_limit,
            slices=slices,
            top_consumers=top_consumers,
            compact_focus=compact_focus,
            compact_reason=compact_reason,
            should_compact=should_compact,
        )

    def should_compact(self, pending_prompt: str = "") -> bool:
        return self.analyze(pending_prompt=pending_prompt).should_compact

    def render(self, pending_prompt: str = "") -> str:
        snapshot = self.analyze(pending_prompt=pending_prompt)
        lines = [
            "Context budget:",
            f"- approx_chars: {snapshot.approx_chars}",
            f"- soft_limit: {snapshot.soft_limit}",
            f"- hard_limit: {snapshot.hard_limit}",
            f"- should_compact: {snapshot.should_compact}",
            f"- compact_focus: {snapshot.compact_focus or 'automatic'}",
            f"- compact_reason: {snapshot.compact_reason or 'within budget'}",
            "",
            "Slices:",
        ]
        for key, value in snapshot.slices.items():
            lines.append(f"- {key}: {value.item_count} item(s), {value.char_count} chars")
        return "\n".join(lines)

    def _recommend_compact_focus(self, slices: Dict[str, BudgetSlice], approx_chars: int) -> tuple[str, str]:
        ranked = sorted(
            (
                (key, value.char_count)
                for key, value in slices.items()
                if key not in {"pending_prompt"} and value.char_count > 0
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        if not ranked:
            return "", ""
        dominant, dominant_chars = ranked[0]
        if approx_chars >= self.hard_limit:
            return dominant, f"context exceeded hard limit; dominant slice is {dominant} ({dominant_chars} chars)"
        if approx_chars >= self.soft_limit:
            return dominant, f"context exceeded soft limit; dominant slice is {dominant} ({dominant_chars} chars)"
        if dominant == "tool_results" and dominant_chars >= max(4000, self.soft_limit // 4):
            return dominant, f"tool output is dominating context ({dominant_chars} chars)"
        if dominant == "reasoning" and dominant_chars >= max(3000, self.soft_limit // 5):
            return dominant, f"reasoning history is dominating context ({dominant_chars} chars)"
        return dominant, ""

    def _label_path(self, path: Any) -> str:
        try:
            return str(path.relative_to(self.session.workspace_root))
        except Exception:
            return str(path)

    @staticmethod
    def _safe_char_count(path: Any) -> int:
        try:
            path_obj = path if hasattr(path, "is_file") else None
            if path_obj is not None and path_obj.is_file():
                return len(path_obj.read_text(encoding="utf-8"))
        except Exception:
            return 0
        return 0
