import unittest

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
        schemas = build_tool_schemas(include_write_edit=False, include_todo=False, include_task=True)
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(names, ["bash", "read_file", "task"])

    def test_task_tool_uses_subagent_runner(self):
        executor = AgentToolExecutor(
            include_task=True,
            subagent_runner=lambda prompt, agent_type: f"{agent_type}:{prompt}",
        )
        output = executor.execute("task", {"prompt": "investigate", "agent_type": "Explore"})
        self.assertEqual(output, "Explore:investigate")


if __name__ == "__main__":
    unittest.main()
