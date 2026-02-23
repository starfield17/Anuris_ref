import tempfile
import unittest
from pathlib import Path

from anuris.agent.tools import AgentToolExecutor, TodoManager, build_tool_schemas


class FakeBackgroundManager:
    def __init__(self):
        self.run_calls = []
        self.check_calls = []
        self.notifications = []

    def run(self, command, timeout=300):
        self.run_calls.append((command, timeout))
        return "Background task fake123 started"

    def check(self, task_id=None):
        self.check_calls.append(task_id)
        return f"check:{task_id or 'all'}"

    def drain_notifications(self):
        items = list(self.notifications)
        self.notifications.clear()
        return items


class FakeTeamManager:
    def __init__(self):
        self.runner = None
        self.calls = []

    def set_worker_runner(self, runner):
        self.runner = runner

    def spawn(self, name, role, prompt):
        self.calls.append(("spawn", name, role, prompt))
        return f"spawn:{name}:{role}"

    def list_members(self):
        self.calls.append(("list_members",))
        return "Team: default\n- alice (coder): idle"

    def send_from_lead(self, to, content, msg_type="message"):
        self.calls.append(("send", to, content, msg_type))
        return f"sent:{to}:{msg_type}"

    def read_inbox_text(self, name):
        self.calls.append(("read_inbox", name))
        return f"inbox:{name}"

    def broadcast_from_lead(self, content):
        self.calls.append(("broadcast", content))
        return "Broadcast to 1 teammate(s)"

    def request_shutdown(self, teammate):
        self.calls.append(("shutdown_request", teammate))
        return "Shutdown request req123 sent to alice"

    def check_shutdown(self, request_id):
        self.calls.append(("shutdown_status", request_id))
        return '{"status": "pending"}'

    def list_shutdown_requests(self):
        self.calls.append(("shutdown_list",))
        return "- req123: alice [pending]"

    def review_plan(self, request_id, approve, feedback=""):
        self.calls.append(("plan_review", request_id, approve, feedback))
        return "Plan req123 marked as approved"

    def list_plan_requests(self):
        self.calls.append(("plan_list",))
        return "- req123: from=alice [pending]"


class AgentToolsTests(unittest.TestCase):
    def test_todo_manager_update_and_render(self):
        manager = TodoManager()
        board = manager.update(
            [
                {"content": "Design", "status": "completed", "activeForm": "Designing"},
                {"content": "Implement", "status": "in_progress", "activeForm": "Implementing"},
                {"content": "Test", "status": "pending", "activeForm": "Testing"},
            ]
        )
        self.assertIn("[x] Design", board)
        self.assertIn("[>] Implement <- Implementing", board)
        self.assertIn("[ ] Test", board)

    def test_todo_manager_rejects_multiple_in_progress(self):
        manager = TodoManager()
        with self.assertRaises(ValueError):
            manager.update(
                [
                    {"content": "A", "status": "in_progress", "activeForm": "doing A"},
                    {"content": "B", "status": "in_progress", "activeForm": "doing B"},
                ]
            )

    def test_build_tool_schemas_respects_flags(self):
        schemas = build_tool_schemas(
            include_write_edit=False,
            include_todo=False,
            include_task=True,
            include_task_board=False,
            include_skill_loading=False,
            include_background_tasks=False,
        )
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(names, ["bash", "read_file", "task"])

    def test_build_tool_schemas_includes_persistent_task_tools(self):
        schemas = build_tool_schemas(
            include_write_edit=False,
            include_todo=False,
            include_task=False,
            include_task_board=True,
            include_skill_loading=False,
            include_background_tasks=False,
        )
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(
            names,
            ["bash", "read_file", "task_create", "task_get", "task_update", "task_list", "claim_task"],
        )

    def test_build_tool_schemas_includes_load_skill(self):
        schemas = build_tool_schemas(
            include_write_edit=False,
            include_todo=False,
            include_task=False,
            include_task_board=False,
            include_skill_loading=True,
            include_background_tasks=False,
        )
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(names, ["bash", "read_file", "load_skill"])

    def test_build_tool_schemas_includes_background_tools(self):
        schemas = build_tool_schemas(
            include_write_edit=False,
            include_todo=False,
            include_task=False,
            include_task_board=False,
            include_skill_loading=False,
            include_background_tasks=True,
        )
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(names, ["bash", "read_file", "background_run", "check_background"])

    def test_build_tool_schemas_includes_team_tools(self):
        schemas = build_tool_schemas(
            include_write_edit=False,
            include_todo=False,
            include_task=False,
            include_task_board=False,
            include_skill_loading=False,
            include_background_tasks=False,
            include_team_ops=True,
        )
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(
            names,
            [
                "bash",
                "read_file",
                "spawn_teammate",
                "list_teammates",
                "send_message",
                "read_inbox",
                "broadcast",
                "shutdown_request",
                "shutdown_status",
                "shutdown_list",
                "plan_review",
                "plan_list",
            ],
        )

    def test_task_tool_uses_subagent_runner(self):
        executor = AgentToolExecutor(
            include_task=True,
            subagent_runner=lambda prompt, agent_type: f"{agent_type}:{prompt}",
        )
        output = executor.execute("task", {"prompt": "investigate", "agent_type": "Explore"})
        self.assertEqual(output, "Explore:investigate")

    def test_persistent_task_board_create_update_and_list(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor = AgentToolExecutor(workspace_root=Path(tmp_dir), include_task_board=True)
            created = executor.execute(
                "task_create",
                {"subject": "Ship feature", "description": "Implement and verify"},
            )
            self.assertIn('"id": 1', created)
            self.assertIn('"status": "pending"', created)

            updated = executor.execute(
                "task_update",
                {"task_id": 1, "status": "in_progress", "owner": "lead"},
            )
            self.assertIn('"status": "in_progress"', updated)
            self.assertIn('"owner": "lead"', updated)

            listed = executor.execute("task_list", {})
            self.assertIn("[>] #1: Ship feature @lead", listed)

    def test_load_skill_returns_skill_body_from_workspace(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            skills_dir = workspace / ".anuris_skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            (skills_dir / "git.md").write_text(
                "---\n"
                "description: Git workflow helpers\n"
                "tags: git,workflow\n"
                "---\n"
                "Use small commits and clear messages.",
                encoding="utf-8",
            )

            executor = AgentToolExecutor(workspace_root=workspace, include_skill_loading=True)
            loaded = executor.execute("load_skill", {"name": "git"})
            self.assertIn('<skill name="git">', loaded)
            self.assertIn("Use small commits", loaded)
            self.assertIn("- git: Git workflow helpers", executor.get_skill_snapshot())

    def test_load_skill_resolves_path_like_aliases(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            skills_dir = workspace / ".anuris_skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            (skills_dir / "nb-source-switch.md").write_text(
                "---\n"
                "description: switch package mirrors\n"
                "tags: source-switch,pip,conda\n"
                "---\n"
                "Use non-interactive wrappers for mirror switching.",
                encoding="utf-8",
            )

            executor = AgentToolExecutor(workspace_root=workspace, include_skill_loading=True)
            loaded = executor.execute("load_skill", {"name": "bash/switch_source.md"})
            self.assertIn('<skill name="nb-source-switch">', loaded)
            self.assertIn("non-interactive wrappers", loaded)

            short_loaded = executor.execute("load_skill", {"name": "source_switch"})
            self.assertIn('<skill name="nb-source-switch">', short_loaded)

    def test_load_skill_unknown_name_includes_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            skills_dir = workspace / ".anuris_skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            (skills_dir / "nb-source-switch.md").write_text(
                "---\n"
                "description: switch package mirrors\n"
                "tags: source-switch,pip,conda\n"
                "---\n"
                "Use mirror helpers.",
                encoding="utf-8",
            )

            executor = AgentToolExecutor(workspace_root=workspace, include_skill_loading=True)
            error = executor.execute("load_skill", {"name": "sorce-swtch"})
            self.assertIn("Did you mean:", error)
            self.assertIn("nb-source-switch", error)

    def test_background_run_and_check_use_background_manager(self):
        fake_bg = FakeBackgroundManager()
        executor = AgentToolExecutor(
            include_background_tasks=True,
            background_manager=fake_bg,
        )
        started = executor.execute("background_run", {"command": "echo hi", "timeout": 5})
        self.assertIn("Background task", started)
        self.assertEqual(fake_bg.run_calls, [("echo hi", 5)])

        check = executor.execute("check_background", {"task_id": "fake123"})
        self.assertEqual(check, "check:fake123")
        self.assertEqual(executor.get_background_snapshot(), "check:all")

    def test_team_tools_use_team_manager(self):
        team = FakeTeamManager()
        executor = AgentToolExecutor(
            include_team_ops=True,
            include_task_board=False,
            include_skill_loading=False,
            include_background_tasks=False,
            team_manager=team,
        )

        self.assertEqual(
            executor.execute("spawn_teammate", {"name": "alice", "role": "coder", "prompt": "fix tests"}),
            "spawn:alice:coder",
        )
        self.assertIn("alice", executor.execute("list_teammates", {}))
        self.assertEqual(
            executor.execute("send_message", {"to": "alice", "content": "hello", "msg_type": "message"}),
            "sent:alice:message",
        )
        self.assertEqual(executor.execute("read_inbox", {}), "inbox:lead")
        self.assertIn("Broadcast", executor.execute("broadcast", {"content": "status update"}))
        self.assertIn("Shutdown request", executor.execute("shutdown_request", {"teammate": "alice"}))
        self.assertIn("pending", executor.execute("shutdown_status", {"request_id": "req123"}))
        self.assertIn("pending", executor.execute("shutdown_list", {}))
        self.assertIn(
            "approved",
            executor.execute(
                "plan_review",
                {"request_id": "req123", "approve": True, "feedback": "ok"},
            ),
        )
        self.assertIn("pending", executor.execute("plan_list", {}))
        self.assertIn("alice", executor.get_team_snapshot())
        self.assertEqual(executor.get_inbox_snapshot("lead"), "inbox:lead")
        self.assertIn("pending", executor.get_plan_snapshot())
        self.assertIn("pending", executor.get_shutdown_snapshot())


if __name__ == "__main__":
    unittest.main()
