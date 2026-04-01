from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class RuntimeWatcher:
    """Polls task and teammate state changes and turns them into events/notices."""

    def __init__(self, task_manager: Any, team_runtime_provider: Optional[Callable[[], Any]] = None):
        self.task_manager = task_manager
        self.team_runtime_provider = team_runtime_provider
        self._known_task_status: Dict[int, str] = {}
        self._known_team_status: Dict[str, str] = {}

    def set_team_runtime_provider(self, provider: Callable[[], Any]) -> None:
        self.team_runtime_provider = provider

    def poll(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for task in self.task_manager.list_records():
            task_id = int(task.get("id", 0))
            status = str(task.get("status", "") or "")
            previous = self._known_task_status.get(task_id)
            if previous and previous != status:
                payload = {"task": task}
                if status == "completed":
                    events.append(
                        {
                            "type": "task_completed",
                            "message": f"Task #{task_id} completed: {task.get('subject', '')}",
                            **payload,
                        }
                    )
                else:
                    events.append(
                        {
                            "type": "task_status_changed",
                            "message": f"Task #{task_id} changed {previous} -> {status}",
                            "previous_status": previous,
                            **payload,
                        }
                    )
            self._known_task_status[task_id] = status

        team_runtime = self.team_runtime_provider() if self.team_runtime_provider else None
        if team_runtime and hasattr(team_runtime, "roster"):
            for member in team_runtime.roster():
                name = str(member.get("name", "") or "").strip()
                if not name:
                    continue
                status = str(member.get("status", "") or "")
                previous = self._known_team_status.get(name)
                if previous and previous != status:
                    event_type = {
                        "idle": "teammate_idle",
                        "shutdown": "teammate_shutdown",
                    }.get(status, "teammate_status_changed")
                    events.append(
                        {
                            "type": event_type,
                            "teammate": name,
                            "status": status,
                            "previous_status": previous,
                            "message": f"Teammate {name} changed {previous} -> {status}",
                        }
                    )
                self._known_team_status[name] = status
        return events
