from __future__ import annotations

from time import monotonic
from typing import Callable

HEARTBEAT_EVENT = "heartbeat"
DELTA_EVENTS = {"assistant_delta", "assistant_reasoning"}
FINAL_EVENTS = {"request_failed", "request_finished", "stream_completed"}
MIN_REFRESH_INTERVAL_SEC = 0.12
HEARTBEAT_REFRESH_INTERVAL_SEC = 1.0


class LiveRenderController:
    """Throttle rich live redraws so runtime events do not cause flicker."""

    def __init__(
        self,
        time_fn: Callable[[], float] = monotonic,
        min_refresh_interval_sec: float = MIN_REFRESH_INTERVAL_SEC,
        heartbeat_refresh_interval_sec: float = HEARTBEAT_REFRESH_INTERVAL_SEC,
    ):
        self._time_fn = time_fn
        self.min_refresh_interval_sec = min_refresh_interval_sec
        self.heartbeat_refresh_interval_sec = heartbeat_refresh_interval_sec
        self._last_render_at = 0.0
        self._pending = False

    def start(self) -> None:
        self._last_render_at = self._time_fn()
        self._pending = False

    def should_render(self, event_type: str) -> bool:
        now = self._time_fn()
        interval = self._interval_for(event_type)
        if event_type in FINAL_EVENTS:
            self._pending = False
            self._last_render_at = now
            return True
        if now - self._last_render_at >= interval:
            self._pending = False
            self._last_render_at = now
            return True
        self._pending = True
        return False

    def flush(self) -> bool:
        if not self._pending:
            return False
        self._pending = False
        self._last_render_at = self._time_fn()
        return True

    def _interval_for(self, event_type: str) -> float:
        if event_type == HEARTBEAT_EVENT:
            return self.heartbeat_refresh_interval_sec
        if event_type in DELTA_EVENTS:
            return self.min_refresh_interval_sec
        return self.min_refresh_interval_sec
