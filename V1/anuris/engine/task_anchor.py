from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from .messages import ConversationMessage

MAX_SECTION_CHARS = 280
MAX_PROGRESS_ITEMS = 6


@dataclass(frozen=True)
class TaskAnchor:
    original_goal: str = ""
    current_plan: str = ""
    constraints: str = ""
    latest_progress: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "original_goal": self.original_goal,
            "current_plan": self.current_plan,
            "constraints": self.constraints,
            "latest_progress": self.latest_progress,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "TaskAnchor":
        raw = payload or {}
        return cls(
            original_goal=_trim(raw.get("original_goal", "")),
            current_plan=_trim(raw.get("current_plan", "")),
            constraints=_trim(raw.get("constraints", "")),
            latest_progress=_trim(raw.get("latest_progress", "")),
            updated_at=str(raw.get("updated_at", "") or ""),
        )


def build_task_anchor(prompt: str, session_id: str, services: Any, previous: Dict[str, Any] | None = None) -> TaskAnchor:
    earlier = TaskAnchor.from_dict(previous)
    goal = _trim(prompt) or earlier.original_goal
    plan = _first_non_empty(_todo_summary(services), _task_summary(services), earlier.current_plan)
    constraints = _first_non_empty(_memory_summary(services, session_id), earlier.constraints)
    return TaskAnchor(
        original_goal=goal,
        current_plan=plan,
        constraints=constraints,
        latest_progress=earlier.latest_progress,
        updated_at=_utc_now(),
    )


def update_task_anchor_progress(previous: Dict[str, Any] | None, final_text: str, tool_events: Iterable[str]) -> TaskAnchor:
    current = TaskAnchor.from_dict(previous)
    latest = _first_non_empty(_progress_summary(final_text, tool_events), current.latest_progress)
    return TaskAnchor(
        original_goal=current.original_goal,
        current_plan=current.current_plan,
        constraints=current.constraints,
        latest_progress=latest,
        updated_at=_utc_now(),
    )


def render_task_anchor_message(anchor_payload: Dict[str, Any] | None) -> str:
    anchor = TaskAnchor.from_dict(anchor_payload)
    sections = _anchor_sections(anchor)
    if not sections:
        return ""
    return "\n".join(
        [
            "Task anchor for the current request. Preserve this objective across long turns and compaction.",
            *sections,
        ]
    )


def render_continuation_anchor(anchor_payload: Dict[str, Any] | None) -> str:
    anchor = TaskAnchor.from_dict(anchor_payload)
    sections = _anchor_sections(anchor, compact=True)
    if not sections:
        return ""
    return "\n".join(["Current task anchor:", *sections])


def render_compaction_summary(anchor_payload: Dict[str, Any] | None, focus: str, removable: list[ConversationMessage]) -> str:
    anchor = TaskAnchor.from_dict(anchor_payload)
    lines = ["Conversation compacted into a working summary."]
    if focus.strip():
        lines.append(f"Focus: {focus.strip()}")
    sections = _anchor_sections(anchor)
    if sections:
        lines.extend(["", "Preserved task anchor:", *sections])
    progress = _message_progress(removable)
    if progress:
        lines.extend(["", "Completed so far:"])
        lines.extend(progress)
    return "\n".join(lines)


def _anchor_sections(anchor: TaskAnchor, compact: bool = False) -> list[str]:
    sections: list[str] = []
    if anchor.original_goal:
        sections.append(f"- Original goal: {anchor.original_goal}")
    if anchor.current_plan:
        sections.append(f"- Current plan: {anchor.current_plan}")
    if anchor.constraints:
        label = "Constraints" if not compact else "Constraints to keep"
        sections.append(f"- {label}: {anchor.constraints}")
    if anchor.latest_progress:
        sections.append(f"- Latest progress: {anchor.latest_progress}")
    return sections


def _todo_summary(services: Any) -> str:
    manager = getattr(services, "todo_manager", None)
    items = getattr(manager, "items", []) if manager else []
    if not items:
        return ""
    active = [item for item in items if str(item.get("status", "")) == "in_progress"]
    pending = [item for item in items if str(item.get("status", "")) == "pending"]
    selected = active[:1] + pending[:2]
    return _trim("; ".join(str(item.get("content", "") or "") for item in selected if item.get("content")))


def _task_summary(services: Any) -> str:
    manager = getattr(services, "task_manager", None)
    if manager is None or not hasattr(manager, "resumable_task"):
        return ""
    task = manager.resumable_task("lead") or manager.resumable_task()
    if not task:
        return ""
    subject = str(task.get("subject", "") or "").strip()
    status = str(task.get("status", "") or "").strip()
    if not subject:
        return ""
    return _trim(f"{subject} [{status}]")


def _memory_summary(services: Any, session_id: str) -> str:
    manager = getattr(services, "memory_manager", None)
    if manager is None:
        return ""
    session_text = _normalize_memory(manager.read_session(session_id))
    workspace_text = _normalize_memory(manager.read())
    return _first_non_empty(session_text, workspace_text)


def _normalize_memory(value: str) -> str:
    text = str(value or "").strip()
    if not text or text == "No memory saved.":
        return ""
    first_lines = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
    return _trim("; ".join(first_lines[:3]))


def _progress_summary(final_text: str, tool_events: Iterable[str]) -> str:
    text = _trim(final_text)
    if text:
        return text
    summaries = [_trim(item) for item in tool_events if _trim(item)]
    if not summaries:
        return ""
    return _trim("; ".join(summaries[-3:]))


def _message_progress(messages: list[ConversationMessage]) -> list[str]:
    items: list[str] = []
    for message in messages[-MAX_PROGRESS_ITEMS:]:
        if message.role not in {"assistant", "tool"}:
            continue
        snippet = _trim(message.preview(180))
        if snippet:
            items.append(f"- {message.role.upper()}: {snippet}")
    return items


def _first_non_empty(*values: str) -> str:
    for value in values:
        trimmed = _trim(value)
        if trimmed:
            return trimmed
    return ""


def _trim(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= MAX_SECTION_CHARS:
        return text
    return text[: MAX_SECTION_CHARS - 3].rstrip() + "..."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
