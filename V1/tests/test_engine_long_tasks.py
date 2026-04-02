import json
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
        self.assertIn("Current task anchor:", str(continuation_message.content))
        self.assertIn("Original goal: Produce a long answer.", str(continuation_message.content))
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

    def test_query_engine_extends_turn_budget_for_long_cli_project(self):
        events = []
        responses = []
        project_dir = self.workspace / "cli_project"
        file_specs = [
            ("README.md", "# CLI Project\n"),
            ("pyproject.toml", "[project]\nname='cli-project'\n"),
            ("src/cli_project/__init__.py", ""),
            ("src/cli_project/main.py", "def main():\n    return 0\n"),
            ("src/cli_project/args.py", "def build_parser():\n    return None\n"),
            ("src/cli_project/commands.py", "COMMANDS = {}\n"),
            ("src/cli_project/io.py", "def write(msg):\n    return msg\n"),
            ("src/cli_project/config.py", "DEFAULTS = {}\n"),
            ("src/cli_project/version.py", "__version__ = '0.1.0'\n"),
            ("src/cli_project/__main__.py", "from .main import main\n"),
            ("tests/test_main.py", "def test_main():\n    assert True\n"),
            ("tests/test_args.py", "def test_args():\n    assert True\n"),
            ("tests/test_commands.py", "def test_commands():\n    assert True\n"),
            ("docs/usage.md", "Usage docs\n"),
            ("docs/design.md", "Design notes\n"),
            ("scripts/dev.sh", "#!/usr/bin/env bash\n"),
            (".gitignore", "__pycache__/\n"),
            ("src/cli_project/helptext.py", "HELP = 'help'\n"),
            ("src/cli_project/exit_codes.py", "OK = 0\n"),
            ("src/cli_project/state.py", "STATE = {}\n"),
            ("src/cli_project/runner.py", "def run():\n    return 0\n"),
            ("src/cli_project/formatter.py", "def fmt(v):\n    return str(v)\n"),
            ("tests/test_runner.py", "def test_runner():\n    assert True\n"),
            ("tests/test_formatter.py", "def test_formatter():\n    assert True\n"),
            ("Makefile", "test:\n\tpython -m pytest\n"),
        ]
        for index, (relative_path, content) in enumerate(file_specs, start=1):
            responses.append(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    tool_call(
                                        "write_file",
                                        json.dumps(
                                            {
                                                "path": str(project_dir / relative_path),
                                                "content": content,
                                            }
                                        ),
                                        f"write_{index}",
                                    )
                                ],
                            }
                        }
                    ]
                }
            )
        responses.append({"choices": [{"message": {"content": "CLI project completed successfully."}}]})
        model = FakeModel(responses)
        store = SessionStore("system", self.workspace, "cli_project_long")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
            event_callback=events.append,
            max_turns=24,
            turn_extension_step=12,
            max_turn_limit=48,
        )

        result = engine.submit("Create a small CLI project in a new workdir.")
        self.assertEqual(result.final_text, "CLI project completed successfully.")
        self.assertGreater(result.rounds, 24)
        self.assertTrue((project_dir / "src/cli_project/main.py").exists())
        self.assertTrue((project_dir / "tests/test_formatter.py").exists())
        extension_event = next(event for event in events if event.get("type") == "turn_budget_extended")
        self.assertEqual(extension_event.get("previous_limit"), 24)
        self.assertEqual(extension_event.get("new_limit"), 36)

    def test_query_engine_raises_explicit_turn_budget_exhaustion(self):
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("read_file", '{"path":"sample.txt"}', "step_1")]}}]},
                {"choices": [{"message": {"content": "unreachable"}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "budget_exhausted")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
            max_turns=1,
            turn_extension_step=1,
            max_turn_limit=1,
        )

        with self.assertRaisesRegex(RuntimeError, "Query turn budget exhausted"):
            engine.submit("Try one step beyond the hard maximum.")
        self.assertEqual(len(model.calls), 1)

    def test_query_engine_does_not_extend_for_low_value_repeat_reads(self):
        events = []
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("read_file", '{"path":"sample.txt"}', "read_1")]}}]},
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("read_file", '{"path":"sample.txt"}', "read_2")]}}]},
                {"choices": [{"message": {"content": "unreachable"}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "repeat_read_budget")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
            event_callback=events.append,
            max_turns=2,
            turn_extension_step=1,
            max_turn_limit=3,
        )

        with self.assertRaisesRegex(RuntimeError, "Query turn budget exhausted"):
            engine.submit("Keep re-reading the same file.")
        event_types = [event["type"] for event in events]
        self.assertIn("low_value_read_repetition", event_types)
        self.assertNotIn("turn_budget_extended", event_types)
