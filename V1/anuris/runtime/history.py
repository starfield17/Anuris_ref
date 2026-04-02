from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class HistoryPage:
    events: List[Dict[str, Any]]
    first_id: Optional[str]
    has_more: bool


class EventHistory:
    """Append-only event history with cursor pagination."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: Dict[str, Any]) -> Dict[str, Any]:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def load_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        items: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if raw:
                    items.append(json.loads(raw))
        return items

    def latest(self, limit: int = 100) -> HistoryPage:
        events = self.load_all()
        page = events[-limit:]
        return HistoryPage(events=page, first_id=_first_id(page), has_more=len(events) > len(page))

    def older(self, before_id: str, limit: int = 100) -> HistoryPage:
        events = self.load_all()
        index = _find_event_index(events, before_id)
        if index <= 0:
            return HistoryPage(events=[], first_id=None, has_more=False)
        start = max(0, index - limit)
        page = events[start:index]
        return HistoryPage(events=page, first_id=_first_id(page), has_more=start > 0)

    def replace(self, events: Iterable[Dict[str, Any]]) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _first_id(events: List[Dict[str, Any]]) -> Optional[str]:
    if not events:
        return None
    return str(events[0].get("event_id") or "")


def _find_event_index(events: List[Dict[str, Any]], event_id: str) -> int:
    for index, event in enumerate(events):
        if str(event.get("event_id") or "") == str(event_id):
            return index
    return -1
