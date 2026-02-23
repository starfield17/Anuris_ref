import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from anuris.agent.loop import AgentLoopRunner
from anuris.agent.tools import AgentToolExecutor


class FakeCompletions:
    def __init__(self, responses):
        self._responses = responses
        self.calls = 0
        self.request_payloads = []

    def create(self, **kwargs):
        if self.calls >= len(self._responses):
            raise AssertionError("No fake response left")
        self.request_payloads.append(kwargs)
        response = self._responses[self.calls]
        self.calls += 1
        return response


class FakeModel:
    def __init__(self, responses):
        self.config = SimpleNamespace(model="fake-model", temperature=0.3)
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(responses)))


def make_response(content, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make_tool_call(tool_id, name, arguments):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=tool_id, function=function)


class AgentLoopRunnerTests(unittest.TestCase):
    def test_returns_direct_content_without_tools(self):
        model = FakeModel([make_response("final answer", tool_calls=None)])
        runner = AgentLoopRunner(model=model, tool_executor=AgentToolExecutor(), max_rounds=4)

        result = runner.run([
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ])

        self.assertEqual(result.final_text, "final answer")
        self.assertEqual(result.rounds, 1)
        self.assertEqual(result.tool_events, [])

    def test_executes_tool_calls_then_returns_final_text(self):
        tool_calls = [
            make_tool_call(
                "call_1",
                "write_file",
                '{"path":"out.txt","content":"hello"}',
            )
        ]
        responses = [
            make_response(content="", tool_calls=tool_calls),
            make_response(content="done", tool_calls=None),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            model = FakeModel(responses)
            runner = AgentLoopRunner(
                model=model,
                tool_executor=AgentToolExecutor(workspace_root=workspace),
                max_rounds=4,
            )

            result = runner.run([
                {"role": "system", "content": "system"},
                {"role": "user", "content": "create file"},
            ])

            self.assertEqual(result.final_text, "done")
            self.assertEqual(result.rounds, 2)
            self.assertEqual((workspace / "out.txt").read_text(), "hello")
            self.assertEqual(len(result.tool_events), 1)
            self.assertIn("write_file", result.tool_events[0])

    def test_includes_reasoning_content_in_assistant_messages_when_required(self):
        tool_calls = [make_tool_call("call_1", "read_file", '{"path":"missing.txt"}')]
        first_message = SimpleNamespace(content="", tool_calls=tool_calls, reasoning_content="thinking")
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(message=first_message)]),
            make_response(content="done", tool_calls=None),
        ]
        model = FakeModel(responses)
        runner = AgentLoopRunner(
            model=model,
            tool_executor=AgentToolExecutor(),
            max_rounds=4,
            require_reasoning_content=True,
        )

        runner.run(
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "previous assistant"},
                {"role": "user", "content": "go"},
            ]
        )

        # second request should contain assistant entries with explicit reasoning_content
        second_request_messages = model.client.chat.completions.request_payloads[1]["messages"]
        assistant_messages = [message for message in second_request_messages if message.get("role") == "assistant"]
        self.assertTrue(assistant_messages)
        self.assertTrue(all("reasoning_content" in message for message in assistant_messages))


if __name__ == "__main__":
    unittest.main()
