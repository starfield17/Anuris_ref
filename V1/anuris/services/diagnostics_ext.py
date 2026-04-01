from __future__ import annotations

from pathlib import Path
from typing import Any, List


class DiagnosticsService:
    """Collects continuous runtime diagnostics and warnings."""

    def __init__(self, session: Any):
        self.session = session

    def warnings(self) -> List[str]:
        session = self.session
        warnings: List[str] = []

        approx_chars = session.session_store.approximate_size()
        if approx_chars >= 18000:
            warnings.append(f"context pressure high: {approx_chars} chars")

        for resource in session.services.mcp_manager.list_resources():
            path = Path(str(resource.get("path", "")))
            if not path.exists():
                warnings.append(f"mcp resource missing: {resource.get('name')} -> {path}")

        for plugin in session.services.plugin_manager.plugins:
            if not plugin.get("name"):
                warnings.append(f"plugin missing name: {plugin.get('path')}")

        roster = {item.get("name") for item in session.team_runtime.roster()} if hasattr(session, "team_runtime") else set()
        for task in session.services.task_manager.list_records():
            owner = str(task.get("owner", "") or "").strip()
            if owner and owner not in roster and owner != "lead":
                warnings.append(f"task #{task.get('id')} owner missing from roster: {owner}")

        for hook in session.services.hook_manager.hooks:
            command = str(hook.get("command", "") or "").strip()
            if not command:
                warnings.append(f"hook missing command for event {hook.get('event')}")

        return warnings

    def snapshot(self) -> dict[str, Any]:
        session = self.session
        warning_list = self.warnings()
        return {
            "context_chars": session.session_store.approximate_size(),
            "permission_mode": session.services.permission_manager.mode,
            "sandbox_mode": session.services.settings_manager.runtime.sandbox_mode,
            "fast_mode": session.services.settings_manager.runtime.fast_mode,
            "effort_level": session.services.settings_manager.runtime.effort_level,
            "queued_notices": session.services.notification_center.count() if session.services.notification_center else 0,
            "background_tasks": len(getattr(session.services.runtime_watcher, "_known_task_status", {})),
            "hooks": len(session.services.hook_manager.hooks),
            "plugins": len(session.services.plugin_manager.plugins),
            "mcp_resources": len(session.services.mcp_manager.list_resources()),
            "warnings": warning_list,
        }

    def render(self) -> str:
        snapshot = self.snapshot()
        lines = [
            "Diagnostics:",
            f"- context_chars: {snapshot['context_chars']}",
            f"- permission_mode: {snapshot['permission_mode']}",
            f"- sandbox_mode: {snapshot['sandbox_mode']}",
            f"- fast_mode: {snapshot['fast_mode']}",
            f"- effort_level: {snapshot['effort_level']}",
            f"- queued_notices: {snapshot['queued_notices']}",
            f"- background_tasks: {snapshot['background_tasks']}",
            f"- hooks: {snapshot['hooks']}",
            f"- plugins: {snapshot['plugins']}",
            f"- mcp_resources: {snapshot['mcp_resources']}",
            f"- warnings: {len(snapshot['warnings'])}",
        ]
        if snapshot["warnings"]:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {item}" for item in snapshot["warnings"])
        return "\n".join(lines)
