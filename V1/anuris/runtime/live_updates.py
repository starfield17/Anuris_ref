from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict


def project_progress_event(event: Dict[str, Any]) -> Dict[str, Any] | None:
    event_type = str(event.get("type", "") or "")
    builders = {
        "request_started": _request_started,
        "request_finished": _request_finished,
        "request_failed": _request_failed,
        "agent_round_started": _round_started,
        "tool_called": _tool_called,
        "tool_result": _tool_result,
        "compact_boundary": _compact_boundary,
        "turn_budget_extended": _turn_budget_extended,
        "task_status_changed": _task_status_changed,
        "task_completed": _task_completed,
    }
    builder = builders.get(event_type)
    if builder is None:
        return None
    payload = builder(event)
    return payload if payload else None


def build_notice_event_payload(
    notice: Any,
    *,
    source_event: str,
    request_id: str = "",
) -> Dict[str, Any]:
    payload = notice.to_dict()
    payload["source_event"] = source_event
    if request_id:
        payload["request_id"] = request_id
    return payload


@dataclass
class LiveStreamState:
    watcher_interval_sec: float = 0.2
    heartbeat_interval_sec: float = 1.0
    active_request_id: str = ""
    last_event_type: str = ""
    last_activity_at: str = ""
    next_watcher_poll_at: float = 0.0
    next_heartbeat_at: float = 0.0

    def mark_started(self, *, request_id: str) -> None:
        now = datetime.now(timezone.utc).timestamp()
        self.active_request_id = request_id
        self.last_activity_at = _utc_now()
        self.next_heartbeat_at = now + self.heartbeat_interval_sec

    def observe(self, event: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).timestamp()
        self.last_event_type = str(event.get("type", "") or "")
        self.last_activity_at = str(event.get("timestamp", "") or _utc_now())
        self.next_heartbeat_at = now + self.heartbeat_interval_sec

    def due_watcher_poll(self, now: float) -> bool:
        if now < self.next_watcher_poll_at:
            return False
        self.next_watcher_poll_at = now + self.watcher_interval_sec
        return True

    def due_heartbeat(self, now: float) -> bool:
        if not self.active_request_id or now < self.next_heartbeat_at:
            return False
        self.next_heartbeat_at = now + self.heartbeat_interval_sec
        return True


def _request_started(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stage": "started",
        "status": "running",
        "request_id": str(event.get("request_id", "") or ""),
        "summary": "Request started",
    }


def _request_finished(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stage": "finished",
        "status": "completed",
        "request_id": str(event.get("request_id", "") or ""),
        "summary": "Request finished",
        "round_count": int(event.get("round_count", 0) or 0),
    }


def _request_failed(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stage": "failed",
        "status": "failed",
        "request_id": str(event.get("request_id", "") or ""),
        "summary": str(event.get("error", "") or "Request failed"),
    }


def _round_started(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stage": "reasoning",
        "status": "running",
        "round": int(event.get("round", 0) or 0),
        "summary": f"Starting round {int(event.get('round', 0) or 0)}",
    }


def _tool_called(event: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = str(event.get("tool_name", "") or "")
    return {
        "stage": "tool_running",
        "status": "running",
        "tool_name": tool_name,
        "round": int(event.get("round", 0) or 0),
        "summary": f"Running {tool_name}",
    }


def _tool_result(event: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = str(event.get("tool_name", "") or "")
    is_error = bool(event.get("is_error"))
    return {
        "stage": "tool_finished",
        "status": "failed" if is_error else "completed",
        "tool_name": tool_name,
        "round": int(event.get("round", 0) or 0),
        "summary": f"{tool_name} {'failed' if is_error else 'completed'}",
        "is_error": is_error,
    }


def _compact_boundary(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stage": "compacted",
        "status": "running",
        "summary": str(event.get("compact_reason", "") or "Context compacted"),
        "focus": str(event.get("focus", "") or ""),
    }


def _turn_budget_extended(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stage": "budget_extended",
        "status": "running",
        "summary": f"Turn budget extended to {int(event.get('new_limit', 0) or 0)}",
        "new_limit": int(event.get("new_limit", 0) or 0),
    }


def _task_status_changed(event: Dict[str, Any]) -> Dict[str, Any]:
    task = event.get("task", {}) or {}
    return {
        "stage": "task_status_changed",
        "status": str(task.get("status", "") or ""),
        "summary": str(event.get("message", "") or "Task status changed"),
        "task": task,
    }


def _task_completed(event: Dict[str, Any]) -> Dict[str, Any]:
    task = event.get("task", {}) or {}
    return {
        "stage": "task_completed",
        "status": "completed",
        "summary": str(event.get("message", "") or "Task completed"),
        "task": task,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
