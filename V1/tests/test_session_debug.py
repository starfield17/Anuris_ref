import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from anuris.config import Config
from anuris.debug_server import DebugSessionManager
from anuris.session import ChatSession


class FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        if self.calls >= len(self._responses):
            raise AssertionError("No fake response left")
        response = self._responses[self.calls]
        self.calls += 1
        return response


class FakeModel:
    def __init__(self, responses):
        self.config = SimpleNamespace(model="fake-model", temperature=0.3)
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(responses)))

    def create_completion(self, messages, stream, tools=None, tool_choice=None):
        return self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=self.config.temperature,
            tools=tools,
            tool_choice=tool_choice,
            stream=stream,
        )


def make_response(content, tool_calls=None, reasoning_content=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=reasoning_content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class SessionAndDebugServerTests(unittest.TestCase):
    def test_headless_session_returns_agent_response_and_events(self):
        events = []
        model = FakeModel([make_response("done", tool_calls=None, reasoning_content="thinking")])
        config = Config(api_key="key", model="demo", base_url="https://api.example.com/v1")

        with tempfile.TemporaryDirectory() as tmp_dir:
            session = ChatSession(
                config,
                workspace_root=Path(tmp_dir),
                model=model,
                event_callback=events.append,
                session_id="session_test",
            )
            result = session.handle_input("hello")

        self.assertEqual(result.final_text, "done")
        self.assertEqual(result.reasoning_text, "thinking")
        self.assertEqual(session.history.messages[-1]["content"], "done")
        event_types = [event["type"] for event in events]
        self.assertIn("request_started", event_types)
        self.assertIn("user_message", event_types)
        self.assertIn("agent_round_started", event_types)
        self.assertIn("assistant_reasoning", event_types)
        self.assertIn("assistant_message", event_types)
        self.assertIn("request_finished", event_types)

    def test_headless_session_command_toggles_agent_mode(self):
        config = Config(api_key="key", model="demo", base_url="https://api.example.com/v1")
        session = ChatSession(config, model=FakeModel([make_response("unused")]), session_id="session_test")

        result = session.handle_input("/agent off")

        self.assertTrue(result.command_handled)
        self.assertFalse(session.agent_mode)
        self.assertIn("Agent mode disabled", result.output_text)

    def test_debug_session_manager_message_flow_persists_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            base_config = Config(api_key="key", model="demo", base_url="https://api.example.com/v1")
            manager = DebugSessionManager(
                base_config,
                workspace_root=workspace,
                debug_dir=workspace / ".anuris_debug",
                model_factory=lambda config: FakeModel([make_response("debug done", tool_calls=None)]),
            )
            created = manager.create_session({"session_name": "debug"})
            session_id = created["session_id"]

            reply = manager.submit_message(session_id, {"message": "hello from agent"}, request_kind="message")
            self.assertEqual(reply["final_text"], "debug done")

            events_payload = manager.get_events(session_id)
            event_types = [event["type"] for event in events_payload["events"]]
            self.assertIn("session_created", event_types)
            self.assertIn("request_started", event_types)
            self.assertIn("assistant_message", event_types)
            self.assertTrue(Path(reply["events_path"]).exists())

            transcript_payload = manager.get_transcript(session_id)
            self.assertIn("### User", transcript_payload["transcript"])
            self.assertIn("hello from agent", transcript_payload["transcript"])
            self.assertIn("debug done", transcript_payload["transcript"])

    def test_debug_session_manager_task_marks_request_kind(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            base_config = Config(api_key="key", model="demo", base_url="https://api.example.com/v1")
            manager = DebugSessionManager(
                base_config,
                workspace_root=workspace,
                debug_dir=workspace / ".anuris_debug",
                model_factory=lambda config: FakeModel([make_response("task done", tool_calls=None)]),
            )
            created = manager.create_session({})
            session_id = created["session_id"]
            reply = manager.submit_message(session_id, {"task": "inspect regression"}, request_kind="task")
            self.assertEqual(reply["request_kind"], "task")
            transcript_payload = manager.get_transcript(session_id)
            self.assertIn("Kind: `task`", transcript_payload["transcript"])


if __name__ == "__main__":
    unittest.main()
