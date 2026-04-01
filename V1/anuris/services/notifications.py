from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RuntimeNotice:
    """Display-oriented runtime notice with queue and collapse metadata."""

    id: str
    message: str
    kind: str = "runtime"
    tone: str = "info"
    channel: str = "runtime"
    priority: int = 50
    defer_until: str = "post_turn"
    collapse_key: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_stamp)
    count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "message": self.message,
            "display_message": self.display_message(),
            "kind": self.kind,
            "tone": self.tone,
            "channel": self.channel,
            "priority": self.priority,
            "defer_until": self.defer_until,
            "collapse_key": self.collapse_key,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "count": self.count,
        }

    def display_message(self) -> str:
        if self.count <= 1:
            return self.message
        last_message = str(self.metadata.get("last_message", "") or "").strip()
        if last_message and last_message != self.message:
            return f"{self.message} (+{self.count - 1}; latest: {last_message})"
        return f"{self.message} ×{self.count}"


class NotificationCenter:
    """Queue for runtime notices with collapse and defer policies."""

    def __init__(self):
        self._queue: List[RuntimeNotice] = []
        self._recent: deque[RuntimeNotice] = deque(maxlen=200)

    def enqueue(
        self,
        message: str,
        *,
        kind: str = "runtime",
        tone: str = "info",
        channel: str = "runtime",
        priority: int = 50,
        defer_until: str = "post_turn",
        collapse_key: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[RuntimeNotice]:
        content = str(message or "").strip()
        if not content:
            return None
        notice = RuntimeNotice(
            id=uuid4().hex[:12],
            message=content,
            kind=kind,
            tone=tone,
            channel=channel,
            priority=int(priority),
            defer_until=defer_until,
            collapse_key=collapse_key.strip(),
            metadata=dict(metadata or {}),
        )
        if notice.collapse_key:
            for existing in reversed(self._queue):
                if existing.collapse_key != notice.collapse_key or existing.defer_until != notice.defer_until:
                    continue
                existing.count += 1
                existing.metadata["last_message"] = notice.message
                existing.metadata["last_created_at"] = notice.created_at
                return existing
        self._queue.append(notice)
        return notice

    def enqueue_event(self, event_type: str, payload: Dict[str, Any]) -> Optional[RuntimeNotice]:
        event_type = str(event_type or "").strip()
        if not event_type:
            return None

        mapping = {
            "task_completed": {
                "tone": "success",
                "channel": "tasks",
                "priority": 80,
                "collapse_key": "tasks:completed",
            },
            "task_status_changed": {
                "tone": "warning",
                "channel": "tasks",
                "priority": 60,
                "collapse_key": "tasks:status",
            },
            "teammate_idle": {
                "tone": "info",
                "channel": "team",
                "priority": 40,
                "collapse_key": "team:idle",
            },
            "teammate_shutdown": {
                "tone": "warning",
                "channel": "team",
                "priority": 75,
                "collapse_key": "team:shutdown",
            },
            "teammate_status_changed": {
                "tone": "info",
                "channel": "team",
                "priority": 50,
                "collapse_key": "team:status",
            },
            "hook_failed": {
                "tone": "danger",
                "channel": "hooks",
                "priority": 90,
                "collapse_key": "hooks:failed",
            },
            "tool_rejected": {
                "tone": "danger",
                "channel": "permissions",
                "priority": 95,
                "collapse_key": "permissions:rejected",
            },
        }
        spec = mapping.get(event_type, {"tone": "info", "channel": "runtime", "priority": 45, "collapse_key": event_type})
        message = str(payload.get("message", "") or payload.get("content", "") or event_type.replace("_", " "))
        return self.enqueue(
            message,
            kind=event_type,
            tone=spec["tone"],
            channel=spec["channel"],
            priority=spec["priority"],
            collapse_key=spec["collapse_key"],
            metadata=payload,
        )

    def drain(self) -> List[Dict[str, Any]]:
        items = [notice.to_dict() for notice in self._queue]
        for notice in self._queue:
            self._recent.append(notice)
        self._queue.clear()
        return items

    def flush_ready(self, stage: str = "post_turn") -> List[RuntimeNotice]:
        ready: List[RuntimeNotice] = []
        remaining: List[RuntimeNotice] = []
        for notice in self._queue:
            if notice.defer_until == stage or (stage == "post_turn" and notice.defer_until == "immediate"):
                ready.append(notice)
            else:
                remaining.append(notice)
        self._queue = remaining
        for notice in sorted(ready, key=lambda item: (-item.priority, item.created_at)):
            self._recent.append(notice)
        return sorted(ready, key=lambda item: (-item.priority, item.created_at))

    def preview(self, include_recent: bool = False) -> str:
        notices: Iterable[RuntimeNotice] = self._queue
        if include_recent:
            notices = list(self._recent)[-20:]
        notices = list(notices)
        if not notices:
            return "No queued runtime notices."
        return "\n".join(
            f"- [{notice.channel}/{notice.tone}] {notice.display_message()}"
            for notice in notices
        )

    def summary_counts(self) -> Dict[str, Any]:
        queued = list(self._queue)
        channels: Dict[str, int] = {}
        tones: Dict[str, int] = {}
        for notice in queued:
            channels[notice.channel] = channels.get(notice.channel, 0) + notice.count
            tones[notice.tone] = tones.get(notice.tone, 0) + notice.count
        return {
            "queued": len(queued),
            "channels": channels,
            "tones": tones,
            "highest_priority": max((notice.priority for notice in queued), default=0),
        }

    def clear(self, channel: str = "") -> int:
        if not channel:
            count = len(self._queue)
            self._queue.clear()
            return count
        kept: List[RuntimeNotice] = []
        removed = 0
        for notice in self._queue:
            if notice.channel == channel:
                removed += 1
                continue
            kept.append(notice)
        self._queue = kept
        return removed

    def count(self) -> int:
        return len(self._queue)

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [notice.to_dict() for notice in list(self._recent)[-limit:]]
