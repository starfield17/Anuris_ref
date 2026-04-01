import json
import tempfile
import unittest
from pathlib import Path

from anuris.config import Config
from anuris.session import ChatSession


class FakeModel:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def create_completion(self, messages, stream, tools=None, tool_choice=None):
        self.calls.append(
            {
                "messages": messages,
                "stream": stream,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return {"choices": [{"message": {"content": "unused"}}]}


class CommandDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "note.txt").write_text("hello", encoding="utf-8")
        self.session = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="cmdtest",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_attach_and_files_commands(self):
        response = self.session.handle_input(f"/attach {self.workspace / 'note.txt'}")
        self.assertIn("Added: note.txt", response.output_text)
        self.assertEqual(len(self.session.attachment_manager.attachments), 1)

        response = self.session.handle_input("/files")
        self.assertIn("note.txt", response.output_text)

    def test_save_and_load_commands_round_trip(self):
        self.session.session_store.add_user_message("hello")
        self.session.session_store.add_assistant_message("world")

        save_response = self.session.handle_input("/save snapshot.json")
        self.assertIn("snapshot.json", save_response.output_text)

        self.session.handle_input("/clear")
        self.assertEqual(len(self.session.session_store.messages), 1)

        load_response = self.session.handle_input("/load snapshot.json")
        self.assertIn("snapshot.json", load_response.output_text)
        self.assertEqual(len(self.session.session_store.messages), 3)

    def test_model_and_agent_commands_update_runtime(self):
        response = self.session.handle_input("/model alt-model")
        self.assertIn("alt-model", response.output_text)
        self.assertEqual(self.session.config.model, "alt-model")

        response = self.session.handle_input("/agent off")
        self.assertIn("disabled", response.output_text)
        self.assertFalse(self.session.agent_mode)

    def test_permissions_and_rewind_commands(self):
        self.session.session_store.add_user_message("first")
        self.session.session_store.add_assistant_message("reply")
        self.session.session_store.add_user_message("second")
        self.session.session_store.add_assistant_message("reply2")

        response = self.session.handle_input("/permissions readonly")
        self.assertIn("readonly", response.output_text)
        self.assertEqual(self.session.services.permission_manager.mode, "readonly")

        response = self.session.handle_input("/rewind 1")
        self.assertIn("Rewound", response.output_text)
        self.assertEqual([message.content for message in self.session.session_store.messages if message.role == "user"], ["first"])

    def test_resume_command_loads_saved_session_snapshot(self):
        other = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(),
            workspace_root=self.workspace,
            session_id="saved123",
        )
        other.session_store.add_user_message("restored user")
        other.session_store.add_assistant_message("restored assistant")

        self.session.handle_input("/clear")
        response = self.session.handle_input("/resume saved123")
        self.assertIn("saved123", response.output_text)
        self.assertTrue(any(message.content == "restored assistant" for message in self.session.session_store.messages))

    def test_mcp_plugin_and_worktree_commands(self):
        docs_path = self.workspace / "docs.txt"
        docs_path.write_text("mcp body", encoding="utf-8")
        plugin_dir = self.workspace / ".anuris" / "plugins" / "demo-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "version": "1.0.0", "description": "demo"}),
            encoding="utf-8",
        )

        response = self.session.handle_input(f"/mcp add-resource docs {docs_path}")
        self.assertIn("Added MCP resource docs", response.output_text)

        response = self.session.handle_input("/mcp list")
        self.assertIn("docs", response.output_text)

        response = self.session.handle_input("/plugin reload")
        self.assertIn("reloaded", response.output_text.lower())

        response = self.session.handle_input("/plugin list")
        self.assertIn("demo-plugin", response.output_text)

        alt_workspace = self.workspace / "alt"
        alt_workspace.mkdir()
        response = self.session.handle_input(f"/worktree enter {alt_workspace}")
        self.assertIn(str(alt_workspace), response.output_text)
        self.assertEqual(self.session.workspace_root, alt_workspace.resolve())

        response = self.session.handle_input("/worktree exit")
        self.assertIn(str(self.workspace), response.output_text)
        self.assertEqual(self.session.workspace_root, self.workspace.resolve())

    def test_files_and_cost_commands_show_context(self):
        self.session.handle_input("Read note", attachment_paths=[str(self.workspace / "note.txt")])
        response = self.session.handle_input("/files")
        self.assertIn("note.txt", response.output_text)

        response = self.session.handle_input("/cost")
        self.assertIn("queries:", response.output_text)

    def test_hooks_command_adds_and_runs_local_hook(self):
        response = self.session.handle_input('/hooks add tool_called "printf hook-fired"')
        self.assertIn("Added hook", response.output_text)

        response = self.session.handle_input("/hooks run tool_called")
        self.assertIn("hook-fired", response.output_text)

    def test_review_and_plan_commands_use_prompt_execution(self):
        review_session = ChatSession(
            Config(api_key="k", model="fake-model", base_url="https://example.com/v1"),
            model=FakeModel(
                [
                    {"choices": [{"message": {"content": "Review findings go here."}}]},
                    {"choices": [{"message": {"content": "Implementation plan drafted."}}]},
                ]
            ),
            workspace_root=self.workspace,
            session_id="cmdreview",
        )

        review_response = review_session.handle_input("/review")
        self.assertIn("Review findings go here.", review_response.output_text)

        plan_response = review_session.handle_input("/plan add MCP support")
        self.assertIn("Implementation plan drafted.", plan_response.output_text)

    def test_summary_context_and_memory_commands(self):
        self.session.session_store.add_user_message("context user")
        self.session.session_store.add_assistant_message("context assistant")
        self.session.services.context_files.record(self.workspace / "note.txt")

        response = self.session.handle_input("/summary")
        self.assertIn("Session summary", response.output_text)
        self.assertIn("context assistant", response.output_text)

        response = self.session.handle_input("/context")
        self.assertIn("messages_total:", response.output_text)
        self.assertIn("Files In Context", response.output_text)
        self.assertIn("note.txt", response.output_text)

        response = self.session.handle_input("/memory append remember the build steps")
        self.assertIn("Added memory", response.output_text)

        response = self.session.handle_input("/memory show")
        self.assertIn("remember the build steps", response.output_text)

        response = self.session.handle_input("/memory clear")
        self.assertIn("cleared", response.output_text.lower())
