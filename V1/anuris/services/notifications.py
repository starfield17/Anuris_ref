from __future__ import annotations

from typing import Any, Dict, List


class NotificationCenter:
    """Small queue for runtime notices injected into the next model turn."""

    def __init__(self):
        self._queue: List[Dict[str, Any]] = []

    def enqueue(self, message: str, *, kind: str = "runtime", metadata: Dict[str, Any] | None = None) -> None:
        content = str(message or "").strip()
        if not content:
            return
        self._queue.append({"message": content, "kind": kind, "metadata": dict(metadata or {})})

    def drain(self) -> List[Dict[str, Any]]:
        items = list(self._queue)
        self._queue.clear()
        return items

    def preview(self) -> str:
        if not self._queue:
            return "No queued runtime notices."
        return "\n".join(f"- [{item['kind']}] {item['message']}" for item in self._queue)

    def count(self) -> int:
        return len(self._queue)
