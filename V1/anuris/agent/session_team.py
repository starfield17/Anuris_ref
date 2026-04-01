from __future__ import annotations

from pathlib import Path
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
            teammate_poll_interval_sec=1,
            teammate_idle_timeout_sec=30,
            teammate_max_runtime_sec=300,
        )

    @property
    def team_manager(self):
        return self.executor.team_manager

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
