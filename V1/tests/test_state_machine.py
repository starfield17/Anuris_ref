import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from anuris.config import Config
from anuris.state_machine import ChatState, ChatStateMachine


class FakeUI:
    def __init__(self, prompts):
        self.prompts = list(prompts)
        self.events = []
        self.messages = []
        self.live_starts = []
        self.live_finishes = []
        self.live_failures = []

    def display_welcome(self, model):
        self.messages.append(("welcome", model))

    def display_prompt(self):
        return self.prompts.pop(0)

    def display_message(self, content, style=None, end="\n", flush=False):
        self.messages.append((str(content), style, end, flush))

    def display_activity_event(self, label, detail="", tone="info"):
        self.messages.append((label, detail, tone))

    def display_attachments(self, attachments):
        self.messages.append(("attachments", list(attachments)))

    def begin_live_turn(self, prompt):
        self.live_starts.append(prompt)

    def handle_runtime_event(self, event):
        self.events.append(dict(event))

    def finish_live_turn(self, response=None):
        self.live_finishes.append(response)

    def fail_live_turn(self, error):
        self.live_failures.append(error)


class FakeSession:
    def __init__(self):
        self.attachment_manager = SimpleNamespace(attachments=[])
        self.stream_inputs = []
        self.sync_inputs = []

    def handle_input(self, user_input):
        self.sync_inputs.append(user_input)

    def handle_input_stream(self, user_input):
        self.stream_inputs.append(user_input)
        yield {"type": "request_started", "request_id": "req1"}
        yield {"type": "assistant_delta", "content": "Working"}
        yield {"type": "progress_update", "summary": "Running read_file", "stage": "tool_running", "status": "running"}
        yield {"type": "stream_completed", "response": {"final_text": "All done.", "reasoning_text": "", "round_count": 2}}


class ChatStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.config = Config(api_key="k", model="fake-model", base_url="https://example.com/v1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_non_command_input_uses_streaming_path(self):
        ui = FakeUI(["Build a demo", "quit"])
        machine = ChatStateMachine(self.config, ui, workspace_root=self.workspace)
        machine.session = FakeSession()

        with patch("anuris.state_machine.Prompt.ask", return_value="y"):
            machine.run()

        self.assertEqual(machine.current_state, ChatState.EXITING)
        self.assertEqual(machine.session.stream_inputs, ["Build a demo"])
        self.assertEqual(machine.session.sync_inputs, [])
        self.assertEqual(ui.live_starts, ["Build a demo"])
        self.assertEqual(ui.live_finishes, [{"final_text": "All done.", "reasoning_text": "", "round_count": 2}])
        self.assertFalse(ui.live_failures)
        self.assertEqual([event["type"] for event in ui.events], ["request_started", "assistant_delta", "progress_update", "stream_completed"])

    def test_command_input_stays_on_sync_path(self):
        ui = FakeUI(["/help", "quit"])
        machine = ChatStateMachine(self.config, ui, workspace_root=self.workspace)
        machine.session = FakeSession()

        with patch("anuris.state_machine.Prompt.ask", return_value="y"):
            machine.run()

        self.assertEqual(machine.session.sync_inputs, ["/help"])
        self.assertEqual(machine.session.stream_inputs, [])
        self.assertEqual(ui.live_starts, [])


if __name__ == "__main__":
    unittest.main()
