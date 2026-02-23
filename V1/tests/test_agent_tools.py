import tempfile
import unittest
from pathlib import Path

from anuris.agent.tools import AgentToolExecutor, TodoManager, build_tool_schemas


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
        )
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(names, ["bash", "read_file", "task"])

    def test_build_tool_schemas_includes_persistent_task_tools(self):
        schemas = build_tool_schemas(
            include_write_edit=False,
            include_todo=False,
            include_task=False,
            include_task_board=True,
        )
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(
            names,
            ["bash", "read_file", "task_create", "task_get", "task_update", "task_list"],
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


if __name__ == "__main__":
    unittest.main()
