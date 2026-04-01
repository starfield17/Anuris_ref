from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Optional

from .executor import AgentToolExecutor
from .loop import AgentLoopRunner
from .tasks import PersistentTaskManager


class SessionTeamRuntime:
    """Session-scoped controller for teammate, inbox, and governance flows."""

    def __init__(
        self,
        model: Any,
        workspace_root: Path,
        task_manager: Optional[PersistentTaskManager] = None,
    ):
        self.model = model
        self.workspace_root = Path(workspace_root).resolve()
        self.executor = AgentToolExecutor(
            workspace_root=self.workspace_root,
            include_write_edit=True,
            include_todo=False,
            include_task=False,
            include_task_board=True,
            include_skill_loading=False,
            include_background_tasks=False,
            include_team_ops=True,
            task_manager=task_manager,
        )
        self.runner = AgentLoopRunner(
            model=self.model,
            tool_executor=self.executor,
            include_todo=False,
            include_task=False,
            include_write_edit=True,
            include_task_board=True,
            include_skill_loading=False,
            include_background_tasks=False,
            include_team_ops=True,
            include_compaction=False,
            hot_swap_tools=False,
            teammate_poll_interval_sec=0.2,
            teammate_idle_timeout_sec=30,
            teammate_max_runtime_sec=300,
        )

    @property
    def team_manager(self):
        return self.executor.team_manager

    @property
    def task_manager(self):
        return self.executor.task_manager

    @property
    def team_dir(self) -> Path:
        return self.team_manager.team_dir

    @property
    def inbox_dir(self) -> Path:
        return self.team_manager.bus.inbox_dir

    def describe(self) -> str:
        team_name = self.team_manager._config.get("team_name", "default")
        lines = [
            f"Team runtime: {team_name}",
            f"Workspace: {self.workspace_root}",
            f"Team dir: {self.team_dir}",
            f"Inbox dir: {self.inbox_dir}",
            "",
            self.runner.get_team_snapshot(),
            "",
            "Plans:",
            self.runner.get_plan_snapshot(),
            "",
            "Shutdowns:",
            self.runner.get_shutdown_snapshot(),
        ]
        return "\n".join(lines)

    def roster(self) -> list[dict[str, str]]:
        return [dict(item) for item in self.team_manager._config.get("members", []) if isinstance(item, dict)]

    def summary_counts(self) -> dict[str, int]:
        roster = self.roster()
        counts = {
            "members": len(roster),
            "working": 0,
            "idle": 0,
            "shutdown": 0,
            "error": 0,
            "lead_inbox": self._count_inbox("lead"),
            "plans_pending": 0,
            "shutdowns_pending": 0,
        }
        for member in roster:
            status = str(member.get("status", "") or "")
            if status in counts:
                counts[status] += 1
        for request in getattr(self.team_manager, "_plan_requests", {}).values():
            if str(request.get("status", "")) == "pending":
                counts["plans_pending"] += 1
        for request in getattr(self.team_manager, "_shutdown_requests", {}).values():
            if str(request.get("status", "")) == "pending":
                counts["shutdowns_pending"] += 1
        return counts

    def render_dashboard(self) -> str:
        counts = self.summary_counts()
        task_summary = self.task_manager.render_summary() if self.task_manager else "tasks_total: 0"
        lines = [
            "Team dashboard:",
            f"- members: {counts['members']} (working={counts['working']}, idle={counts['idle']}, shutdown={counts['shutdown']}, error={counts['error']})",
            f"- lead_inbox: {counts['lead_inbox']}",
            f"- plans_pending: {counts['plans_pending']}",
            f"- shutdowns_pending: {counts['shutdowns_pending']}",
            "",
            "Roster:",
        ]
        if not self.roster():
            lines.append("- (no teammates)")
        else:
            for member in self.roster():
                lines.append(
                    f"- {member.get('name', '?')}: role={member.get('role', 'teammate')} status={member.get('status', 'unknown')}"
                )
        lines.extend(["", "Tasks:", task_summary])
        return "\n".join(lines)

    def render_processes(self) -> str:
        roster = self.roster()
        owner_counts = self.task_manager.summary_counts().get("owners", {}) if self.task_manager else {}
        lines = ["Teammate processes:"]
        if not roster:
            lines.append("- (no teammates)")
        else:
            for member in roster:
                name = str(member.get("name", "?"))
                lines.append(
                    f"- {name}: status={member.get('status', 'unknown')} role={member.get('role', 'teammate')} tasks={owner_counts.get(name, 0)} inbox={self._count_inbox(name)}"
                )
        lines.append(
            f"- lead: inbox={self._count_inbox('lead')} tasks={owner_counts.get('lead', 0)} plans={self.summary_counts()['plans_pending']}"
        )
        return "\n".join(lines)

    def governance_snapshot(self) -> dict[str, Any]:
        plan_requests = getattr(self.team_manager, "_plan_requests", {})
        shutdown_requests = getattr(self.team_manager, "_shutdown_requests", {})
        pending_plans = [
            {"request_id": request_id, **payload}
            for request_id, payload in sorted(plan_requests.items())
            if str(payload.get("status", "")) == "pending"
        ]
        pending_shutdowns = [
            {"request_id": request_id, **payload}
            for request_id, payload in sorted(shutdown_requests.items())
            if str(payload.get("status", "")) == "pending"
        ]
        return {
            "lead_inbox": self._count_inbox("lead"),
            "pending_plans": pending_plans,
            "pending_shutdowns": pending_shutdowns,
        }

    def render_governance(self) -> str:
        snapshot = self.governance_snapshot()
        lines = ["Governance:"]
        lines.append(f"- lead_inbox: {snapshot['lead_inbox']}")
        lines.append(f"- plans_pending: {len(snapshot['pending_plans'])}")
        if snapshot["pending_plans"]:
            for item in snapshot["pending_plans"][:5]:
                lines.append(
                    f"  - plan {item['request_id']} from={item.get('from', '?')} status={item.get('status', 'pending')}"
                )
        lines.append(f"- shutdowns_pending: {len(snapshot['pending_shutdowns'])}")
        if snapshot["pending_shutdowns"]:
            for item in snapshot["pending_shutdowns"][:5]:
                lines.append(
                    f"  - shutdown {item['request_id']} target={item.get('target', '?')} status={item.get('status', 'pending')}"
                )
        return "\n".join(lines)

    def claim_next(self, owner: str = "lead") -> str:
        if not self.task_manager:
            return "Task manager unavailable"
        task = self.task_manager.claim_next_unblocked(owner)
        if not task:
            return f"No unblocked tasks available for {owner}"
        return json.dumps(task, ensure_ascii=False, indent=2)

    def spawn(self, name: str, role: str, prompt: str) -> str:
        return self.executor.run_spawn_teammate(name, role, prompt)

    def list_members(self) -> str:
        return self.runner.get_team_snapshot()

    def read_inbox(self, name: str = "lead") -> str:
        return self.runner.get_inbox_snapshot(name)

    def send_message(self, to: str, content: str, msg_type: str = "message") -> str:
        return self.executor.run_send_message(to, content, msg_type)

    def broadcast(self, content: str) -> str:
        return self.executor.run_broadcast(content)

    def request_shutdown(self, teammate: str) -> str:
        return self.executor.run_shutdown_request(teammate)

    def shutdown_status(self, request_id: str) -> str:
        return self.executor.run_shutdown_status(request_id)

    def list_shutdown_requests(self) -> str:
        return self.executor.run_shutdown_list()

    def list_plan_requests(self) -> str:
        return self.executor.run_plan_list()

    def review_plan(self, request_id: str, approve: bool, feedback: str = "") -> str:
        return self.executor.run_plan_review(request_id, approve, feedback)

    def _count_inbox(self, name: str) -> int:
        inbox_path = self.inbox_dir / f"{name}.jsonl"
        if not inbox_path.exists():
            return 0
        return len([line for line in inbox_path.read_text(encoding="utf-8").splitlines() if line.strip()])
