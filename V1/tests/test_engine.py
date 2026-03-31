import tempfile
import unittest
from pathlib import Path

from anuris.config import Config
from anuris.engine import PermissionContext, QueryEngine, SessionServices, SessionStore
from anuris.agent.skills import SkillLoader
from anuris.agent.tasks import PersistentTaskManager
from anuris.agent.todo import TodoManager
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


class QueryEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "sample.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        self.services = SessionServices(
            todo_manager=TodoManager(),
            task_manager=PersistentTaskManager(self.workspace / ".anuris" / "tasks"),
            skill_loader=SkillLoader(self.workspace),
        )
        self.tool_registry = ToolRegistry(build_default_tools())
        self.config = Config(api_key="k", model="fake-model", base_url="https://example.com/v1")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_query_engine_executes_tool_then_returns_answer(self):
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("read_file", '{"path":"sample.txt"}')]}}]},
                {"choices": [{"message": {"content": "The file contains alpha and beta."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "eng1")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        result = engine.submit("Inspect the sample file.")
        self.assertEqual(result.final_text, "The file contains alpha and beta.")
        self.assertTrue(any("read_file" in item for item in result.tool_events))
        self.assertTrue((self.workspace / ".anuris" / "sessions" / "eng1" / "transcript.md").exists())

    def test_query_engine_feeds_tool_errors_back_to_model(self):
        model = FakeModel(
            [
                {"choices": [{"message": {"content": "", "tool_calls": [tool_call("write_file", '{"path":"bad.txt","content":"x"}')]}}]},
                {"choices": [{"message": {"content": "Write was rejected, switching to analysis only."}}]},
            ]
        )
        store = SessionStore("system", self.workspace, "eng2")
        engine = QueryEngine(
            model=model,
            session_store=store,
            tool_registry=self.tool_registry,
            services=self.services,
            workspace_root=self.workspace,
            config=self.config,
        )

        result = engine.submit(
            "Attempt a write in readonly mode.",
            permission_context=PermissionContext(mode="readonly", allowed_tools={"read_file"}),
            allowed_tool_names={"read_file"},
        )
        self.assertIn("analysis only", result.final_text)
        self.assertTrue(any(message.role == "tool" and "Error:" in str(message.content) for message in store.messages))

    def test_session_store_compaction_creates_boundary(self):
        store = SessionStore("system", self.workspace, "eng3")
        for index in range(12):
            store.add_user_message(f"user {index}")
            store.add_assistant_message(f"assistant {index}")
        summary = store.compact_history("keep only the current direction", keep_last=4)
        self.assertIn("Conversation compacted", summary)
        self.assertEqual(store.messages[1].kind, "compact_boundary")

