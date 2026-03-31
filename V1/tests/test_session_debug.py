import tempfile
import unittest
from pathlib import Path

from anuris.config import Config
from anuris.debug_server import DebugSessionManager
from anuris.session import ChatSession


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def create_completion(self, messages, stream, tools=None, tool_choice=None):
        return self.responses.pop(0)


def tool_call(name, arguments, tool_id="call_1"):
    return {
        "id": tool_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


class SessionAndDebugTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "README.md").write_text("project readme", encoding="utf-8")
        self.config = Config(api_key="k", model="fake-model", base_url="https://example.com/v1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_headless_session_handles_tool_loop(self):
        session = ChatSession(
            self.config,
            workspace_root=self.workspace,
            model=FakeModel(
                [
                    {"choices": [{"message": {"content": "", "tool_calls": [tool_call("read_file", '{"path":"README.md"}')]}}]},
                    {"choices": [{"message": {"content": "Done reading the README."}}]},
                ]
            ),
            session_id="sess1",
        )
        response = session.handle_input("Read the README")
        self.assertEqual(response.final_text, "Done reading the README.")
        self.assertTrue(any("read_file" in item for item in response.tool_events))
        self.assertIn("Done reading the README.", response.output_text)

    def test_debug_session_manager_records_events_and_transcript(self):
        manager = DebugSessionManager(
            self.config,
            workspace_root=self.workspace,
            debug_dir=self.workspace / ".debug",
            model_factory=lambda config: FakeModel(
                [
                    {"choices": [{"message": {"content": "Headless reply."}}]},
                ]
            ),
        )
        created = manager.create_session({"session_id": "debug1"})
        self.assertEqual(created["session_id"], "debug1")

        result = manager.submit_message("debug1", {"message": "hello"})
        self.assertEqual(result["final_text"], "Headless reply.")

        transcript = manager.get_transcript("debug1")
        self.assertIn("Headless reply.", transcript["transcript"])
