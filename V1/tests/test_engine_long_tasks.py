import tempfile
import unittest
from pathlib import Path

from anuris.agent.skills import SkillLoader
from anuris.agent.tasks import PersistentTaskManager
from anuris.agent.todo import TodoManager
from anuris.config import Config
from anuris.engine import PermissionContext, QueryEngine, SessionServices, SessionStore
from anuris.engine.messages import ToolCall
from anuris.services import (
    ContextFileTracker,
    HookManager,
    MCPManager,
    NotificationCenter,
    PermissionManager,
    PluginManager,
    RuntimeWatcher,
    SessionCatalog,
    SettingsManager,
    UsageTracker,
    WorktreeManager,
)
from anuris.tools import ToolRegistry, build_default_tools


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
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


def streamed_tool_chunk(*, index, tool_id="", name="", arguments="", finish_reason=""):
    tool_payload = {"index": index}
    if tool_id:
        tool_payload["id"] = tool_id
    function_payload = {}
    if name:
        function_payload["name"] = name
    if arguments:
        function_payload["arguments"] = arguments
    if function_payload:
        tool_payload["function"] = function_payload
    choice = {"delta": {"tool_calls": [tool_payload]}}
    if finish_reason:
        choice["finish_reason"] = finish_reason
    return {"choices": [choice]}


class QueryEngineLongTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "sample.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        task_manager = PersistentTaskManager(self.workspace / ".anuris" / "tasks")
        self.services = SessionServices(
            todo_manager=TodoManager(),
            task_manager=task_manager,
            skill_loader=SkillLoader(self.workspace),
            permission_manager=PermissionManager(),
            session_catalog=SessionCatalog(self.workspace),
            worktree_manager=WorktreeManager(self.workspace),
            plugin_manager=PluginManager(self.workspace),
            mcp_manager=MCPManager(self.workspace),
            settings_manager=SettingsManager(),
            hook_manager=HookManager(self.workspace),
            context_files=ContextFileTracker(self.workspace),
            usage_tracker=UsageTracker(),
            notification_center=NotificationCenter(),
            runtime_watcher=RuntimeWatcher(task_manager),
        )
        self.tool_registry = ToolRegistry(build_default_tools())
        self.config = Config(api_key="k", model="fake-model", base_url="https://example.com/v1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_streamed_tool_call_deltas_use_stable_index(self):
        model = FakeModel(
            [
                [
                    streamed_tool_chunk(index=0, tool_id="stream_1", name="read_file", arguments='{"path":"sam'),
                    streamed_tool_chunk(index=0, arguments='ple.txt"}', finish_reason="tool_calls"),
                ],
                {"choices": [{"message": {"content": "Streamed tool call completed."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "stream_ok")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        result = engine.submit("Read the sample file.")
        self.assertEqual(result.final_text, "Streamed tool call completed.")
        self.assertTrue(any("read_file" in item for item in result.tool_events))
        self.assertEqual([call["stream"] for call in model.calls], [True, True])

    def test_invalid_streamed_tool_call_falls_back_to_non_stream(self):
        events = []
        model = FakeModel(
            [
                [
                    streamed_tool_chunk(index=0, tool_id="stream_bad", arguments='{"path":"sample.txt"}', finish_reason="tool_calls"),
                ],
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("read_file", '{"path":"sample.txt"}', "fallback_1")]}}]},
                {"choices": [{"message": {"content": "Fallback recovered the response."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "stream_fallback")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
            event_callback=events.append,
        )

        result = engine.submit("Read the sample file.")
        self.assertEqual(result.final_text, "Fallback recovered the response.")
        self.assertEqual([call["stream"] for call in model.calls], [True, False, True])
        event_types = [event["type"] for event in events]
        self.assertIn("streaming_fallback_triggered", event_types)
        self.assertIn("provider_streaming_incompatible", event_types)

    def test_query_engine_continues_after_length_and_preserves_output(self):
        events = []
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "Alpha "}, "finish_reason": "length"}]},
                {"choices": [{"message": {"content": "Beta."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "continuation")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
            event_callback=events.append,
        )

        result = engine.submit("Produce a long answer.")
        self.assertEqual(result.final_text, "Alpha Beta.")
        self.assertEqual(result.rounds, 2)
        continuation_message = next(message for message in store.messages if message.metadata.get("continuation"))
        self.assertTrue(continuation_message.metadata.get("is_meta"))
        event_types = [event["type"] for event in events]
        self.assertIn("continuation_scheduled", event_types)

    def test_query_engine_raises_explicit_stall_before_turn_cap(self):
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("write_file", '{"path":"loop.txt","content":"x"}', "loop_1")]}}]},
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("write_file", '{"path":"loop.txt","content":"x"}', "loop_1")]}}]},
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("write_file", '{"path":"loop.txt","content":"x"}', "loop_1")]}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "stall_guard")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
            max_turns=8,
        )

        with self.assertRaisesRegex(RuntimeError, "Tool loop stalled"):
            engine.submit(
                "Keep trying the blocked write.",
                permission_context=PermissionContext(mode="readonly", allowed_tools={"write_file"}),
                allowed_tool_names={"write_file"},
            )
        self.assertEqual(len(model.calls), 3)

    def test_query_engine_raises_on_unpaired_tool_history(self):
        model = FakeModel([{"choices": [{"message": {"content": "unreachable"}}]}])
        store = SessionStore("system", self.workspace, "pairing_guard")
        store.add_assistant_message(
            "",
            tool_calls=[ToolCall(id="dangling", name="read_file", arguments_json='{"path":"sample.txt"}')],
        )
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        with self.assertRaisesRegex(RuntimeError, "Tool pairing mismatch detected"):
            engine.submit("Continue from the corrupted session.")
        self.assertEqual(model.calls, [])
