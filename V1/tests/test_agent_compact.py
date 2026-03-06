import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from anuris.agent.compact import ContextCompactor
from anuris.agent.loop import AgentLoopRunner
from anuris.agent.tools import AgentToolExecutor


class FakeCompletions:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("No fake response left")
        return self._responses.pop(0)


class FakeModel:
    def __init__(self, responses):
        self.config = SimpleNamespace(model="fake-model", temperature=0.1)
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


def make_response(content: str):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ContextCompactorTests(unittest.TestCase):
    def test_micro_compact_replaces_older_tool_outputs(self):
        model = FakeModel([])
        with tempfile.TemporaryDirectory() as tmp_dir:
            compactor = ContextCompactor(
                model=model,
                transcript_dir=Path(tmp_dir) / ".anuris_transcripts",
                keep_recent_tool_messages=2,
                threshold_tokens=999999,
            )
            messages = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "ask"},
                {"role": "tool", "tool_call_id": "a", "content": "X" * 200},
                {"role": "tool", "tool_call_id": "b", "content": "Y" * 200},
                {"role": "tool", "tool_call_id": "c", "content": "Z" * 200},
                {"role": "tool", "tool_call_id": "d", "content": "W" * 200},
            ]

            compactor.micro_compact(messages)

            self.assertIn("omitted: a", messages[2]["content"])
            self.assertIn("omitted: b", messages[3]["content"])
            self.assertEqual(messages[4]["content"], "Z" * 200)
            self.assertEqual(messages[5]["content"], "W" * 200)

    def test_auto_compact_generates_summary_and_transcript(self):
        model = FakeModel([make_response("summary text")])
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript_dir = Path(tmp_dir) / ".anuris_transcripts"
            compactor = ContextCompactor(model=model, transcript_dir=transcript_dir)
            messages = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "request"},
                {"role": "assistant", "content": "response"},
            ]

            compacted = compactor.auto_compact(messages, focus="risks")

            self.assertEqual(compacted[0]["role"], "system")
            self.assertIn("Conversation compacted", compacted[1]["content"])
            self.assertIn("summary text", compacted[1]["content"])
            self.assertEqual(compacted[2]["role"], "assistant")
            self.assertTrue(any(transcript_dir.glob("transcript_*.jsonl")))
            self.assertIn("Focus: risks", model.client.chat.completions.calls[0]["messages"][1]["content"])

    def test_agent_loop_auto_compacts_when_threshold_exceeded(self):
        responses = [
            make_response("history summary"),
            make_response("final answer"),
        ]
        model = FakeModel(responses)
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runner = AgentLoopRunner(
                model=model,
                tool_executor=AgentToolExecutor(workspace_root=workspace),
                compaction_threshold_tokens=1,
                include_compaction=True,
                max_rounds=3,
            )
            result = runner.run(
                [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hello world"},
                ]
            )

            self.assertEqual(result.final_text, "final answer")
            self.assertEqual(len(model.client.chat.completions.calls), 2)
            second_call_messages = model.client.chat.completions.calls[1]["messages"]
            self.assertTrue(any("Conversation compacted" in str(message.get("content")) for message in second_call_messages))
            self.assertTrue(any(message.get("role") == "user" and "hello world" in str(message.get("content")) for message in second_call_messages))

    def test_auto_compact_preserves_active_request_suffix(self):
        model = FakeModel([make_response("history summary")])
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runner = AgentLoopRunner(
                model=model,
                tool_executor=AgentToolExecutor(workspace_root=workspace),
                compaction_threshold_tokens=1,
                include_compaction=True,
                max_rounds=3,
            )
            messages = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "older request"},
                {"role": "assistant", "content": "older reply"},
                {"role": "user", "content": "current request"},
                {"role": "assistant", "content": "tool planning"},
                {"role": "tool", "tool_call_id": "call_1", "content": "tool output"},
            ]

            compacted = runner._auto_compact_preserving_active_request(messages)

            self.assertEqual(compacted[-3]["content"], "current request")
            self.assertEqual(compacted[-2]["content"], "tool planning")
            self.assertEqual(compacted[-1]["content"], "tool output")
            self.assertTrue(any("Conversation compacted" in str(message.get("content")) for message in compacted))


if __name__ == "__main__":
    unittest.main()
