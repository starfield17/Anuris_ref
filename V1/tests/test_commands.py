import tempfile
import unittest
from pathlib import Path

from anuris.config import Config
from anuris.session import ChatSession


class FakeModel:
    def __init__(self):
        self.calls = []

    def create_completion(self, messages, stream, tools=None, tool_choice=None):
        self.calls.append(
            {
                "messages": messages,
                "stream": stream,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
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

