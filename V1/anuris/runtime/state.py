from __future__ import annotations

import queue
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .events import build_runtime_event
from .history import EventHistory
from .queue import RuntimeEventQueue
from .runs import RuntimeRunManager
from .tasks import RuntimeTaskManager


@dataclass
class RuntimeTurnState:
    request_id: str
    request_kind: str
    status: str = "running"
    rounds: int = 0
    tool_calls: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class RuntimeState:
    """Session-scoped runtime state and live event fanout."""

    def __init__(self, session_id: str, workspace_root: Path, event_path: Path, tasks_root: Path):
        self.session_id = session_id
        self.workspace_root = Path(workspace_root).resolve()
        self.project_root = self.workspace_root
        self.cwd = self.workspace_root
        self.permission_mode = "default"
        self.status = "idle"
        self.history = EventHistory(event_path)
        runtime_root = event_path.parent / "runtime"
        self.queue = RuntimeEventQueue(runtime_root / "queue")
        self.runs = RuntimeRunManager(runtime_root / "runs")
        self.tasks = RuntimeTaskManager(tasks_root)
        self._subscribers: List[queue.Queue] = []

    def begin_turn(self, request_id: str, request_kind: str, **metadata: Any) -> RuntimeTurnState:
        self.status = "running"
        turn = RuntimeTurnState(request_id=request_id, request_kind=request_kind, metadata=dict(metadata))
        self.publish("request_started", request_id=request_id, request_kind=request_kind, **metadata)
        return turn

    def finish_turn(self, turn: RuntimeTurnState, **payload: Any) -> Dict[str, Any]:
        self.status = "idle"
        turn.status = "completed"
        return self.publish("request_finished", request_id=turn.request_id, request_kind=turn.request_kind, **payload)

    def fail_turn(self, turn: RuntimeTurnState, error: str) -> Dict[str, Any]:
        self.status = "failed"
        turn.status = "failed"
        return self.publish("request_failed", request_id=turn.request_id, request_kind=turn.request_kind, error=error)

    def publish(self, event_type: str, **payload: Any) -> Dict[str, Any]:
        event = build_runtime_event(event_type, session_id=self.session_id, **payload)
        self.history.append(event)
        priority = "next"
        if event_type in {"request_started", "request_failed"}:
            priority = "now"
        elif event_type in {"task_completed", "teammate_shutdown"}:
            priority = "later"
        self.queue.enqueue(event_type, event, target=str(payload.get("target", "")), source="runtime", priority=priority)
        for subscriber in list(self._subscribers):
            subscriber.put(event)
        return event

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue()
        self._subscribers.append(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        self._subscribers = [item for item in self._subscribers if item is not channel]
