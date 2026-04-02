from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .events import utc_timestamp


PRIORITY_RANK = {"now": 0, "next": 1, "later": 2}


@dataclass
class QueuedEvent:
    id: str
    event_type: str
    payload: Dict[str, Any]
    target: str = ""
    source: str = "system"
    priority: str = "next"
    status: str = "queued"
    created_at: str = field(default_factory=utc_timestamp)


class RuntimeEventQueue:
    """Durable command/event queue for runtime-side scheduling and recovery."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._snapshot_path = self.root / "queue.json"
        self._log_path = self.root / "operations.jsonl"
        self._items: List[QueuedEvent] = self._load_snapshot()

    def enqueue(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        target: str = "",
        source: str = "system",
        priority: str = "next",
    ) -> QueuedEvent:
        item = QueuedEvent(
            id=uuid4().hex[:12],
            event_type=event_type,
            payload=dict(payload),
            target=target.strip(),
            source=source.strip() or "system",
            priority=priority if priority in PRIORITY_RANK else "next",
        )
        self._items.append(item)
        self._persist("enqueue", item)
        return item

    def peek(self, target: str = "") -> Optional[QueuedEvent]:
        items = self._select(target)
        return items[0] if items else None

    def drain(self, target: str = "") -> List[QueuedEvent]:
        items = self._select(target)
        if not items:
            return []
        drained_ids = {item.id for item in items}
        self._items = [item for item in self._items if item.id not in drained_ids]
        for item in items:
            item.status = "drained"
            self._append_log("drain", item)
        self._write_snapshot()
        return items

    def list(self) -> List[QueuedEvent]:
        return list(self._items)

    def _select(self, target: str) -> List[QueuedEvent]:
        filtered = [item for item in self._items if not target or item.target in {"", target}]
        return sorted(filtered, key=lambda item: (PRIORITY_RANK.get(item.priority, 1), item.created_at))

    def _persist(self, operation: str, item: QueuedEvent) -> None:
        self._append_log(operation, item)
        self._write_snapshot()

    def _append_log(self, operation: str, item: QueuedEvent) -> None:
        payload = {"operation": operation, "timestamp": utc_timestamp(), "item": asdict(item)}
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_snapshot(self) -> None:
        payload = [asdict(item) for item in self._items]
        self._snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_snapshot(self) -> List[QueuedEvent]:
        if not self._snapshot_path.exists():
            return []
        raw = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
        return [QueuedEvent(**item) for item in raw if isinstance(item, dict)]
