from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class DiagnosticRecord:
    id: str
    severity: str
    category: str
    summary: str
    detail: str = ""
    suggested_action: str = ""

    def render(self) -> str:
        extra = f" ({self.detail})" if self.detail else ""
        return f"[{self.severity}/{self.category}] {self.summary}{extra}"


class DiagnosticsService:
    """Collects continuous runtime diagnostics and structured warnings."""

    def __init__(self, session: Any):
        self.session = session

    def records(self) -> List[DiagnosticRecord]:
        session = self.session
        records: List[DiagnosticRecord] = []
        budget_service = getattr(session.services, "context_budget", None)
        budget = budget_service.analyze() if budget_service is not None else None
        notice_summary = session.services.notification_center.summary_counts() if session.services.notification_center else {}

        if budget is not None and budget.approx_chars >= budget.soft_limit:
            records.append(
                DiagnosticRecord(
                    id="context-pressure-high",
                    severity="warning",
                    category="context",
                    summary=f"context pressure high: {budget.approx_chars} chars",
                    detail=budget.compact_reason or f"soft limit {budget.soft_limit}",
                    suggested_action="compact conversation or trim large tool outputs",
                )
            )
        if budget is not None and budget.should_compact and not any(message.kind == "compact_boundary" for message in session.session_store.messages):
            records.append(
                DiagnosticRecord(
                    id="compact-recommended",
                    severity="info",
                    category="context",
                    summary="context compaction recommended",
                    detail=budget.compact_reason or budget.compact_focus or "budget threshold exceeded",
                    suggested_action="run /compact or let auto-compaction handle the next turn",
                )
            )
        if notice_summary.get("queued", 0) >= 6:
            records.append(
                DiagnosticRecord(
                    id="queue-backlog",
                    severity="warning",
                    category="events",
                    summary=f"queued notices backlog: {notice_summary['queued']}",
                    detail=f"highest priority {notice_summary.get('highest_priority', 0)}",
                    suggested_action="inspect /notices recent and clear or collapse noisy sources",
                )
            )

        for resource in session.services.mcp_manager.list_resources():
            path = Path(str(resource.get("path", "")))
            if not path.exists():
                records.append(
                    DiagnosticRecord(
                        id=f"mcp-missing-{resource.get('name')}",
                        severity="error",
                        category="mcp",
                        summary=f"mcp resource missing: {resource.get('name')}",
                        detail=str(path),
                        suggested_action="fix or remove the MCP resource entry",
                    )
                )

        for plugin in session.services.plugin_manager.plugins:
            if not plugin.get("name"):
                records.append(
                    DiagnosticRecord(
                        id=f"plugin-missing-name-{plugin.get('path')}",
                        severity="warning",
                        category="plugins",
                        summary="plugin missing name metadata",
                        detail=str(plugin.get("path")),
                        suggested_action="add a name in plugin metadata",
                    )
                )

        roster = {item.get("name") for item in session.team_runtime.roster()} if hasattr(session, "team_runtime") else set()
        for task in session.services.task_manager.list_records():
            owner = str(task.get("owner", "") or "").strip()
            if owner and owner not in roster and owner != "lead":
                records.append(
                    DiagnosticRecord(
                        id=f"task-owner-{task.get('id')}",
                        severity="warning",
                        category="tasks",
                        summary=f"task #{task.get('id')} owner missing from roster: {owner}",
                        detail=str(task.get("subject", "")),
                        suggested_action="reassign the task or respawn the teammate",
                    )
                )

        governance = session.team_runtime.governance_snapshot() if hasattr(session, "team_runtime") else {}
        if governance.get("pending_plans"):
            records.append(
                DiagnosticRecord(
                    id="governance-pending-plans",
                    severity="info",
                    category="team",
                    summary=f"{len(governance['pending_plans'])} plan approval(s) pending",
                    suggested_action="review pending plans in /agents plans or /tasks board",
                )
            )
        if governance.get("pending_shutdowns"):
            records.append(
                DiagnosticRecord(
                    id="governance-pending-shutdowns",
                    severity="info",
                    category="team",
                    summary=f"{len(governance['pending_shutdowns'])} shutdown request(s) pending",
                    suggested_action="review shutdown requests in /agents shutdown list",
                )
            )

        for hook in session.services.hook_manager.hooks:
            command = str(hook.get("command", "") or "").strip()
            if not command:
                records.append(
                    DiagnosticRecord(
                        id=f"hook-missing-{hook.get('event')}",
                        severity="warning",
                        category="hooks",
                        summary=f"hook missing command for event {hook.get('event')}",
                        suggested_action="update the hook entry to include a command",
                    )
                )

        runtime = session.services.settings_manager.runtime
        if session.services.permission_manager.mode == "plan" and runtime.sandbox_mode == "off":
            records.append(
                DiagnosticRecord(
                    id="policy-plan-off-sandbox",
                    severity="warning",
                    category="policy",
                    summary="plan mode is active while sandbox is off",
                    suggested_action="prefer read-only or workspace-write sandbox in plan mode",
                )
            )
        return records

    def warnings(self) -> List[str]:
        return [record.summary for record in self.records() if record.severity in {"warning", "error"}]

    def snapshot(self) -> Dict[str, Any]:
        session = self.session
        records = self.records()
        severities: Dict[str, int] = {}
        for record in records:
            severities[record.severity] = severities.get(record.severity, 0) + 1
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
            "warnings": [record.summary for record in records if record.severity in {"warning", "error"}],
            "records": [record.__dict__ for record in records],
            "severity_counts": severities,
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
            f"- records: {len(snapshot['records'])}",
            f"- severity_counts: {snapshot['severity_counts']}",
        ]
        if snapshot["records"]:
            lines.extend(["", "Records:"])
            for record in self.records():
                lines.append(f"- {record.render()}")
        return "\n".join(lines)
