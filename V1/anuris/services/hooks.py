from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List


class HookManager:
    """Simple local hook registry for tool and session events."""

    SUPPORTED_EVENTS = (
        "request_started",
        "user_input_received",
        "user_message",
        "assistant_reasoning",
        "assistant_message",
        "before_model_call",
        "tool_called",
        "tool_result",
        "request_finished",
        "request_failed",
        "compact_boundary",
        "skill_prefetch",
        "runtime_notice_injected",
        "task_completed",
        "task_status_changed",
        "teammate_idle",
        "teammate_shutdown",
        "teammate_status_changed",
    )

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.hooks_path = self.workspace_root / ".anuris" / "hooks.json"
        self.hooks_path.parent.mkdir(parents=True, exist_ok=True)
        self.hooks: List[Dict[str, str]] = []
        self.reload()

    def reload(self) -> None:
        if self.hooks_path.exists():
            try:
                payload = json.loads(self.hooks_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {"hooks": []}
        else:
            payload = {"hooks": []}
        self.hooks = [dict(item) for item in payload.get("hooks", []) if isinstance(item, dict)]

    def save(self) -> None:
        self.hooks_path.write_text(json.dumps({"hooks": self.hooks}, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, event: str, command: str) -> Dict[str, str]:
        entry = {"event": event.strip(), "command": command.strip()}
        if not entry["event"] or not entry["command"]:
            raise ValueError("event and command are required")
        self.hooks.append(entry)
        self.save()
        return entry

    def remove(self, index: int) -> Dict[str, str]:
        if index < 0 or index >= len(self.hooks):
            raise ValueError("Invalid hook index")
        entry = self.hooks.pop(index)
        self.save()
        return entry

    def run(self, event: str, payload: Dict[str, Any]) -> List[Dict[str, str]]:
        results: List[Dict[str, str]] = []
        for entry in self.hooks:
            if entry.get("event") != event:
                continue
            completed = subprocess.run(
                entry["command"],
                cwd=self.workspace_root,
                shell=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            results.append(
                {
                    "event": event,
                    "command": entry["command"],
                    "returncode": str(completed.returncode),
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                    "payload": json.dumps(payload, ensure_ascii=False)[:1000],
                }
            )
        return results

    def render(self) -> str:
        if not self.hooks:
            return "No hooks configured.\nSupported events: " + ", ".join(self.SUPPORTED_EVENTS)
        return "\n".join(
            [f"[{index}] {item['event']} -> {item['command']}" for index, item in enumerate(self.hooks)]
            + ["", "Supported events: " + ", ".join(self.SUPPORTED_EVENTS)]
        )
